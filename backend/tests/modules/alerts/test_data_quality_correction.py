"""Data-quality correction contract regressions (F03, #5 6394581672).

Late bad evidence for an already-accepted anomaly/recovery arrives as
a ``data_quality`` candidate whose ``related_source_event_id`` names
the original anomaly. Contract under test:

- valid correction: accepted, stored with the link, notified with
  both ids (envelope), and NEVER writes a transition row — a
  resolved original stays resolved (no reopen), an open one stays
  open, and the correction trace survives later recovery activity;
- unknown original / cross-batch link: permanent invalid receipts
  with zero partial writes;
- other kinds still cannot carry ``related_source_event_id`` (422);
- same ``source_event_id`` + same body replays as an idempotent
  duplicate (one row); same id with a CHANGED body is a conflict;
- the correction takes the SAME original-row lock as ACK/recovery:
  a correction racing a real recovery on a second PG connection
  serializes behind it and must not regress the resolved state.

Real PostgreSQL (shared test database); world rows use uuid-suffixed
codes so reruns never collide, and committed alert rows are deleted
again in tearDown so the shared database stays clean.
"""

from __future__ import annotations

import os
import threading
import unittest
import uuid
from typing import Any

from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.db import engine
from app.modules.alerts.models.batch import Batch, BatchCreate
from app.modules.alerts.models.event import (
    AlertEvent,
    AlertStatusTransition,
    CandidateCreate,
    CandidateKind,
    NotificationAttempt,
)
from app.modules.alerts.models.warehouse import Warehouse, WarehouseCreate
from app.modules.alerts.notifications.dispatch import build_envelope
from app.modules.alerts.notifications.transport import NotificationKind
from app.modules.alerts.services.batches import create_batch
from app.modules.alerts.services.events import get_alert_status, receive_candidate
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
    @classmethod
    def setUpClass(cls) -> None:
        # This suite writes real PostgreSQL rows. Never run it merely because
        # an application DATABASE_URL happens to be configured.
        if os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") != "1":
            raise unittest.SkipTest("requires explicit disposable PostgreSQL opt-in")
        url = make_url(str(settings.DATABASE_URL))
        local = (
            url.get_backend_name() == "postgresql"
            and url.host == "test-db"
            and url.database == "freshlink_test"
            and settings.PROJECT_NAME == "FreshLink-test"
        )
        hosted = (
            url.get_backend_name() == "postgresql"
            and os.getenv("GITHUB_ACTIONS") == "true"
            and os.getenv("CI") == "true"
            and url.host in {"localhost", "127.0.0.1", "db"}
            and url.database == "app"
            and settings.FASTAPI_ENV == "development"
        )
        if not (local or hosted):
            raise RuntimeError("refusing non-disposable correction test database")

    def setUp(self) -> None:
        # addCleanup also runs if setUp fails after one of the service
        # methods commits, so even a partial test world is removed.
        self._sources: list[str] = []
        self.addCleanup(self._cleanup_world)
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
            session.commit()

    def _cleanup_world(self) -> None:
        # Only IDs created by this test are removed; never clear a shared
        # table or guess at other tests' rows.
        with Session(engine) as session:
            ids = (
                list(
                    session.exec(
                        select(AlertEvent.id).where(
                            col(AlertEvent.source_event_id).in_(self._sources)
                        )
                    ).all()
                )
                if self._sources
                else []
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
            batch_ids = [
                batch_id
                for batch_id in (
                    getattr(self, "bid", None),
                    getattr(self, "other_bid", None),
                )
                if batch_id is not None
            ]
            if batch_ids:
                session.execute(delete(Batch).where(col(Batch.id).in_(batch_ids)))
            warehouse_id = getattr(self, "wid", None)
            if warehouse_id is not None:
                session.execute(delete(Warehouse).where(Warehouse.id == warehouse_id))
            session.commit()

    def _receive(self, payload: CandidateCreate) -> Any:
        self._sources.append(payload.source_event_id)
        with Session(engine) as session:
            receipt = receive_candidate(session, payload)
            session.commit()
            return receipt

    def _recovery_payload(self, related: str) -> CandidateCreate:
        return _candidate(
            source_event_id=f"recovered-{uuid.uuid4()}",
            warehouse_id=self.wid,
            batch_id=self.bid,
            kind=CandidateKind.TEMPERATURE_RECOVERED,
            value=6.0,
            rule_snapshot_id=uuid.UUID("aaaa1111-1111-4111-8111-111111111111"),
            rule_version="v1",
            related_source_event_id=related,
        )

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
            self._receive(self._recovery_payload(source))
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

    def _event_id(self, source: str) -> uuid.UUID:
        with Session(engine) as session:
            row = session.exec(
                select(AlertEvent).where(col(AlertEvent.source_event_id) == source)
            ).first()
            assert row is not None
            return row.id

    def _correction_rows(self, related: str) -> list[AlertEvent]:
        with Session(engine) as session:
            return list(
                session.exec(
                    select(AlertEvent).where(
                        col(AlertEvent.kind) == str(CandidateKind.DATA_QUALITY),
                        col(AlertEvent.related_source_event_id) == related,
                    )
                ).all()
            )

    # -- valid correction, resolved original stays resolved ----------

    def test_correction_after_recovery_keeps_resolved_and_links(self) -> None:
        original = self._seed_original(resolved=True)
        receipt = self._receive(self._correction(original))
        self.assertEqual(str(receipt.status), "accepted_new")
        self.assertIsNotNone(receipt.event_id)
        # History untouched: exactly the recovery's one resolved row.
        self.assertEqual(self._transitions(original), ["resolved"])
        with Session(engine) as session:
            self.assertEqual(
                get_alert_status(session, self._event_id(original)),
                "resolved",
            )

    def test_correction_on_open_alert_does_not_resolve(self) -> None:
        original = self._seed_original(resolved=False)
        receipt = self._receive(self._correction(original))
        self.assertEqual(str(receipt.status), "accepted_new")
        self.assertEqual(self._transitions(original), [])
        with Session(engine) as session:
            self.assertEqual(
                get_alert_status(session, self._event_id(original)),
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

    # -- ordering: correction before / after recovery ------------------

    def test_correction_before_recovery_trace_survives_resolution(self) -> None:
        original = self._seed_original(resolved=False)
        self._receive(self._correction(original))
        # The recovery still resolves the open alert afterwards.
        recovery = self._recovery_payload(original)
        self._receive(recovery)
        with Session(engine) as session:
            self.assertEqual(
                get_alert_status(session, self._event_id(original)),
                "resolved",
            )
        # And the correction trace is NOT erased by the resolution.
        rows = self._correction_rows(original)
        self.assertEqual(len(rows), 1)

    def test_old_recovery_replay_does_not_erase_correction_trace(self) -> None:
        original = self._seed_original(resolved=False)
        recovery = self._recovery_payload(original)
        self._receive(recovery)
        self._receive(self._correction(original))
        # Replay the SAME recovery body (same source id, same digest).
        replay = self._receive(
            recovery.model_copy(update={"source_event_id": recovery.source_event_id})
        )
        self.assertEqual(str(replay.status), "accepted_duplicate")
        self.assertEqual(self._transitions(original), ["resolved"])
        self.assertEqual(len(self._correction_rows(original)), 1)

    # -- invalid links ------------------------------------------------

    def test_unknown_original_is_invalid(self) -> None:
        receipt = self._receive(self._correction(f"missing-{uuid.uuid4()}"))
        self.assertEqual(str(receipt.status), "invalid")
        self.assertEqual(receipt.reason_code, "unknown_related_event")

    def test_cross_batch_link_is_invalid(self) -> None:
        original = self._seed_original(resolved=False)
        wrong = self._correction(original)
        wrong.batch_id = self.other_bid  # same warehouse, other batch
        receipt = self._receive(wrong)
        self.assertEqual(str(receipt.status), "invalid")
        self.assertEqual(receipt.reason_code, "related_event_mismatch")

    def _related_kind_is_invalid_zero_write(self, original: str) -> None:
        """A review 5644143081 P1: the original must be a TEMPERATURE
        anomaly — a recovery, a data_quality event or a humidity
        anomaly can never be the subject of a correction; permanent
        invalid, zero partial writes."""

        before = len(self._correction_rows(original))
        receipt = self._receive(self._correction(original))
        self.assertEqual(str(receipt.status), "invalid")
        self.assertEqual(receipt.reason_code, "related_event_mismatch")
        with Session(engine) as session:
            stored = session.exec(
                select(AlertEvent).where(
                    col(AlertEvent.source_event_id) == receipt.source_event_id
                )
            ).all()
        self.assertEqual(stored, [])  # zero partial writes
        self.assertEqual(len(self._correction_rows(original)), before)

    def test_correction_of_a_recovery_is_invalid(self) -> None:
        original = self._seed_original(resolved=False)
        recovery = self._recovery_payload(original)
        self._receive(recovery)
        self._related_kind_is_invalid_zero_write(recovery.source_event_id)

    def test_correction_of_a_correction_is_invalid(self) -> None:
        original = self._seed_original(resolved=False)
        first = self._correction(original)
        self._receive(first)
        self._related_kind_is_invalid_zero_write(first.source_event_id)

    def test_correction_of_humidity_anomaly_is_invalid(self) -> None:
        humidity_source = f"humidity-{uuid.uuid4()}"
        self._receive(
            _candidate(
                source_event_id=humidity_source,
                warehouse_id=self.wid,
                batch_id=self.bid,
                metric_code="humidity",
                kind=CandidateKind.HUMIDITY_HIGH,
                value=95.0,
                unit="%RH",
                evidence={"comparison": ">", "threshold": 90.0},
            )
        )
        self._related_kind_is_invalid_zero_write(humidity_source)

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

    # -- disposition list carries the optional link (A P2) -------------

    def test_list_summary_carries_related_only_for_corrections(self) -> None:
        from tests.modules.alerts.test_alerts_routes import _client

        client = _client()
        wid = client.post("/warehouses/", json={"code": "WHL", "name": "W"}).json()[
            "id"
        ]
        bid = client.post(
            f"/warehouses/{wid}/batches",
            json={"code": "B001", "product": "x"},
        ).json()["id"]
        anomaly = {
            "source_event_id": "list-anom-1",
            "warehouse_id": wid,
            "batch_id": bid,
            "observation_ids": ["00000000-0000-4000-8000-0000000000aa"],
            "metric_code": "temperature",
            "sampled_at": "2026-10-25T08:00:00Z",
            "value": 11.0,
            "unit": "°C",
            "kind": "temperature_high",
            "severity": "warning",
            "rule_version": "v1",
        }
        self.assertEqual(client.post("/candidates", json=anomaly).status_code, 201)
        correction = {
            "source_event_id": "list-correction-1",
            "warehouse_id": wid,
            "batch_id": bid,
            "observation_ids": ["00000000-0000-4000-8000-0000000000ab"],
            "metric_code": "temperature",
            "sampled_at": "2026-10-25T09:00:00Z",
            "value": None,
            "kind": "data_quality",
            "severity": "info",
            "evidence": {"correction": "late invalid evidence"},
            "related_source_event_id": "list-anom-1",
        }
        self.assertEqual(client.post("/candidates", json=correction).status_code, 201)
        rows = client.get("/alerts/").json()["data"]
        by_source = {row["source_event_id"]: row for row in rows}
        self.assertEqual(
            by_source["list-correction-1"]["related_source_event_id"],
            "list-anom-1",
        )
        # Plain anomalies carry the optional field as None (no link).
        self.assertIsNone(by_source["list-anom-1"]["related_source_event_id"])

    # -- idempotent replay and content conflict ------------------------

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

    def test_correction_same_source_changed_body_conflicts(self) -> None:
        original = self._seed_original(resolved=False)
        payload = self._correction(original)
        source = payload.source_event_id
        self._receive(payload)
        tampered = payload.model_copy(
            update={
                "source_event_id": source,
                "evidence": {"correction": "DIFFERENT body"},
            }
        )
        receipt = self._receive(tampered)
        self.assertEqual(str(receipt.status), "conflict")
        self.assertEqual(receipt.reason_code, "content_digest_mismatch")
        # Exactly one stored correction row; the tampered body lost.
        self.assertEqual(len(self._correction_rows(original)), 1)

    # -- real PG dual-connection interleaving ---------------------------

    def test_correction_serializes_behind_held_original_row_lock(self) -> None:
        """A correction racing a committed recovery cannot regress it.

        Connection 1 holds the original's row lock inside an uncommitted
        transaction (as ACK/recovery do); the correction on connection 2
        blocks on the SAME lock. Connection 1 then commits a recovery
        (resolved). The correction runs afterwards: still accepted, but
        the alert STAYS resolved and no second transition appears.
        """

        original = self._seed_original(resolved=False)
        recovery = self._recovery_payload(original)
        self._sources.append(recovery.source_event_id)
        correction = self._correction(original)
        self._sources.append(correction.source_event_id)

        holder = Session(engine)
        blocked = threading.Event()
        result: dict[str, Any] = {}

        def hold_lock_then_recover() -> None:
            holder.exec(
                select(AlertEvent.id)
                .where(col(AlertEvent.source_event_id) == original)
                .with_for_update()
            ).first()
            blocked.set()
            # Hold the lock a moment so the correction really queues.
            threading.Event().wait(0.4)
            receipt = receive_candidate(holder, recovery)
            result["recovery"] = str(receipt.status)
            holder.commit()
            holder.close()

        worker = threading.Thread(target=hold_lock_then_recover)
        worker.start()
        self.assertTrue(blocked.wait(5.0), "holder never acquired the lock")
        receipt = receive_candidate(Session(engine), correction)
        worker.join(10.0)
        with Session(engine) as session:
            status = get_alert_status(session, self._event_id(original))
            transitions = self._transitions(original)
        self.assertEqual(result.get("recovery"), "accepted_new")
        self.assertEqual(str(receipt.status), "accepted_new")
        self.assertEqual(status, "resolved")
        # The queued correction did not add history or reopen anything.
        self.assertEqual(transitions, ["resolved"])
        self.assertEqual(len(self._correction_rows(original)), 1)


if __name__ == "__main__":
    unittest.main()
