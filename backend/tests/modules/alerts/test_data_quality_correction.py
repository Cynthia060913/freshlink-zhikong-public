"""Data-quality correction contract regressions (F03, #5 6394581672).

Late bad evidence for an already-accepted anomaly/recovery arrives as
a ``data_quality`` candidate whose ``related_source_event_id`` names
the original anomaly. Contract under test:

- valid correction: accepted, stored with the link, NOTifies with
  both ids (envelope), and NEVER writes a transition row — a
  resolved original stays resolved (no reopen), an open one stays
  open;
- unknown original / cross-batch link: permanent invalid receipts;
- other kinds still cannot carry ``related_source_event_id`` (422);
- replay of the same ``source_event_id`` is an idempotent duplicate
  (exactly one correction row, digest-keyed).

Real PostgreSQL (shared test database); world rows use uuid-suffixed
codes so reruns never collide.
"""

from __future__ import annotations

import unittest
import uuid
from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.core.db import engine
from app.modules.alerts.models.batch import BatchCreate
from app.modules.alerts.models.event import (
    AlertEvent,
    AlertStatusTransition,
    CandidateCreate,
    CandidateKind,
    NotificationAttempt,
)
from app.modules.alerts.models.warehouse import WarehouseCreate
from app.modules.alerts.notifications.dispatch import build_envelope
from app.modules.alerts.notifications.transport import NotificationKind
from app.modules.alerts.services.batches import create_batch
from app.modules.alerts.services.events import (
    get_alert_status,
    receive_candidate,
)
from app.modules.alerts.services.warehouses import create_warehouse


def _candidate(**over: Any) -> CandidateCreate:
    base: dict[str, Any] = {
        "source_event_id": f"cand-{uuid.uuid4()}",
        "warehouse_id": None,  # supplied by the world
        "batch_id": None,
        "observation_ids": ["00000000-0000-4000-8000-0000000000aa"],
        "metric_code": "temperature",
        "sampled_at": "2026-10-25T08:00:00Z",
        "value": 11.0,
        "unit": "°C",
        "kind": CandidateKind.TEMPERATURE_HIGH,
        "severity": "warning",
        "rule_snapshot_id": uuid.UUID("aaaa1111-1111-4111-8111-111111111111"),
        "rule_version": "v1",
        "algorithm_version": "threshold-rule/0.2.0",
        "evidence": {"comparison": ">", "threshold": 8.0},
    }
    base.update(over)
    return CandidateCreate(**base)


class CorrectionContractCase(unittest.TestCase):
    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        with Session(engine) as session:
            warehouse = create_warehouse(
                session,
                WarehouseCreate(code=f"CQ-{suffix}", name="correction-world"),
            )
            self.wid = warehouse.id
            self.bid = create_batch(
                session,
                self.wid,
                BatchCreate(code=f"CQB-{suffix}", product="apple"),
            ).id
            other_batch = create_batch(
                session,
                self.wid,
                BatchCreate(code=f"CQX-{suffix}", product="pear"),
            )
            self.other_bid = other_batch.id
        # source_event_ids this test committed, for tearDown cleanup:
        # committed rows would otherwise join the shared database and
        # crowd the runner tests' oldest-first pending scan.
        self._sources: list[str] = []

    def tearDown(self) -> None:
        if not self._sources:
            return
        with Session(engine) as session:
            ids = list(
                session.exec(
                    select(AlertEvent.id).where(
                        col(AlertEvent.source_event_id).in_(self._sources)
                    )
                ).all()
            )
            if ids:
                session.execute(
                    delete(AlertStatusTransition).where(
                        col(AlertStatusTransition.alert_event_id).in_(ids)
                    )
                )
                session.execute(
                    delete(NotificationAttempt).where(
                        col(NotificationAttempt.alert_event_id).in_(ids)
                    )
                )
                session.execute(delete(AlertEvent).where(col(AlertEvent.id).in_(ids)))
            session.commit()

    def _receive(self, payload: CandidateCreate) -> Any:
        self._sources.append(payload.source_event_id)
        with Session(engine) as session:
            receipt = receive_candidate(session, payload)
            session.commit()
            return receipt

    def _seed_original(self, *, resolved: bool) -> str:
        source = f"original-{uuid.uuid4()}"
        self._receive(
            _candidate(
                source_event_id=source,
                warehouse_id=self.wid,
                batch_id=self.bid,
            )
        )
        if resolved:
            self._receive(
                _candidate(
                    source_event_id=f"recovered-{uuid.uuid4()}",
                    warehouse_id=self.wid,
                    batch_id=self.bid,
                    kind=CandidateKind.TEMPERATURE_RECOVERED,
                    value=6.0,
                    rule_snapshot_id=uuid.UUID("aaaa1111-1111-4111-8111-111111111111"),
                    rule_version="v1",
                    related_source_event_id=source,
                )
            )
        return source

    def _correction(self, related: str) -> CandidateCreate:
        return _candidate(
            source_event_id=f"correction-{uuid.uuid4()}",
            warehouse_id=self.wid,
            batch_id=self.bid,
            kind=CandidateKind.DATA_QUALITY,
            severity="info",
            value=None,
            rule_snapshot_id=None,
            rule_version=None,
            algorithm_version="quality-gate/0.3.0",
            evidence={"correction": "late invalid evidence"},
            related_source_event_id=related,
        )

    def _transitions(self, source: str) -> list[str]:
        with Session(engine) as session:
            original = session.exec(
                select(AlertEvent).where(col(AlertEvent.source_event_id) == source)
            ).first()
            assert original is not None
            return [
                str(row.to_status)
                for row in session.exec(
                    select(AlertStatusTransition)
                    .where(col(AlertStatusTransition.alert_event_id) == original.id)
                    .order_by(col(AlertStatusTransition.created_at))
                ).all()
            ]

    # -- valid correction, resolved original stays resolved ----------

    def test_correction_after_recovery_keeps_resolved_and_links(self) -> None:
        original = self._seed_original(resolved=True)
        receipt = self._receive(self._correction(original))
        self.assertEqual(str(receipt.status), "accepted_new")
        self.assertIsNotNone(receipt.event_id)
        # History untouched: exactly the recovery's one resolved row.
        self.assertEqual(self._transitions(original), ["resolved"])
        self.assertEqual(
            get_alert_status(Session(engine), self._event_id(original)),
            "resolved",
        )

    def test_correction_on_open_alert_does_not_resolve(self) -> None:
        original = self._seed_original(resolved=False)
        receipt = self._receive(self._correction(original))
        self.assertEqual(str(receipt.status), "accepted_new")
        self.assertEqual(self._transitions(original), [])
        self.assertEqual(
            get_alert_status(Session(engine), self._event_id(original)),
            "open",
        )

    def test_correction_row_carries_the_link(self) -> None:
        original = self._seed_original(resolved=False)
        receipt = self._receive(self._correction(original))
        assert receipt.event_id is not None
        with Session(engine) as session:
            row = session.get(AlertEvent, receipt.event_id)
            assert row is not None
            self.assertEqual(str(row.kind), str(CandidateKind.DATA_QUALITY))
            self.assertEqual(row.related_source_event_id, original)

    def test_envelope_shows_both_ids(self) -> None:
        original = self._seed_original(resolved=False)
        receipt = self._receive(self._correction(original))
        assert receipt.event_id is not None
        with Session(engine) as session:
            row = session.get(AlertEvent, receipt.event_id)
            assert row is not None
            envelope = build_envelope(row, NotificationKind.SMTP_EMAIL)
        self.assertIn(f"来源事件: {row.source_event_id}", envelope.body_text)
        self.assertIn(f"关联事件: {original}", envelope.body_text)

    # -- invalid links ------------------------------------------------

    def test_unknown_original_is_invalid(self) -> None:
        receipt = self._receive(self._correction(f"missing-{uuid.uuid4()}"))
        self.assertEqual(str(receipt.status), "invalid")
        self.assertEqual(receipt.reason_code, "unknown_related_event")

    def test_cross_batch_link_is_invalid(self) -> None:
        original = self._seed_original(resolved=False)
        wrong = _candidate(
            source_event_id=f"correction-{uuid.uuid4()}",
            warehouse_id=self.wid,
            batch_id=self.other_bid,  # same warehouse, other batch
            kind=CandidateKind.DATA_QUALITY,
            severity="info",
            value=None,
            rule_snapshot_id=None,
            rule_version=None,
            related_source_event_id=original,
        )
        receipt = self._receive(wrong)
        self.assertEqual(str(receipt.status), "invalid")
        self.assertEqual(receipt.reason_code, "related_event_mismatch")

    def test_other_kinds_still_reject_related_at_dto(self) -> None:
        with self.assertRaises(ValueError):
            _candidate(
                warehouse_id=self.wid,
                batch_id=self.bid,
                related_source_event_id=f"original-{uuid.uuid4()}",
            )

    def test_recovered_without_related_still_rejected_at_dto(self) -> None:
        with self.assertRaises(ValueError):
            _candidate(
                warehouse_id=self.wid,
                batch_id=self.bid,
                kind=CandidateKind.TEMPERATURE_RECOVERED,
                rule_snapshot_id=uuid.UUID("aaaa1111-1111-4111-8111-111111111111"),
            )

    # -- idempotent replay --------------------------------------------

    def test_correction_replay_is_duplicate_single_row(self) -> None:
        original = self._seed_original(resolved=True)
        payload = self._correction(original)
        source = payload.source_event_id
        first = self._receive(payload)
        replay = self._receive(payload.model_copy(update={"source_event_id": source}))
        self.assertEqual(str(first.status), "accepted_new")
        self.assertEqual(str(replay.status), "accepted_duplicate")
        with Session(engine) as session:
            rows = list(
                session.exec(
                    select(AlertEvent).where(col(AlertEvent.source_event_id) == source)
                ).all()
            )
        self.assertEqual(len(rows), 1)
        # The replay must not duplicate history either.
        self.assertEqual(self._transitions(original), ["resolved"])

    def _event_id(self, source: str) -> uuid.UUID:
        with Session(engine) as session:
            row = session.exec(
                select(AlertEvent).where(col(AlertEvent.source_event_id) == source)
            ).first()
            assert row is not None
            return row.id


if __name__ == "__main__":
    unittest.main()
