"""Dispatch-layer regressions (B3): envelopes, bounded retry, attempts.

All transports are synthetic (scripted results, no network, no real
credentials, no real sends); database work runs against the local test
engine in the caller's transaction and is rolled back unless the test
explicitly exercises the independent-session persistence mode.
"""

from __future__ import annotations

import unittest
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlmodel import Session, col, select

from app.core.db import engine
from app.modules.alerts.models.event import AlertEvent, NotificationAttempt
from app.modules.alerts.notifications.dispatch import (
    AlertNotificationDispatcher,
    build_envelope,
    build_transports,
    format_alert_time,
)
from app.modules.alerts.notifications.transport import (
    DeliveryOutcome,
    NotificationEnvelope,
    NotificationKind,
    NotificationResult,
    NotificationTransport,
)


def _event(
    *,
    source: str = "disp-cand-1",
    kind: str = "temperature_high",
    severity: str | None = "critical",
    device: str | None = "dev-01",
    metric: str | None = "temp_c",
    value: float | None = 9.6,
    unit: str | None = "°C",
    sampled_at: datetime | None = None,
) -> AlertEvent:
    return AlertEvent(
        source_event_id=source,
        kind=kind,
        severity=severity,
        device_id=device,
        metric_code=metric,
        value=value,
        unit=unit,
        sampled_at=sampled_at or datetime(2026, 10, 21, 8, 30, tzinfo=UTC),
        content_digest="0" * 64,
        payload_json="{}",
    )


def _result(
    kind: NotificationKind = NotificationKind.SMTP_EMAIL,
    outcome: DeliveryOutcome = DeliveryOutcome.ACCEPTED_BY_SERVICE,
    *,
    detail: str = "",
    retry_after_s: float | None = None,
) -> NotificationResult:
    return NotificationResult(
        attempt_id=uuid.uuid4(),
        transport=kind,
        outcome=outcome,
        delivered=(outcome is DeliveryOutcome.ACCEPTED_BY_SERVICE),
        detail=detail,
        retry_after_s=retry_after_s,
    )


class _ScriptedTransport(NotificationTransport):
    """Deterministic transport: pops scripted results in order."""

    def __init__(
        self, kind: NotificationKind, results: list[NotificationResult]
    ) -> None:
        self.kind = kind
        self._results = list(results)
        self.envelopes: list[NotificationEnvelope] = []

    def send(self, envelope: NotificationEnvelope) -> NotificationResult:
        self.envelopes.append(envelope)
        if not self._results:
            raise AssertionError("scripted transport exhausted results")
        return self._results.pop(0)


class _Sleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class _FailingAddSession:
    """Duck-typed Session whose ``add`` always raises (persistence guard)."""

    def __init__(self) -> None:
        self.rollback_calls = 0

    def exec(self, statement: Any) -> list[Any]:
        return []

    def add(self, obj: Any) -> None:
        raise RuntimeError("storage unavailable")

    def flush(self) -> None:
        return None

    def rollback(self) -> None:
        self.rollback_calls += 1


class _FailingReadSession:
    """Dedup state is unknown: no channel may be sent."""

    def exec(self, statement: Any) -> list[Any]:
        raise RuntimeError("secret webhook URL must not appear in result")


class _ExplodingTransport(NotificationTransport):
    def __init__(self, kind: NotificationKind, error: Exception) -> None:
        self.kind = kind
        self.error = error
        self.calls = 0

    def send(self, envelope: NotificationEnvelope) -> NotificationResult:
        self.calls += 1
        raise self.error


@contextmanager
def _independent_factory() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def _attempts(session: Session, alert_event_id: uuid.UUID) -> list[NotificationAttempt]:
    statement = (
        select(NotificationAttempt)
        .where(NotificationAttempt.alert_event_id == alert_event_id)
        .order_by(col(NotificationAttempt.created_at), col(NotificationAttempt.id))
    )
    return list(session.exec(statement))


class EnvelopeBuildingCase(unittest.TestCase):
    """build_envelope: labels, traceability, timezone, fallbacks."""

    def test_subject_body_and_traceability(self) -> None:
        event = _event()
        envelope = build_envelope(
            event, NotificationKind.SMTP_EMAIL, recipients=("ops@example.com",)
        )
        assert envelope.subject == "[FreshLink] 严重告警：温度超上限"
        assert envelope.kind is NotificationKind.SMTP_EMAIL
        assert envelope.source_event_id == event.id
        assert envelope.recipients == ("ops@example.com",)
        assert "设备: dev-01" in envelope.body_text
        assert "指标: temp_c = 9.6 °C" in envelope.body_text
        assert "来源事件: disp-cand-1" in envelope.body_text
        assert f"告警记录: {event.id}" in envelope.body_text
        assert "人工复核" in envelope.body_text

    def test_time_rendered_in_shanghai(self) -> None:
        event = _event(sampled_at=datetime(2026, 10, 21, 16, 0, tzinfo=UTC))
        envelope = build_envelope(event, NotificationKind.APP_PUSH)
        assert "2026-10-22 00:00:00（Asia/Shanghai）" in envelope.body_text
        assert (
            format_alert_time(datetime(2026, 10, 21, 0, 0, tzinfo=UTC))
            == "2026-10-21 08:00:00（Asia/Shanghai）"
        )

    def test_unknown_kind_and_missing_severity_fall_back(self) -> None:
        device_event = _event(source="disp-dev-1", kind="fault", severity=None)
        envelope = build_envelope(device_event, NotificationKind.APP_PUSH)
        assert envelope.subject == "[FreshLink] 告警：设备故障"

        mystery = _event(source="disp-x-1", kind="mystery_kind", severity="urgent")
        envelope = build_envelope(mystery, NotificationKind.APP_PUSH)
        assert envelope.subject == "[FreshLink] 告警：mystery_kind"

    def test_humidity_kind_label(self) -> None:
        event = _event(source="disp-h-1", kind="humidity_low", severity="warning")
        envelope = build_envelope(event, NotificationKind.APP_PUSH)
        assert envelope.subject == "[FreshLink] 警告告警：湿度低于下限"


class RetryCase(unittest.TestCase):
    """Bounded retry: only transient/timeout, exponential + Retry-After
    floor, max_delay cap, every attempt recorded."""

    def test_transient_then_accepted_retries_once(self) -> None:
        smtp = _ScriptedTransport(
            NotificationKind.SMTP_EMAIL,
            [
                _result(outcome=DeliveryOutcome.TRANSIENT_FAILURE, detail="conn reset"),
                _result(),
            ],
        )
        sleeper = _Sleep()
        dispatcher = AlertNotificationDispatcher(
            [smtp], base_delay_s=1.5, sleep=sleeper
        )
        with Session(engine) as session:
            event = _event(source="disp-retry-1")
            session.add(event)
            session.flush()
            report = dispatcher.dispatch(session, event)
            session.rollback()

        assert len(smtp.envelopes) == 2
        assert report.results[0].outcome is DeliveryOutcome.ACCEPTED_BY_SERVICE
        assert sleeper.calls == [1.5]

    def test_retry_after_floor_and_max_delay_cap(self) -> None:
        smtp = _ScriptedTransport(
            NotificationKind.SMTP_EMAIL,
            [
                _result(outcome=DeliveryOutcome.TRANSIENT_FAILURE, retry_after_s=5.0),
                _result(),
            ],
        )
        sleeper = _Sleep()
        dispatcher = AlertNotificationDispatcher(
            [smtp], base_delay_s=1.0, sleep=sleeper
        )
        with Session(engine) as session:
            event = _event(source="disp-retry-2")
            session.add(event)
            session.flush()
            dispatcher.dispatch(session, event)
            session.rollback()
        assert sleeper.calls == [5.0]

        capped = _ScriptedTransport(
            NotificationKind.SMTP_EMAIL,
            [
                _result(outcome=DeliveryOutcome.TIMEOUT, retry_after_s=999.0),
                _result(),
            ],
        )
        sleeper = _Sleep()
        dispatcher = AlertNotificationDispatcher(
            [capped], base_delay_s=1.0, max_delay_s=4.0, sleep=sleeper
        )
        with Session(engine) as session:
            event = _event(source="disp-retry-3")
            session.add(event)
            session.flush()
            dispatcher.dispatch(session, event)
            session.rollback()
        assert sleeper.calls == [4.0]

    def test_permanent_failure_never_retried(self) -> None:
        smtp = _ScriptedTransport(
            NotificationKind.SMTP_EMAIL,
            [_result(outcome=DeliveryOutcome.PERMANENT_FAILURE, detail="550 refused")],
        )
        dispatcher = AlertNotificationDispatcher([smtp], sleep=_Sleep())
        with Session(engine) as session:
            event = _event(source="disp-perm-1")
            session.add(event)
            session.flush()
            report = dispatcher.dispatch(session, event)
            session.rollback()
        assert len(smtp.envelopes) == 1
        assert len(report.results) == 1

    def test_timeout_retried_with_exponential_backoff(self) -> None:
        smtp = _ScriptedTransport(
            NotificationKind.SMTP_EMAIL,
            [
                _result(outcome=DeliveryOutcome.TIMEOUT),
                _result(outcome=DeliveryOutcome.TIMEOUT),
                _result(outcome=DeliveryOutcome.TIMEOUT),
            ],
        )
        sleeper = _Sleep()
        dispatcher = AlertNotificationDispatcher(
            [smtp], base_delay_s=1.0, sleep=sleeper
        )
        with Session(engine) as session:
            event = _event(source="disp-timeout-1")
            session.add(event)
            session.flush()
            report = dispatcher.dispatch(session, event)
            attempts = _attempts(session, event.id)
            session.rollback()

        assert len(smtp.envelopes) == 3
        assert sleeper.calls == [1.0, 2.0]
        assert report.results[0].outcome is DeliveryOutcome.TIMEOUT
        assert len(attempts) == 3


class AttemptPersistenceCase(unittest.TestCase):
    """Attempt rows: caller-transaction mode, independent-session mode,
    and the never-raise persistence guard."""

    def test_rows_written_with_full_result_fields(self) -> None:
        smtp = _ScriptedTransport(
            NotificationKind.SMTP_EMAIL,
            [_result(detail="queued as 123", retry_after_s=None)],
        )
        dispatcher = AlertNotificationDispatcher([smtp])
        with Session(engine) as session:
            event = _event(source="disp-persist-1")
            session.add(event)
            session.flush()
            dispatcher.dispatch(session, event)
            attempts = _attempts(session, event.id)
            assert len(attempts) == 1
            # Snapshot plain values BEFORE rollback: rollback expires
            # ORM objects and later attribute access would be a detach
            # error.
            row = attempts[0]
            snapshot = (row.channel, row.outcome, row.delivered, row.detail)
            session.rollback()

        assert snapshot == ("smtp_email", "accepted_by_service", True, "queued as 123")

    def test_independent_session_persists_despite_caller_rollback(self) -> None:
        smtp = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        dispatcher = AlertNotificationDispatcher(
            [smtp], attempt_session_factory=_independent_factory
        )
        with Session(engine) as session:
            # This test COMMITS; use a unique source id so reruns against
            # a persistent dev database never hit the unique constraint.
            event = _event(source=f"disp-indep-{uuid.uuid4()}")
            session.add(event)
            session.commit()
            event_id = event.id
            dispatcher.dispatch(session, event)
            # Everything the caller's session did after the commit is
            # rolled back; the attempt rows must survive anyway.
            marker = _event(source="disp-indep-marker")
            session.add(marker)
            session.flush()
            session.rollback()

        with Session(engine) as reader:
            rows = _attempts(reader, event_id)
            assert len(rows) == 1
            assert rows[0].channel == "smtp_email"
            marker_rows = reader.exec(
                select(AlertEvent).where(
                    AlertEvent.source_event_id == "disp-indep-marker"
                )
            ).all()
            assert marker_rows == []

    def test_persistence_failure_annotated_never_raised(self) -> None:
        smtp = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result(detail="ok")])
        dispatcher = AlertNotificationDispatcher([smtp])
        stub = _FailingAddSession()
        report = dispatcher.dispatch(cast(Session, stub), _event(source="disp-guard-1"))
        assert stub.rollback_calls == 1
        assert report.results[0].detail == "ok [attempt record NOT persisted]"

        smtp2 = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result(detail="ok")])
        dispatcher = AlertNotificationDispatcher(
            [smtp2], attempt_session_factory=_raise_factory
        )
        with Session(engine) as session:
            event = _event(source="disp-guard-2")
            session.add(event)
            session.flush()
            report = dispatcher.dispatch(session, event)
            session.rollback()
        assert report.results[0].detail == "ok [attempt record NOT persisted]"


def _raise_factory() -> Any:
    raise RuntimeError("factory broken")


class DedupCase(unittest.TestCase):
    """Already-delivered (event, channel) pairs are skipped."""

    def test_delivered_attempt_skips_channel(self) -> None:
        smtp = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        app_push = _ScriptedTransport(
            NotificationKind.APP_PUSH, [_result(kind=NotificationKind.APP_PUSH)]
        )
        dispatcher = AlertNotificationDispatcher([smtp, app_push])
        with Session(engine) as session:
            event = _event(source="disp-dedup-1")
            session.add(event)
            session.flush()
            session.add(
                NotificationAttempt(
                    alert_event_id=event.id,
                    channel="smtp_email",
                    outcome="accepted_by_service",
                    delivered=True,
                )
            )
            report = dispatcher.dispatch(session, event)
            session.rollback()

        assert smtp.envelopes == []
        assert len(app_push.envelopes) == 1
        assert report.skipped_channels == (NotificationKind.SMTP_EMAIL,)
        assert len(report.results) == 1

    def test_undelivered_attempt_does_not_skip(self) -> None:
        smtp = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        dispatcher = AlertNotificationDispatcher([smtp])
        with Session(engine) as session:
            event = _event(source="disp-dedup-2")
            session.add(event)
            session.flush()
            session.add(
                NotificationAttempt(
                    alert_event_id=event.id,
                    channel="smtp_email",
                    outcome="transient_failure",
                    delivered=False,
                )
            )
            report = dispatcher.dispatch(session, event)
            session.rollback()
        assert len(smtp.envelopes) == 1
        assert report.skipped_channels == ()

    def test_dedup_read_failure_blocks_all_channels_without_leaking_error(self) -> None:
        smtp = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        app_push = _ScriptedTransport(
            NotificationKind.APP_PUSH, [_result(kind=NotificationKind.APP_PUSH)]
        )
        dispatcher = AlertNotificationDispatcher([smtp, app_push])
        report = dispatcher.dispatch(
            cast(Session, _FailingReadSession()), _event(source="disp-read-fail")
        )
        assert smtp.envelopes == []
        assert app_push.envelopes == []
        assert report.skipped_channels == ()
        assert [result.detail for result in report.results] == [
            "dedup_store_unavailable",
            "dedup_store_unavailable",
        ]
        assert all(
            result.outcome is DeliveryOutcome.TRANSIENT_FAILURE and not result.delivered
            for result in report.results
        )

    def test_timeout_in_first_channel_does_not_skip_second_channel(self) -> None:
        smtp = _ExplodingTransport(
            NotificationKind.SMTP_EMAIL,
            TimeoutError("secret smtp reply must not appear in result"),
        )
        app_push = _ScriptedTransport(
            NotificationKind.APP_PUSH, [_result(kind=NotificationKind.APP_PUSH)]
        )
        dispatcher = AlertNotificationDispatcher([smtp, app_push], max_attempts=1)
        with Session(engine) as session:
            event = _event(source="disp-timeout-channel")
            session.add(event)
            session.flush()
            report = dispatcher.dispatch(session, event)
            attempts = _attempts(session, event.id)
            session.rollback()

        assert smtp.calls == 1
        assert len(app_push.envelopes) == 1
        assert [result.outcome for result in report.results] == [
            DeliveryOutcome.TIMEOUT,
            DeliveryOutcome.ACCEPTED_BY_SERVICE,
        ]
        assert report.results[0].detail == "transport_timeout"
        assert [row.detail for row in attempts] == [
            "transport_timeout",
            None,
        ]

    def test_transport_runtime_failure_is_redacted_and_other_channel_runs(self) -> None:
        smtp = _ExplodingTransport(
            NotificationKind.SMTP_EMAIL,
            RuntimeError("private recipient and token must not appear in result"),
        )
        app_push = _ScriptedTransport(
            NotificationKind.APP_PUSH, [_result(kind=NotificationKind.APP_PUSH)]
        )
        dispatcher = AlertNotificationDispatcher([smtp, app_push], max_attempts=1)
        with Session(engine) as session:
            event = _event(source="disp-runtime-channel")
            session.add(event)
            session.flush()
            report = dispatcher.dispatch(session, event)
            session.rollback()
        assert smtp.calls == 1
        assert len(app_push.envelopes) == 1
        assert report.results[0].detail == "transport_unavailable"
        assert report.results[0].outcome is DeliveryOutcome.TRANSIENT_FAILURE


class ConstructionCase(unittest.TestCase):
    def test_duplicate_kinds_rejected(self) -> None:
        first = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        second = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        with pytest.raises(ValueError, match="duplicate transport kind"):
            AlertNotificationDispatcher([first, second])

    def test_invalid_bounds_rejected(self) -> None:
        smtp = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        with pytest.raises(ValueError, match="max_attempts"):
            AlertNotificationDispatcher([smtp], max_attempts=0)
        with pytest.raises(ValueError, match="non-negative"):
            AlertNotificationDispatcher([smtp], base_delay_s=-1.0)


class FactoryCase(unittest.TestCase):
    """build_transports: only configured channels; a broken optional
    channel never blocks the other one."""

    def test_nothing_configured(self) -> None:
        settings = SimpleNamespace(SMTP_HOST="", EMAILS_FROM_EMAIL="")
        assert build_transports(settings=settings) == []

    def test_smtp_configured(self) -> None:
        settings = SimpleNamespace(
            SMTP_HOST="smtp.example.com",
            SMTP_PORT=587,
            SMTP_TLS=True,
            SMTP_SSL=None,
            SMTP_TIMEOUT=5,
            SMTP_USER="",
            SMTP_PASSWORD="",
            EMAILS_FROM_EMAIL="noreply@example.com",
        )
        transports = build_transports(settings=settings)
        assert [t.kind for t in transports] == [NotificationKind.SMTP_EMAIL]

    def test_broken_smtp_does_not_block_feishu(self) -> None:
        settings = SimpleNamespace(
            SMTP_HOST="smtp.example.com",
            SMTP_PORT=0,  # invalid → SMTPTransport construction fails
            SMTP_TLS=False,
            SMTP_SSL=None,
            SMTP_TIMEOUT=5,
            SMTP_USER="",
            SMTP_PASSWORD="",
            EMAILS_FROM_EMAIL="noreply@example.com",
        )
        env = {
            "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/aaa-111"
        }
        transports = build_transports(settings=settings, env=env)
        assert [t.kind for t in transports] == [NotificationKind.APP_PUSH]

    def test_invalid_feishu_url_skipped(self) -> None:
        settings = SimpleNamespace(SMTP_HOST="", EMAILS_FROM_EMAIL="")
        env = {"FEISHU_WEBHOOK_URL": "http://open.feishu.cn/open-apis/bot/v2/hook/aaa"}
        assert build_transports(settings=settings, env=env) == []

    def test_smtp_without_from_email_skipped(self) -> None:
        settings = SimpleNamespace(
            SMTP_HOST="smtp.example.com",
            SMTP_PORT=587,
            SMTP_TLS=True,
            SMTP_SSL=None,
            SMTP_TIMEOUT=5,
            SMTP_USER="",
            SMTP_PASSWORD="",
            EMAILS_FROM_EMAIL="",
        )
        assert build_transports(settings=settings) == []
