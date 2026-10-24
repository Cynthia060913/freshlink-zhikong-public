"""Lifecycle concurrency regressions (PR #10 ack/recovery races).

Real PostgreSQL, dual connections (SQLite cannot express these
races). Proofs:

1. Two racing acknowledgements leave exactly ONE ``ack`` transition
   row — the loser blocks on the event-row lock, re-reads the
   committed status and takes the idempotent no-op path.
2. A recovery committing first wins: a concurrently arriving ack is
   rejected (``AlertAlreadyResolved``) and the alert is NEVER
   regressed from resolved back to acknowledged.
3. An ack committing first is honored: the recovery resolves FROM
   ``acknowledged`` (never from a stale "open" read).
4. A recovery racing an already-resolved alert (and a replayed
   recovery) does not duplicate the ``resolved`` row.

Mechanism under test: ``acknowledge_alert`` and the recovery path
both take ``FOR UPDATE`` on the alert's event row BEFORE reading the
status (services/events.py ``_lock_alert_row``).
"""

from __future__ import annotations

import threading
import unittest
import uuid
from typing import Any

from sqlmodel import Session, col, select

from app.core.db import engine
from app.modules.alerts.models.batch import BatchCreate
from app.modules.alerts.models.event import (
    AlertEvent,
    AlertStatusTransition,
    CandidateCreate,
    CandidateKind,
)
from app.modules.alerts.models.warehouse import WarehouseCreate
from app.modules.alerts.services.batches import create_batch
from app.modules.alerts.services.events import (
    AlertAlreadyResolved,
    acknowledge_alert,
    get_alert_status,
    receive_candidate,
)
from app.modules.alerts.services.warehouses import create_warehouse

_SNAP = uuid.UUID("99999999-9999-4999-8999-999999999999")
_RULE_VERSION = "v1"
_SEVERITY = "warning"
_METRIC = "temperature"


def _run(fn: Any) -> tuple[threading.Thread, dict[str, Any]]:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # surfaced to the assertions below
            box["error"] = exc

    thread = threading.Thread(target=target)
    thread.start()
    return thread, box


class LifecycleConcurrencyCase(unittest.TestCase):
    """Dual-connection proofs on the shared real-PG test database."""

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        with Session(engine) as session:
            warehouse = create_warehouse(
                session,
                WarehouseCreate(code=f"LC-{suffix}", name="lifecycle-race"),
            )
            batch = create_batch(
                session,
                warehouse.id,
                BatchCreate(code=f"LCB-{suffix}", product="apple"),
            )
            self.wid = warehouse.id
            self.bid = batch.id

    def _seed_alert(self) -> tuple[uuid.UUID, str]:
        source = f"lifecycle-race-{uuid.uuid4()}"
        event = AlertEvent(
            source_event_id=source,
            kind=str(CandidateKind.TEMPERATURE_HIGH),
            warehouse_id=self.wid,
            batch_id=self.bid,
            metric_code=_METRIC,
            sampled_at="2026-10-24T08:00:00Z",
            value=11.0,
            unit="°C",
            severity=_SEVERITY,
            rule_snapshot_id=_SNAP,
            rule_version=_RULE_VERSION,
            content_digest="0" * 64,
            payload_json="{}",
        )
        with Session(engine) as session:
            session.add(event)
            session.commit()
            return event.id, source

    def _recovery(self, original_source: str) -> CandidateCreate:
        return CandidateCreate(
            source_event_id=f"recovery-{uuid.uuid4()}",
            kind=CandidateKind.TEMPERATURE_RECOVERED,
            warehouse_id=self.wid,
            batch_id=self.bid,
            observation_ids=["00000000-0000-4000-8000-0000000000aa"],
            metric_code=_METRIC,
            sampled_at="2026-10-24T09:00:00Z",
            value=6.0,
            unit="°C",
            severity=_SEVERITY,
            rule_snapshot_id=_SNAP,
            rule_version=_RULE_VERSION,
            algorithm_version="threshold-rule/0.2.0",
            evidence={"recovered": True},
            related_source_event_id=original_source,
        )

    def _transitions(self, event_id: uuid.UUID) -> list[AlertStatusTransition]:
        with Session(engine) as session:
            return list(
                session.exec(
                    select(AlertStatusTransition)
                    .where(col(AlertStatusTransition.alert_event_id) == event_id)
                    .order_by(col(AlertStatusTransition.created_at))
                ).all()
            )

    # -- 1. double ack: one row -------------------------------------

    def test_racing_double_ack_leaves_one_row(self) -> None:
        event_id, _ = self._seed_alert()
        with Session(engine) as first:
            # First writer mid-transaction: row lock + ack, uncommitted.
            first.exec(
                select(AlertEvent.id)
                .where(col(AlertEvent.id) == event_id)
                .with_for_update()
            ).first()
            first.add(
                AlertStatusTransition(
                    alert_event_id=event_id,
                    from_status="open",
                    to_status="acknowledged",
                    reason="first-writer",
                )
            )
            second, box = _run(lambda: acknowledge_alert(Session(engine), event_id))
            # Blocked on the row lock while the first writer holds it.
            second.join(2.0)
            self.assertTrue(second.is_alive(), "second ack must block on the lock")
            first.commit()
            second.join(30)
        self.assertFalse(second.is_alive())
        self.assertIsNone(box.get("error"), box)
        self.assertEqual(box["result"], ("acknowledged", False))
        ack_rows = [
            row
            for row in self._transitions(event_id)
            if row.to_status == "acknowledged"
        ]
        self.assertEqual(len(ack_rows), 1)
        self.assertEqual(get_alert_status(Session(engine), event_id), "acknowledged")

    # -- 2. recovery wins, late ack rejected ------------------------

    def test_recovery_committed_then_ack_rejected_no_regression(self) -> None:
        event_id, _ = self._seed_alert()
        with Session(engine) as first:
            first.exec(
                select(AlertEvent.id)
                .where(col(AlertEvent.id) == event_id)
                .with_for_update()
            ).first()
            first.add(
                AlertStatusTransition(
                    alert_event_id=event_id,
                    from_status="open",
                    to_status="resolved",
                    reason="recovery-wins",
                )
            )
            second, box = _run(lambda: acknowledge_alert(Session(engine), event_id))
            second.join(2.0)
            self.assertTrue(second.is_alive(), "ack must block on the lock")
            first.commit()
            second.join(30)
        self.assertFalse(second.is_alive())
        self.assertIsInstance(box.get("error"), AlertAlreadyResolved)
        self.assertNotIn("result", box)  # nothing written by the loser
        rows = self._transitions(event_id)
        self.assertEqual([row.to_status for row in rows], ["resolved"])
        self.assertEqual(get_alert_status(Session(engine), event_id), "resolved")

    # -- 3. ack wins: recovery resolves FROM acknowledged ------------

    def test_ack_committed_then_recovery_starts_from_acknowledged(self) -> None:
        event_id, source = self._seed_alert()
        with Session(engine) as first:
            first.exec(
                select(AlertEvent.id)
                .where(col(AlertEvent.id) == event_id)
                .with_for_update()
            ).first()
            first.add(
                AlertStatusTransition(
                    alert_event_id=event_id,
                    from_status="open",
                    to_status="acknowledged",
                    reason="ack-wins",
                )
            )
            payload = self._recovery(source)

            def recover() -> str:
                with Session(engine) as session:
                    receipt = receive_candidate(session, payload)
                    session.commit()
                    return str(receipt.status)

            second, box = _run(recover)
            second.join(2.0)
            self.assertTrue(second.is_alive(), "recovery must block on the lock")
            first.commit()
            second.join(30)
        self.assertFalse(second.is_alive())
        self.assertIsNone(box.get("error"), box)
        self.assertEqual(box["result"], "accepted_new")
        rows = self._transitions(event_id)
        self.assertEqual(
            [(row.from_status, row.to_status) for row in rows],
            [("open", "acknowledged"), ("acknowledged", "resolved")],
        )
        self.assertEqual(get_alert_status(Session(engine), event_id), "resolved")

    # -- 4. racing recovery vs resolved: no duplicate resolved row ---

    def test_recovery_racing_resolved_leaves_one_resolved_row(self) -> None:
        event_id, source = self._seed_alert()
        with Session(engine) as first:
            first.exec(
                select(AlertEvent.id)
                .where(col(AlertEvent.id) == event_id)
                .with_for_update()
            ).first()
            first.add(
                AlertStatusTransition(
                    alert_event_id=event_id,
                    from_status="open",
                    to_status="resolved",
                    reason="first-recovery",
                )
            )
            payload = self._recovery(source)

            def recover() -> str:
                with Session(engine) as session:
                    receipt = receive_candidate(session, payload)
                    session.commit()
                    return str(receipt.status)

            second, box = _run(recover)
            second.join(2.0)
            self.assertTrue(second.is_alive(), "second recovery must block on the lock")
            first.commit()
            second.join(30)
        self.assertFalse(second.is_alive())
        self.assertIsNone(box.get("error"), box)
        self.assertEqual(box["result"], "accepted_new")  # event lands,
        # but with NO redundant transition row.
        rows = [
            row for row in self._transitions(event_id) if row.to_status == "resolved"
        ]
        self.assertEqual(len(rows), 1)

    def test_replayed_recovery_does_not_duplicate_resolved_row(self) -> None:
        event_id, source = self._seed_alert()
        payload = self._recovery(source)
        payload = payload.model_copy(
            update={"source_event_id": f"recovery-replay-{uuid.uuid4()}"}
        )
        with Session(engine) as session:
            first = receive_candidate(session, payload)
            session.commit()
            second = receive_candidate(session, payload)
            session.commit()
        self.assertEqual(str(first.status), "accepted_new")
        self.assertEqual(str(second.status), "accepted_duplicate")
        resolved = [
            row for row in self._transitions(event_id) if row.to_status == "resolved"
        ]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(get_alert_status(Session(engine), event_id), "resolved")


if __name__ == "__main__":
    unittest.main()
