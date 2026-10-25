"""Dispatch-runner regressions (B3-R): pending scan, hammering guard,
worker loop.

All transports are synthetic (no network, no credentials, no real
sends); database work commits rows with uuid-suffixed source ids so
reruns against the persistent local test database never collide.
Helpers are shared from ``test_dispatch`` per repo test idiom.
"""

from __future__ import annotations

import unittest
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import patch

from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, col, func, select

from app.core.db import engine
from app.modules.alerts.models.event import AlertEvent, NotificationAttempt
from app.modules.alerts.notifications.runner import (
    _DISPATCH_LEASE_KEY,
    RunnerCycleReport,
    pending_alert_event_ids,
    run_dispatch_worker,
    run_pending_dispatch_cycle,
)
from app.modules.alerts.notifications.transport import (
    DeliveryOutcome,
    NotificationEnvelope,
    NotificationKind,
    NotificationResult,
)
from tests.modules.alerts.test_dispatch import (
    _event,
    _independent_factory,
    _result,
    _ScriptedTransport,
)


class _EndlessTransport(_ScriptedTransport):
    """Scripted transport that keeps answering after its script runs
    out: pending scans pick up stray rows from other tests, so a
    finite script would make cycle tests order-dependent."""

    def send(self, envelope: NotificationEnvelope) -> NotificationResult:
        self.envelopes.append(envelope)
        if self._results:
            return self._results.pop(0)
        return _result(self.kind)


class _TargetedTransport(_EndlessTransport):
    """Scripted results apply ONLY to one target event; everything
    else gets a synthetic success. Cycle tests stay deterministic even
    when the shared test database holds stray pending events."""

    def __init__(
        self,
        kind: NotificationKind,
        results: list[NotificationResult],
        target_event_id: uuid.UUID,
    ) -> None:
        super().__init__(kind, results)
        self._target = target_event_id

    def send(self, envelope: NotificationEnvelope) -> NotificationResult:
        self.envelopes.append(envelope)
        if envelope.source_event_id == self._target and self._results:
            return self._results.pop(0)
        return _result(self.kind)


def _targeted(
    transport: _EndlessTransport, event_id: uuid.UUID
) -> list[NotificationEnvelope]:
    return [e for e in transport.envelopes if e.source_event_id == event_id]


def _seed_attempt(
    event_id: uuid.UUID,
    channel: str,
    *,
    delivered: bool,
) -> NotificationAttempt:
    return NotificationAttempt(
        alert_event_id=event_id,
        channel=channel,
        outcome=(
            DeliveryOutcome.ACCEPTED_BY_SERVICE.value
            if delivered
            else DeliveryOutcome.TRANSIENT_FAILURE.value
        ),
        delivered=delivered,
        detail="runner-test-seed",
    )


def _commit_event(*, source: str, created_at: datetime | None = None) -> uuid.UUID:
    event = _event(source=source)
    if created_at is not None:
        event.created_at = created_at
    with Session(engine) as session:
        session.add(event)
        session.commit()
        return event.id


def _pending() -> list[uuid.UUID]:
    # Backoff OFF for scan-shape tests; the dedicated backoff test
    # (SafetyCase) asserts the deferral behavior itself.
    return pending_alert_event_ids(_independent_factory, min_attempt_age_s=0.0)


class PendingScanCase(unittest.TestCase):
    """pending_alert_event_ids: no delivered attempt -> pending;
    delivered -> excluded; partial -> still pending; capped -> out."""

    def test_events_without_attempts_are_pending_oldest_first(self) -> None:
        older = _commit_event(
            source=f"run-sel-a-{uuid.uuid4()}",
            created_at=datetime(2026, 10, 24, 1, 0, tzinfo=UTC),
        )
        newer = _commit_event(
            source=f"run-sel-b-{uuid.uuid4()}",
            created_at=datetime(2026, 10, 24, 2, 0, tzinfo=UTC),
        )
        pending = _pending()
        self.assertIn(older, pending)
        self.assertIn(newer, pending)
        self.assertLess(pending.index(older), pending.index(newer))

    def test_delivered_event_is_excluded(self) -> None:
        event_id = _commit_event(source=f"run-del-{uuid.uuid4()}")
        with Session(engine) as session:
            # Delivered on EVERY default channel -> nothing left to send.
            session.add(
                _seed_attempt(
                    event_id, NotificationKind.SMTP_EMAIL.value, delivered=True
                )
            )
            session.add(
                _seed_attempt(event_id, NotificationKind.APP_PUSH.value, delivered=True)
            )
            session.commit()
        self.assertNotIn(event_id, _pending())

    def test_partially_delivered_event_stays_pending(self) -> None:
        event_id = _commit_event(source=f"run-part-{uuid.uuid4()}")
        with Session(engine) as session:
            session.add(
                _seed_attempt(
                    event_id, NotificationKind.SMTP_EMAIL.value, delivered=True
                )
            )
            session.commit()
        self.assertIn(event_id, _pending())

    def test_attempt_cap_excludes_but_lower_cap_includes(self) -> None:
        event_id = _commit_event(source=f"run-cap-{uuid.uuid4()}")
        with Session(engine) as session:
            for _ in range(2):
                session.add(
                    _seed_attempt(
                        event_id, NotificationKind.SMTP_EMAIL.value, delivered=False
                    )
                )
            session.commit()
        self.assertNotIn(
            event_id,
            pending_alert_event_ids(
                _independent_factory,
                max_attempts_per_event=2,
                min_attempt_age_s=0.0,
            ),
        )
        self.assertIn(
            event_id,
            pending_alert_event_ids(
                _independent_factory,
                max_attempts_per_event=3,
                min_attempt_age_s=0.0,
            ),
        )


class RunnerCycleCase(unittest.TestCase):
    """run_pending_dispatch_cycle: dispatch, complete missing channels,
    stop re-sending delivered ones, respect the hammering guard."""

    def test_default_single_channel_scan_does_not_starve_new_event(self) -> None:
        # A two-row page reproduces the production 50-row starvation case:
        # old events have SMTP delivered but no unconfigured app_push attempt.
        created: list[uuid.UUID] = []
        try:
            for minute in (0, 1):
                created.append(
                    _commit_event(
                        source=f"run-smtp-old-{uuid.uuid4()}",
                        created_at=datetime(2020, 1, 1, 0, minute, tzinfo=UTC),
                    )
                )
            new_id = _commit_event(
                source=f"run-smtp-new-{uuid.uuid4()}",
                created_at=datetime(2021, 1, 1, tzinfo=UTC),
            )
            created.append(new_id)
            with Session(engine) as session:
                session.add_all(
                    _seed_attempt(
                        event_id, NotificationKind.SMTP_EMAIL.value, delivered=True
                    )
                    for event_id in created[:2]
                )
                session.commit()

            smtp = _TargetedTransport(NotificationKind.SMTP_EMAIL, [], new_id)
            with patch(
                "app.modules.alerts.notifications.runner.build_transports",
                return_value=[smtp],
            ):
                preview = run_pending_dispatch_cycle(
                    _independent_factory,
                    limit=2,
                    min_attempt_age_s=0.0,
                    dry_run=True,
                )
                self.assertIn(new_id, preview.pending)
                self.assertFalse(set(created[:2]) & set(preview.pending))
                self.assertEqual(smtp.envelopes, [])
                report = run_pending_dispatch_cycle(
                    _independent_factory,
                    limit=1,
                    min_attempt_age_s=0.0,
                )
            self.assertIn(new_id, report.pending)
            self.assertEqual(len(_targeted(smtp, new_id)), 1)
            with Session(engine) as reader:
                self.assertEqual(len(_attempts(reader, new_id)), 1)
        finally:
            if created:
                with Session(engine) as session:
                    session.execute(
                        delete(NotificationAttempt).where(
                            col(NotificationAttempt.alert_event_id).in_(created)
                        )
                    )
                    session.execute(
                        delete(AlertEvent).where(col(AlertEvent.id).in_(created))
                    )
                    session.commit()

    def test_no_configured_channel_has_no_pending_or_dispatch(self) -> None:
        with patch(
            "app.modules.alerts.notifications.runner.build_transports",
            return_value=[],
        ):
            dry = run_pending_dispatch_cycle(_independent_factory, dry_run=True)
            live = run_pending_dispatch_cycle(_independent_factory)
        self.assertEqual(dry.pending, ())
        self.assertEqual(live.pending, ())
        self.assertEqual(live.reports, ())

    def test_cycle_dispatches_then_next_cycle_skips(self) -> None:
        event_id = _commit_event(source=f"run-cycle-{uuid.uuid4()}")
        smtp = _TargetedTransport(NotificationKind.SMTP_EMAIL, [_result()], event_id)
        first = run_pending_dispatch_cycle(_independent_factory, transports=[smtp])
        reports = dict(first.reports)
        self.assertIn(event_id, first.pending)
        self.assertIn(event_id, reports)
        self.assertFalse(reports[event_id].dedup_error)
        self.assertEqual(len(_targeted(smtp, event_id)), 1)
        with Session(engine) as reader:
            self.assertEqual(len(_attempts(reader, event_id)), 1)
        second = run_pending_dispatch_cycle(_independent_factory, transports=[smtp])
        self.assertNotIn(event_id, second.pending)
        self.assertNotIn(event_id, dict(second.reports))
        # No duplicate send after delivery: still exactly one envelope.
        self.assertEqual(len(_targeted(smtp, event_id)), 1)

    def test_cycle_completes_only_missing_channel(self) -> None:
        event_id = _commit_event(source=f"run-partial-{uuid.uuid4()}")
        with Session(engine) as session:
            session.add(
                _seed_attempt(
                    event_id, NotificationKind.SMTP_EMAIL.value, delivered=True
                )
            )
            session.commit()
        smtp = _EndlessTransport(NotificationKind.SMTP_EMAIL, [])
        push = _TargetedTransport(NotificationKind.APP_PUSH, [], event_id)
        report = run_pending_dispatch_cycle(
            _independent_factory,
            transports=[smtp, push],
            min_attempt_age_s=0.0,
        )
        per_event = dict(report.reports).get(event_id)
        self.assertIsNotNone(per_event)
        assert per_event is not None
        self.assertEqual(
            [r.transport for r in per_event.results], [NotificationKind.APP_PUSH]
        )
        self.assertIn(NotificationKind.SMTP_EMAIL, per_event.skipped_channels)
        # Delivered channel untouched, missing channel served.
        self.assertEqual(_targeted(smtp, event_id), [])
        self.assertEqual(len(_targeted(push, event_id)), 1)
        with Session(engine) as reader:
            channels = {row.channel for row in _attempts(reader, event_id)}
        self.assertIn("app_push", channels)
        with Session(engine) as reader:
            smtp_rows = [
                row
                for row in _attempts(reader, event_id)
                if row.channel == "smtp_email"
            ]
            self.assertEqual(len(smtp_rows), 1)

    def test_capped_event_is_not_dispatched_again(self) -> None:
        event_id = _commit_event(source=f"run-capd-{uuid.uuid4()}")
        with Session(engine) as session:
            for _ in range(2):
                session.add(
                    _seed_attempt(
                        event_id, NotificationKind.SMTP_EMAIL.value, delivered=False
                    )
                )
            session.commit()
        push = _EndlessTransport(NotificationKind.APP_PUSH, [])
        guarded = run_pending_dispatch_cycle(
            _independent_factory,
            transports=[push],
            max_attempts_per_event=2,
            min_attempt_age_s=0.0,
        )
        self.assertNotIn(event_id, guarded.pending)
        self.assertNotIn(event_id, dict(guarded.reports))
        # Below the cap the event is picked up again (persistent retry
        # across cycles is the point of the runner).
        retried = run_pending_dispatch_cycle(
            _independent_factory,
            transports=[push],
            max_attempts_per_event=3,
            min_attempt_age_s=0.0,
        )
        self.assertIn(event_id, retried.pending)
        self.assertIn(event_id, dict(retried.reports))

    def test_retry_exhaustion_leaves_event_for_next_cycle(self) -> None:
        event_id = _commit_event(source=f"run-exh-{uuid.uuid4()}")
        push = _TargetedTransport(
            NotificationKind.APP_PUSH,
            # Every retry attempt fails: the report carries the LAST
            # attempt's result, so script each attempt transient.
            [
                _result(NotificationKind.APP_PUSH, DeliveryOutcome.TRANSIENT_FAILURE),
                _result(NotificationKind.APP_PUSH, DeliveryOutcome.TRANSIENT_FAILURE),
            ],
            event_id,
        )
        report = run_pending_dispatch_cycle(
            _independent_factory,
            transports=[push],
            max_attempts=2,
        )
        per_event = dict(report.reports).get(event_id)
        self.assertIsNotNone(per_event)
        assert per_event is not None
        self.assertEqual(
            per_event.results[0].outcome, DeliveryOutcome.TRANSIENT_FAILURE
        )
        with Session(engine) as reader:
            self.assertEqual(len(_attempts(reader, event_id)), 2)
        # Still pending: a later cycle (or restart) retries it.
        self.assertIn(event_id, _pending())


class MutexLeaseCase(unittest.TestCase):
    """Single-dispatcher mutex: second instance never sends; a dying
    lease connection stops new dispatches; release lets the next one
    in. Lock is a dedicated PG session advisory lock, never a business
    transaction or alert row lock."""

    def test_second_instance_cannot_grab_lease_or_send(self) -> None:
        holder = Session(engine)
        try:
            held = holder.exec(
                select(func.pg_try_advisory_lock(_DISPATCH_LEASE_KEY))
            ).one()
            self.assertTrue(held)
            push = _EndlessTransport(NotificationKind.APP_PUSH, [])
            reports = run_dispatch_worker(
                _independent_factory,
                transports=[push],
                poll_interval_s=0,
                max_cycles=2,
            )
            self.assertEqual(reports, [RunnerCycleReport(interrupted="lease_busy")])
            self.assertEqual(push.envelopes, [])
            self.assertTrue(
                holder.exec(select(func.pg_advisory_unlock(_DISPATCH_LEASE_KEY))).one()
            )
        finally:
            holder.close()
        # After release, a new worker acquires and runs.
        push = _EndlessTransport(NotificationKind.APP_PUSH, [])
        reports = run_dispatch_worker(
            _independent_factory,
            transports=[push],
            poll_interval_s=0,
            max_cycles=1,
        )
        self.assertNotEqual(reports, [])
        self.assertIsNone(reports[0].interrupted)

    def test_lost_lease_connection_stops_new_dispatches(self) -> None:
        class _DyingLease:
            """Acquires the 'lock', then the connection dies."""

            def __init__(self) -> None:
                self.calls = 0

            def exec(self, statement: Any) -> Any:
                self.calls += 1
                if self.calls == 1:  # pg_try_advisory_lock -> True
                    return _OneShotResult(True)
                raise OperationalError("SELECT 1", {}, OSError("conn lost"))

            def __call__(self) -> _DyingLease:
                return self

            def __enter__(self) -> _DyingLease:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

        push = _EndlessTransport(NotificationKind.APP_PUSH, [])
        factory = cast("Callable[[], AbstractContextManager[Session]]", _DyingLease())
        reports = run_dispatch_worker(
            _independent_factory,
            transports=[push],
            poll_interval_s=0,
            max_cycles=2,
            lease_session_factory=factory,
        )
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].interrupted, "OperationalError")
        self.assertEqual(push.envelopes, [])


class SafetyCase(unittest.TestCase):
    """Dry-run writes nothing; uncommitted events are never scanned;
    persistent backoff defers freshly-attempted events."""

    def test_dry_run_reports_without_sending_or_writing(self) -> None:
        event_id = _commit_event(source=f"run-dry-{uuid.uuid4()}")
        push = _TargetedTransport(NotificationKind.APP_PUSH, [], event_id)
        report = run_pending_dispatch_cycle(
            _independent_factory, transports=[push], dry_run=True
        )
        self.assertTrue(report.dry_run)
        self.assertIn(event_id, report.pending)
        self.assertEqual(report.reports, ())
        self.assertEqual(_targeted(push, event_id), [])
        with Session(engine) as reader:
            self.assertEqual(_attempts(reader, event_id), [])

    def test_uncommitted_event_is_never_dispatched(self) -> None:
        push = _EndlessTransport(NotificationKind.APP_PUSH, [])
        uncommitted = _event(source=f"run-uncommitted-{uuid.uuid4()}")
        with Session(engine) as session:
            session.add(uncommitted)
            session.flush()  # visible only inside THIS transaction
            report = run_pending_dispatch_cycle(_independent_factory, transports=[push])
        self.assertNotIn(uncommitted.id, report.pending)
        self.assertNotIn(uncommitted.id, dict(report.reports))

    def test_persistent_backoff_defers_fresh_attempts(self) -> None:
        event_id = _commit_event(source=f"run-backoff-{uuid.uuid4()}")
        with Session(engine) as session:
            session.add(
                _seed_attempt(
                    event_id, NotificationKind.SMTP_EMAIL.value, delivered=False
                )
            )
            session.commit()
        # Budget rows persist in the table: a fresh attempt defers the
        # event across restarts — no in-memory counter involved.
        self.assertNotIn(
            event_id,
            pending_alert_event_ids(_independent_factory, min_attempt_age_s=3600.0),
        )
        self.assertIn(
            event_id,
            pending_alert_event_ids(_independent_factory, min_attempt_age_s=0.0),
        )

    def test_budget_persisted_rows_cap_events(self) -> None:
        event_id = _commit_event(source=f"run-budget-{uuid.uuid4()}")
        with Session(engine) as session:
            for _ in range(12):
                session.add(
                    _seed_attempt(
                        event_id, NotificationKind.SMTP_EMAIL.value, delivered=False
                    )
                )
            session.commit()
        # Restart cannot zero the budget: rows are the budget.
        self.assertNotIn(
            event_id,
            pending_alert_event_ids(_independent_factory, max_attempts_per_event=12),
        )


class _OneShotResult:
    def __init__(self, value: bool) -> None:
        self._value = value

    def one(self) -> bool:
        return self._value


class WorkerLoopCase(unittest.TestCase):
    """run_dispatch_worker: bounded cycles, stop flag, arg validation."""

    def test_worker_runs_bounded_cycles(self) -> None:
        push = _EndlessTransport(NotificationKind.APP_PUSH, [])
        reports = run_dispatch_worker(
            _independent_factory,
            transports=[push],
            poll_interval_s=0,
            max_cycles=2,
        )
        self.assertEqual(len(reports), 2)

    def test_worker_stops_on_flag(self) -> None:
        class _StopAfterOne:
            def __init__(self) -> None:
                self.calls = 0

            def __call__(self) -> bool:
                self.calls += 1
                return self.calls > 1

        push = _EndlessTransport(NotificationKind.APP_PUSH, [])
        reports = run_dispatch_worker(
            _independent_factory,
            transports=[push],
            poll_interval_s=0,
            stop=_StopAfterOne(),
        )
        # Checked at cycle top and before sleeping: exactly one cycle.
        self.assertEqual(len(reports), 1)

    def test_worker_validates_arguments(self) -> None:
        push = _EndlessTransport(NotificationKind.APP_PUSH, [])
        with self.assertRaises(ValueError):
            run_dispatch_worker(
                _independent_factory, transports=[push], poll_interval_s=-1
            )
        with self.assertRaises(ValueError):
            run_dispatch_worker(_independent_factory, transports=[push], max_cycles=0)


def _attempts(session: Session, event_id: uuid.UUID) -> list[NotificationAttempt]:
    statement = (
        select(NotificationAttempt)
        .where(col(NotificationAttempt.alert_event_id) == event_id)
        .order_by(col(NotificationAttempt.created_at), col(NotificationAttempt.id))
    )
    return list(session.exec(statement))


if __name__ == "__main__":
    unittest.main()
