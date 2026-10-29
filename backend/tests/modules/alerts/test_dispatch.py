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
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, col, select

from app.core.db import engine
from app.modules.alerts.models.event import AlertEvent, NotificationAttempt
from app.modules.alerts.notifications.dispatch import (
    SEND_INTENT_OUTCOME,
    AlertNotificationDispatcher,
    build_envelope,
    build_transports,
    dispatch_committed_event,
    format_alert_time,
)
from app.modules.alerts.notifications.transport import (
    DeliveryOutcome,
    NotificationEnvelope,
    NotificationKind,
    NotificationResult,
    NotificationTransport,
)
from tests.modules.alerts.test_smtp import (
    MockSMTP,
    SMTPTransport,
    _config,
    _factory,
    _State,
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
    """Duck-typed Session whose ``add`` always raises (persistence guard).

    Records whether the dispatcher rolled back the WHOLE session
    (forbidden: must not undo the caller's business work) versus only
    the SAVEPOINT (required)."""

    def __init__(self) -> None:
        self.rollback_calls = 0
        self.savepoint_rolls = 0

    def exec(self, statement: Any) -> list[Any]:
        return []

    def begin_nested(self) -> Any:
        outer = self

        class _Savepoint:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                if exc_type is not None:
                    outer.savepoint_rolls += 1

        return _Savepoint()

    def add(self, obj: Any) -> None:
        raise RuntimeError("storage unavailable")

    def flush(self) -> None:
        return None

    def rollback(self) -> None:
        self.rollback_calls += 1


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

    def test_intent_persistence_failure_means_zero_sends(self) -> None:
        # A review 5682106843: the pre-send intent IS the guard. If the
        # intent cannot be persisted, ZERO provider calls happen — the
        # send never earns the right to leave the wire.
        smtp = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result(detail="ok")])
        dispatcher = AlertNotificationDispatcher([smtp])
        stub = _FailingAddSession()
        report = dispatcher.dispatch(cast(Session, stub), _event(source="disp-guard-1"))
        # The bounded budget retries the GUARD write (never the wire):
        # 3 attempts -> 3 failed savepoints, zero provider calls.
        assert stub.savepoint_rolls == 3
        assert stub.rollback_calls == 0  # caller's transaction untouched
        assert smtp.envelopes == []  # ZERO provider calls across retries
        assert report.results[0].detail == "dispatch_intent_not_persisted"

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
        assert smtp2.envelopes == []  # ZERO provider calls
        assert report.results[0].detail == "dispatch_intent_not_persisted"


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


class _FailingExecSession:
    """Duck-typed Session whose ``exec`` always raises (dedup failure)."""

    def exec(self, statement: Any) -> Any:
        raise RuntimeError("dedup query failed")

    def add(self, obj: Any) -> None:
        return None

    def flush(self) -> None:
        return None

    def rollback(self) -> None:
        return None


class FailClosedCase(unittest.TestCase):
    """Dedup query failure must fail closed: zero sends, no raise."""

    def test_dedup_failure_sends_nothing(self) -> None:
        smtp = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        app_push = _ScriptedTransport(
            NotificationKind.APP_PUSH, [_result(kind=NotificationKind.APP_PUSH)]
        )
        dispatcher = AlertNotificationDispatcher([smtp, app_push])
        report = dispatcher.dispatch(
            cast(Session, _FailingExecSession()), _event(source="disp-fc-1")
        )
        assert report.dedup_error is True
        # Every configured channel reports a fixed redacted transient
        # failure; nothing was sent and nothing recorded as an attempt.
        assert [r.detail for r in report.results] == [
            "dedup_store_unavailable",
            "dedup_store_unavailable",
        ]
        assert all(
            r.outcome is DeliveryOutcome.TRANSIENT_FAILURE for r in report.results
        )
        assert report.skipped_channels == ()
        assert smtp.envelopes == []
        assert app_push.envelopes == []

    def test_dedup_success_reports_no_error(self) -> None:
        smtp = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        dispatcher = AlertNotificationDispatcher([smtp])
        with Session(engine) as session:
            event = _event(source=f"disp-fc-ok-{uuid.uuid4()}")
            session.add(event)
            session.flush()
            report = dispatcher.dispatch(session, event)
            session.rollback()
        assert report.dedup_error is False
        assert len(report.results) == 1


class ChannelIsolationCase(unittest.TestCase):
    """A transport leaking past the never-raise contract affects only
    its own channel; the remaining channels are still attempted."""

    def test_raising_first_channel_does_not_block_second(self) -> None:
        class _RaisingTransport(NotificationTransport):
            kind = NotificationKind.SMTP_EMAIL

            def send(self, envelope: NotificationEnvelope) -> NotificationResult:
                raise RuntimeError("first channel exploded")

        second = _ScriptedTransport(
            NotificationKind.APP_PUSH, [_result(kind=NotificationKind.APP_PUSH)]
        )
        dispatcher = AlertNotificationDispatcher([_RaisingTransport(), second])
        with Session(engine) as session:
            event = _event(source=f"disp-iso-1-{uuid.uuid4()}")
            session.add(event)
            session.flush()
            report = dispatcher.dispatch(session, event)
            attempts = _attempts(session, event.id)
            session.rollback()

        assert len(second.envelopes) == 1
        assert len(report.results) == 2
        first = report.results[0]
        assert first.outcome is DeliveryOutcome.PERMANENT_FAILURE
        assert "RuntimeError" in first.detail
        assert "first channel exploded" not in first.detail
        assert report.results[1].outcome is DeliveryOutcome.ACCEPTED_BY_SERVICE
        assert len(attempts) == 2

    def test_timeout_escape_classified_and_isolated(self) -> None:
        class _TimeoutTransport(NotificationTransport):
            kind = NotificationKind.SMTP_EMAIL

            def send(self, envelope: NotificationEnvelope) -> NotificationResult:
                raise TimeoutError("read timed out")

        second = _ScriptedTransport(
            NotificationKind.APP_PUSH, [_result(kind=NotificationKind.APP_PUSH)]
        )
        dispatcher = AlertNotificationDispatcher(
            [_TimeoutTransport(), second], sleep=_Sleep()
        )
        with Session(engine) as session:
            event = _event(source=f"disp-iso-2-{uuid.uuid4()}")
            session.add(event)
            session.flush()
            report = dispatcher.dispatch(session, event)
            session.rollback()

        assert report.results[0].outcome is DeliveryOutcome.TIMEOUT
        assert report.results[0].detail == "transport_timeout"
        assert len(second.envelopes) == 1

    def test_transient_leak_is_retried_within_retry_budget(self) -> None:
        # A connection-class exception leaking from send() is a
        # transient outcome: the retry loop keeps the channel's budget
        # instead of abandoning it.
        class _FlakyTransport(NotificationTransport):
            kind = NotificationKind.SMTP_EMAIL

            def __init__(self) -> None:
                self.calls = 0

            def send(self, envelope: NotificationEnvelope) -> NotificationResult:
                self.calls += 1
                if self.calls == 1:
                    raise OSError("connection reset mid-send")
                return _result()

        flaky = _FlakyTransport()
        dispatcher = AlertNotificationDispatcher([flaky], sleep=_Sleep())
        with Session(engine) as session:
            event = _event(source=f"disp-iso-3-{uuid.uuid4()}")
            session.add(event)
            session.flush()
            report = dispatcher.dispatch(session, event)
            session.rollback()

        assert flaky.calls == 2
        assert report.results[0].outcome is DeliveryOutcome.ACCEPTED_BY_SERVICE


class SavepointCase(unittest.TestCase):
    """Attempt-record failure must not poison the caller's transaction:
    the insert runs in a SAVEPOINT, business work still commits."""

    def test_intent_failure_leaves_caller_transaction_committable(self) -> None:
        scripted = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        dispatcher = AlertNotificationDispatcher([scripted])
        with Session(engine) as session:
            business = _event(source=f"disp-svpt-biz-{uuid.uuid4()}")
            session.add(business)
            session.flush()
            business_id = business.id
            business_source = business.source_event_id
            # The phantom event is never persisted: the pre-send intent
            # row's FK insert fails, which must roll back ONLY the
            # savepoint — and means ZERO provider calls.
            phantom = _event(source="disp-svpt-phantom-unused")
            report = dispatcher.dispatch(session, phantom)
            session.commit()

        assert report.results[0].detail == "dispatch_intent_not_persisted"
        assert scripted.envelopes == []
        with Session(engine) as reader:
            saved = reader.exec(
                select(AlertEvent).where(AlertEvent.id == business_id)
            ).one()
            assert saved.source_event_id == business_source
            assert _attempts(reader, business_id) == []


class PostCommitEntryCase(unittest.TestCase):
    """dispatch_committed_event: loads a COMMITTED event, sends with
    independent attempt transactions, never on unknown ids."""

    def test_committed_event_dispatched(self) -> None:
        with Session(engine) as session:
            event = _event(source=f"disp-post-{uuid.uuid4()}")
            session.add(event)
            session.commit()
            event_id = event.id
        scripted = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        sleeper = _Sleep()
        report = dispatch_committed_event(
            _independent_factory,
            event_id,
            transports=[scripted],
            sleep=sleeper,
        )
        assert report.dedup_error is False
        assert len(report.results) == 1
        assert report.results[0].outcome is DeliveryOutcome.ACCEPTED_BY_SERVICE
        with Session(engine) as reader:
            assert len(_attempts(reader, event_id)) == 1

    def test_unknown_event_is_noop(self) -> None:
        scripted = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        report = dispatch_committed_event(
            _independent_factory,
            uuid.uuid4(),
            transports=[scripted],
        )
        assert report.results == ()
        assert scripted.envelopes == []


class AcceptanceUnknownHoldCase(unittest.TestCase):
    """C review 5678438821 defect 1: a send whose acceptance cannot be
    proven (connection broke mid-DATA) must never be auto-retried —
    neither within one dispatch nor across later runs — and the hold
    must be persisted, not carried in free text."""

    @staticmethod
    def _unknown_result() -> NotificationResult:
        return NotificationResult(
            attempt_id=uuid.uuid4(),
            transport=NotificationKind.SMTP_EMAIL,
            outcome=DeliveryOutcome.TRANSIENT_FAILURE,
            delivered=False,
            acceptance_unknown=True,
            detail="connection error stage=data data_acceptance=unknown",
        )

    def test_unknown_not_retried_within_dispatch(self) -> None:
        # The result classifies transient (retryable outcome), but the
        # unknown-acceptance flag must stop the retry loop after the
        # FIRST attempt: max_attempts=3, exactly one send.
        scripted = _ScriptedTransport(
            NotificationKind.SMTP_EMAIL,
            [self._unknown_result(), _result(), _result()],
        )
        sleeper = _Sleep()
        dispatcher = AlertNotificationDispatcher(
            [scripted], max_attempts=3, sleep=sleeper
        )
        with Session(engine) as session:
            event = _event(source=f"disp-unknown-1-{uuid.uuid4()}")
            session.add(event)
            session.flush()
            report = dispatcher.dispatch(session, event)
            session.rollback()
        assert len(scripted.envelopes) == 1
        assert sleeper.calls == []
        assert report.results[0].delivered is False

    def test_unknown_attempt_persisted_with_sentinel_outcome(self) -> None:
        scripted = _ScriptedTransport(
            NotificationKind.SMTP_EMAIL, [self._unknown_result()]
        )
        dispatcher = AlertNotificationDispatcher([scripted], max_attempts=3)
        with Session(engine) as session:
            event = _event(source=f"disp-unknown-2-{uuid.uuid4()}")
            session.add(event)
            session.flush()
            dispatcher.dispatch(session, event)
            rows = _attempts(session, event.id)
            outcomes = [(r.outcome, r.delivered) for r in rows]
            session.rollback()
        assert outcomes == [("acceptance_unknown", False)]

    def test_unknown_attempt_settles_channel_across_runs(self) -> None:
        # A later dispatch (fresh dispatcher, fresh transport — the
        # restart shape) must SKIP the channel: an unproven acceptance
        # is never answered with an automatic re-send.
        scripted = _ScriptedTransport(
            NotificationKind.SMTP_EMAIL, [self._unknown_result()]
        )
        dispatcher = AlertNotificationDispatcher([scripted], max_attempts=3)
        with Session(engine) as session:
            event = _event(source=f"disp-unknown-3-{uuid.uuid4()}")
            session.add(event)
            session.commit()
            dispatcher.dispatch(session, event)
            session.commit()  # persist the unknown attempt, not just flush
            event_id = event.id
        later = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        later_dispatcher = AlertNotificationDispatcher([later], max_attempts=3)
        with Session(engine) as session:
            stored = session.get(AlertEvent, event_id)
            assert stored is not None
            report = later_dispatcher.dispatch(session, stored)
            session.rollback()
        assert later.envelopes == []
        assert report.skipped_channels == (NotificationKind.SMTP_EMAIL,)

    def test_unknown_hold_via_real_smtp_adapter_and_post_commit_entry(self) -> None:
        # Combined regression with the REAL SMTP transport + MockSMTP
        # (C review 5678438821): mid-DATA ConnectionResetError → one
        # attempt, sentinel outcome persisted, and a second
        # dispatch_committed_event run (restart shape) sends nothing.
        MockSMTP.state = _State(send_raises=ConnectionResetError("eof mid-data"))
        first = SMTPTransport(_config(), _factory())
        with Session(engine) as session:
            event = _event(source=f"disp-unknown-4-{uuid.uuid4()}")
            session.add(event)
            session.commit()
            event_id = event.id
        report = dispatch_committed_event(
            _independent_factory,
            event_id,
            transports=[first],
            recipients=("a@example.com",),
        )
        assert len(MockSMTP.sent_messages) == 1
        assert report.results[0].acceptance_unknown is True
        with Session(engine) as reader:
            rows = _attempts(reader, event_id)
        assert len(rows) == 1
        assert rows[0].outcome == "acceptance_unknown"
        # Restart: fresh adapter, fresh run — zero further sends.
        MockSMTP.state = _State()
        second = SMTPTransport(_config(), _factory())
        dispatch_committed_event(
            _independent_factory,
            event_id,
            transports=[second],
            recipients=("a@example.com",),
        )
        assert len(MockSMTP.sent_messages) == 1

    def test_pre_send_failure_still_retries_within_budget(self) -> None:
        # Guard against over-suppression: a plain pre-DATA transient
        # (acceptance KNOWN not to have happened) keeps the bounded
        # retry budget.
        scripted = _ScriptedTransport(
            NotificationKind.SMTP_EMAIL,
            [
                _result(
                    outcome=DeliveryOutcome.TRANSIENT_FAILURE,
                    detail="connection error stage=connect",
                    retry_after_s=0.0,
                ),
                _result(),
            ],
        )
        sleeper = _Sleep()
        dispatcher = AlertNotificationDispatcher(
            [scripted], max_attempts=3, sleep=sleeper
        )
        with Session(engine) as session:
            event = _event(source=f"disp-unknown-5-{uuid.uuid4()}")
            session.add(event)
            session.flush()
            dispatcher.dispatch(session, event)
            session.rollback()
        assert len(scripted.envelopes) == 2
        assert len(sleeper.calls) == 1


class _FailOnNthFactory:
    """Independent-session factory: the Nth session open fails with
    OperationalError (A review 5682106843's fault-injection shape:
    audit-store outage, real business DB untouched)."""

    def __init__(self, fail_on_call: int) -> None:
        self._calls = 0
        self._fail_on = fail_on_call
        self.calls_made = 0

    @contextmanager
    def __call__(self) -> Iterator[Session]:
        self._calls += 1
        self.calls_made = self._calls
        if self._calls == self._fail_on:
            raise OperationalError(
                "injected audit-store outage", None, Exception("pg gone")
            )
        with Session(engine) as session:
            yield session


class IntentGuardCase(unittest.TestCase):
    """A review 5682106843 defect 1: the pre-send dispatch intent must
    be durable BEFORE any network call (write failure = zero sends),
    and an unfinished intent after the send must hold the channel
    across restarts instead of allowing an automatic resend."""

    def test_intent_row_precedes_provider_call(self) -> None:
        # Ordering proof: the send_intent row is committed before the
        # transport is ever invoked.
        events: list[str] = []

        class _SpyTransport(_ScriptedTransport):
            def send(self, envelope: NotificationEnvelope) -> NotificationResult:
                events.append("send")
                return super().send(envelope)

        spy = _SpyTransport(NotificationKind.SMTP_EMAIL, [_result()])

        @contextmanager
        def _spy_factory() -> Iterator[Session]:
            with Session(engine) as session:
                yield session
            events.append("intent_committed")  # commit done on clean exit

        dispatcher = AlertNotificationDispatcher(
            [spy], attempt_session_factory=_spy_factory
        )
        with Session(engine) as session:
            event = _event(source=f"disp-intent-order-{uuid.uuid4()}")
            session.add(event)
            session.commit()
            dispatcher.dispatch(session, event)
        # Attempt 1: intent committed BEFORE send; attempt completion
        # opens its own (committed) session after the send. Exactly two
        # committed intent sessions for exactly one send.
        assert events == ["intent_committed", "send", "intent_committed"]

    def test_intent_write_failure_zero_provider_calls_real_pg(self) -> None:
        # Requirement 1 (wrap-up checklist): persistent pre-send guard
        # write fails -> provider calls == 0. The audit store is down
        # (Nth session open raises OperationalError); the business DB
        # stays real and is not poisoned by the dispatch.
        class _AlwaysDownFactory:
            """Audit store down for the WHOLE dispatch: every session
            open raises OperationalError (no recovery mid-run)."""

            def __init__(self) -> None:
                self.calls_made = 0

            @contextmanager
            def __call__(self) -> Iterator[Session]:
                self.calls_made += 1
                raise OperationalError(
                    "injected audit-store outage", None, Exception("pg gone")
                )
                yield None  # pragma: no cover

        broken = _AlwaysDownFactory()
        scripted = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        with Session(engine) as session:
            event = _event(source=f"disp-intent-fail-{uuid.uuid4()}")
            session.add(event)
            session.commit()
            event_id = event.id
        dispatcher = AlertNotificationDispatcher(
            [scripted], max_attempts=3, sleep=_Sleep(), attempt_session_factory=broken
        )
        report = dispatcher.dispatch(session, event)
        # Every budget attempt is stopped by the guard: the wire is
        # never reached, with the audit store down the whole time.
        assert scripted.envelopes == []  # ZERO provider calls
        assert broken.calls_made == 3  # guard retried, wire never reached
        with Session(engine) as reader:
            assert _attempts(reader, event_id) == []  # nothing recorded
        assert report.results[0].detail == "dispatch_intent_not_persisted"
        assert report.results[0].outcome is DeliveryOutcome.TRANSIENT_FAILURE
        with Session(engine) as reader:
            assert _attempts(reader, event_id) == []  # nothing recorded

    def test_retryable_with_completion_write_failure_stops_same_dispatch(
        self,
    ) -> None:
        # A review 5686551361 / C review 5686742366 requirements 1+2:
        # a retryable transport result (definite transient refusal or
        # timeout) whose intent COMPLETION write fails must stop the
        # channel within the SAME dispatch — while an unfinished
        # send_intent row holds the channel, the retry loop may not
        # touch the provider again, no matter how retryable the result
        # looks (the stop is structural, not detail-string based).
        for outcome in (DeliveryOutcome.TRANSIENT_FAILURE, DeliveryOutcome.TIMEOUT):
            with self.subTest(outcome=outcome):
                scripted = _ScriptedTransport(
                    NotificationKind.SMTP_EMAIL,
                    [
                        _result(
                            outcome=outcome,
                            detail="450 4.2.0 mailbox busy"
                            if outcome is DeliveryOutcome.TRANSIENT_FAILURE
                            else "gateway timeout",
                        )
                    ],
                )
                with Session(engine) as session:
                    event = _event(source=f"disp-cf-stop-{uuid.uuid4()}")
                    session.add(event)
                    session.commit()
                    event_id = event.id
                completion = _FailOnNthFactory(
                    fail_on_call=2
                )  # intent ok, complete fails
                dispatcher = AlertNotificationDispatcher(
                    [scripted],
                    max_attempts=3,
                    sleep=_Sleep(),
                    attempt_session_factory=completion,
                )
                with Session(engine) as session:
                    stored = session.get(AlertEvent, event_id)
                    assert stored is not None
                    report = dispatcher.dispatch(
                        session, stored, recipients=("a@example.com",)
                    )
                # EXACTLY one provider call this dispatch: the budget
                # retry loop is cut off by the failed completion write.
                assert len(scripted.envelopes) == 1
                assert report.results[0].outcome is outcome
                with Session(engine) as reader:
                    snapshot = [
                        (r.outcome, r.delivered) for r in _attempts(reader, event_id)
                    ]
                # The unfinished intent row stays as the cross-process hold.
                assert snapshot == [(SEND_INTENT_OUTCOME, False)]

                # Restart shape (requirement 3): a healthy new dispatcher
                # must NOT resend — the hold row keeps the channel paused,
                # adding ZERO new provider calls.
                healthy = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
                later = AlertNotificationDispatcher(
                    [healthy], attempt_session_factory=_independent_factory
                )
                with Session(engine) as session:
                    stored = session.get(AlertEvent, event_id)
                    assert stored is not None
                    later_report = later.dispatch(
                        session, stored, recipients=("a@example.com",)
                    )
                assert len(healthy.envelopes) == 0  # zero new provider calls
                assert later_report.skipped_channels == (NotificationKind.SMTP_EMAIL,)
                with Session(engine) as reader:
                    assert len(_attempts(reader, event_id)) == 1  # hold row only

    def test_persisted_transient_refusal_still_retries_within_budget(self) -> None:
        # A review 5686551361 requirement 4: a DEFINITE transient
        # refusal whose completion write SUCCEEDS keeps the normal
        # bounded retry per budget (the completion-write-failure stop
        # must not swallow legitimate retries).
        scripted = _ScriptedTransport(
            NotificationKind.SMTP_EMAIL,
            [
                _result(
                    outcome=DeliveryOutcome.TRANSIENT_FAILURE,
                    detail=f"450 try {n}",
                )
                for n in range(3)
            ],
        )
        with Session(engine) as session:
            event = _event(source=f"disp-cf-retry-{uuid.uuid4()}")
            session.add(event)
            session.commit()
            event_id = event.id
        dispatcher = AlertNotificationDispatcher(
            [scripted],
            max_attempts=3,
            sleep=_Sleep(),
            attempt_session_factory=_independent_factory,
        )
        with Session(engine) as session:
            stored = session.get(AlertEvent, event_id)
            assert stored is not None
            report = dispatcher.dispatch(session, stored, recipients=("a@example.com",))
        assert len(scripted.envelopes) == 3  # full budget used
        assert report.results[0].outcome is DeliveryOutcome.TRANSIENT_FAILURE
        with Session(engine) as reader:
            snapshot = [(r.outcome, r.delivered) for r in _attempts(reader, event_id)]
        assert snapshot == [("transient_failure", False)] * 3

    def test_unknown_completion_failure_holds_across_restart(self) -> None:
        # Requirement 2 (A review 5682106843 defect 1 exact repro):
        # DATA breaks (acceptance unknown), the intent row IS written,
        # but the COMPLETION write fails (audit store outage). The row
        # stays send_intent; a later run with a healthy audit store
        # must NOT resend — the unfinished intent is a cross-process
        # hold for manual verification.
        MockSMTP.state = _State(send_raises=ConnectionResetError("eof mid-data"))
        first = SMTPTransport(_config(), _factory())
        completion = _FailOnNthFactory(fail_on_call=2)  # intent ok, complete fails
        with Session(engine) as session:
            event = _event(source=f"disp-intent-hold-{uuid.uuid4()}")
            session.add(event)
            session.commit()
            event_id = event.id
        dispatcher = AlertNotificationDispatcher(
            [first], attempt_session_factory=completion
        )
        with Session(engine) as session:
            stored = session.get(AlertEvent, event_id)
            assert stored is not None
            report = dispatcher.dispatch(session, stored, recipients=("a@example.com",))
        assert len(MockSMTP.sent_messages) == 1  # the one unproven send
        assert report.results[0].acceptance_unknown is True
        assert report.results[0].detail.endswith(
            "[send_intent hold retained; manual verification required]"
        )
        with Session(engine) as reader:
            rows = _attempts(reader, event_id)
            snapshot = [(r.outcome, r.delivered) for r in rows]
        assert snapshot == [(SEND_INTENT_OUTCOME, False)]  # the hold row

        # Restart shape: healthy audit store, fresh adapter, fresh run.
        MockSMTP.state = _State()
        second = SMTPTransport(_config(), _factory())
        later = dispatch_committed_event(
            _independent_factory,
            event_id,
            transports=[second],
            recipients=("a@example.com",),
        )
        assert len(MockSMTP.sent_messages) == 1  # STILL one: never resent
        assert later.skipped_channels == (NotificationKind.SMTP_EMAIL,)

    def test_successful_send_completes_intent_one_way(self) -> None:
        # Requirement 3 shape: after a normal acceptance the intent row
        # is completed (not left holding, not duplicated) with the full
        # result fields — one row per real send.
        scripted = _ScriptedTransport(
            NotificationKind.SMTP_EMAIL, [_result(detail="queued as 123")]
        )
        with Session(engine) as session:
            event = _event(source=f"disp-intent-ok-{uuid.uuid4()}")
            session.add(event)
            session.flush()
            dispatcher = AlertNotificationDispatcher([scripted])
            dispatcher.dispatch(session, event)
            rows = _attempts(session, event.id)
            snapshot = [(r.outcome, r.delivered, r.detail, r.channel) for r in rows]
            session.rollback()
        assert snapshot == [
            ("accepted_by_service", True, "queued as 123", "smtp_email")
        ]

    def test_unfinished_intent_row_skips_channel_in_dedup(self) -> None:
        # The dedup guard treats an unfinished send_intent row as
        # settled (held) even with no delivered/unknown row present.
        smtp = _ScriptedTransport(NotificationKind.SMTP_EMAIL, [_result()])
        dispatcher = AlertNotificationDispatcher([smtp])
        with Session(engine) as session:
            event = _event(source=f"disp-intent-dedup-{uuid.uuid4()}")
            session.add(event)
            session.flush()
            session.add(
                NotificationAttempt(
                    alert_event_id=event.id,
                    channel="smtp_email",
                    outcome=SEND_INTENT_OUTCOME,
                    delivered=False,
                    detail="dispatch intent recorded before network send",
                )
            )
            report = dispatcher.dispatch(session, event)
            session.rollback()
        assert smtp.envelopes == []
        assert report.skipped_channels == (NotificationKind.SMTP_EMAIL,)
