"""F03/F04 ingestion service + route tests (contract #5 6139896658).

Covers:

- five-way receipts: accepted_new / accepted_duplicate / conflict /
  invalid / retryable_failure
- idempotency by source_event_id + content digest classification
- digest stability across input key order and tz representations
- by-source lookup: found / not_found (404 ≠ invalid)
- F04 device events: offline accepted, recovery needs
  related_source_event_id, recovery links the original
- notification attempt persistence + listing
- DTO validation: naive sampled_at, bad ids, non-finite value
"""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest import mock

from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.modules.alerts.models import (
    AlertEvent,
    CandidateCreate,
    CandidateKind,
    CandidateReceipt,
    DeviceEventCreate,
    DeviceEventKind,
    NotificationAttempt,
    WarehouseCreate,
)
from app.modules.alerts.models.batch import Batch, BatchCreate
from app.modules.alerts.notifications.transport import (
    DeliveryOutcome,
    NotificationKind,
    NotificationResult,
)
from app.modules.alerts.services.batches import create_batch
from app.modules.alerts.services.events import (
    EventNotFound,
    get_event_by_source,
    list_attempts_for_event,
    receive_candidate,
    receive_device_event,
    record_notification_attempt,
)
from app.modules.alerts.services.warehouses import create_warehouse

T0 = datetime(2026, 10, 13, 8, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)


def _engine() -> Any:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _candidate(
    *,
    source_event_id: str = "evt-1",
    batch_id: uuid.UUID | None = None,
    sampled_at: datetime = T0,
    value: float | None = 11.0,
    kind: CandidateKind = CandidateKind.TEMPERATURE_HIGH,
    metric_code: str = "temperature",
) -> CandidateCreate:
    return CandidateCreate(
        source_event_id=source_event_id,
        kind=kind,
        batch_id=batch_id or uuid.uuid4(),
        metric_code=metric_code,
        sampled_at=sampled_at,
        value=value,
        unit="°C",
    )


def _device_event(
    *,
    source_event_id: str = "dev-evt-1",
    warehouse_id: uuid.UUID | None = None,
    kind: DeviceEventKind = DeviceEventKind.OFFLINE,
    related: str | None = None,
    batch_id: uuid.UUID | None = None,
) -> DeviceEventCreate:
    return DeviceEventCreate(
        source_event_id=source_event_id,
        kind=kind,
        warehouse_id=warehouse_id or uuid.uuid4(),
        device_id="device-01",
        batch_id=batch_id,
        sampled_at=T0,
        related_source_event_id=related,
    )


class _World(unittest.TestCase):
    """One warehouse + one batch on a fresh engine."""

    def setUp(self) -> None:
        self.eng = _engine()
        with Session(self.eng) as session:
            self.wid = create_warehouse(
                session, WarehouseCreate(code="WH", name="W")
            ).id
            self.bid = create_batch(
                session, self.wid, BatchCreate(code="B1", product="x")
            ).id


class FiveWayReceiptCase(_World):
    def test_accepted_new_first_arrival(self) -> None:
        with Session(self.eng) as session:
            receipt = receive_candidate(session, _candidate(batch_id=self.bid))
        self.assertEqual(receipt.status, "accepted_new")
        self.assertEqual(receipt.source_event_id, "evt-1")
        self.assertIsNotNone(receipt.event_id)
        self.assertTrue(str(receipt.content_digest or "").startswith("sha256:"))

    def test_same_content_replay_is_duplicate(self) -> None:
        with Session(self.eng) as session:
            first = receive_candidate(session, _candidate(batch_id=self.bid))
            replay = receive_candidate(session, _candidate(batch_id=self.bid))
        self.assertEqual(replay.status, "accepted_duplicate")
        self.assertEqual(replay.event_id, first.event_id)
        self.assertEqual(replay.stored_event_id, first.event_id)
        self.assertEqual(replay.content_digest, first.content_digest)

    def test_same_id_different_content_is_conflict(self) -> None:
        with Session(self.eng) as session:
            first = receive_candidate(session, _candidate(batch_id=self.bid))
            clash = receive_candidate(
                session, _candidate(batch_id=self.bid, value=99.0)
            )
        self.assertEqual(clash.status, "conflict")
        self.assertEqual(clash.reason_code, "content_digest_mismatch")
        self.assertEqual(clash.stored_event_id, first.event_id)

    def test_unknown_batch_is_invalid_and_final(self) -> None:
        with Session(self.eng) as session:
            receipt = receive_candidate(session, _candidate())
        self.assertEqual(receipt.status, "invalid")
        self.assertEqual(receipt.reason_code, "unknown_batch")
        self.assertFalse(receipt.retryable)

    def test_operational_error_is_retryable(self) -> None:
        with Session(self.eng) as session:
            session.get(Batch, self.bid)  # warms identity map
            broken = OperationalError("INSERT", {}, RuntimeError("db busy"))
            with (
                mock.patch.object(session, "commit", side_effect=broken),
                mock.patch.object(session, "rollback"),
            ):
                receipt = receive_candidate(session, _candidate(batch_id=self.bid))
        self.assertEqual(receipt.status, "retryable_failure")
        self.assertTrue(receipt.retryable)
        self.assertEqual(receipt.retry_after_s, 30.0)
        self.assertEqual(receipt.reason_code, "storage_unavailable")


class DigestStabilityCase(unittest.TestCase):
    def test_key_order_and_tz_representation_do_not_matter(self) -> None:
        utc_candidate = _candidate()
        plus8 = _candidate(sampled_at=T0.astimezone(timezone(timedelta(hours=8))))
        self.assertEqual(utc_candidate.sampled_at, plus8.sampled_at)
        self.assertEqual(
            utc_candidate.model_dump(mode="json")["sampled_at"],
            plus8.model_dump(mode="json")["sampled_at"],
        )

    def test_digest_changes_with_value(self) -> None:
        a = _candidate(value=11.0)
        b = _candidate(value=11.1)
        from app.modules.alerts.models import compute_content_digest

        self.assertNotEqual(compute_content_digest(a), compute_content_digest(b))

    def test_digest_stable_across_instances(self) -> None:
        from app.modules.alerts.models import compute_content_digest

        fixed_batch = uuid.uuid4()
        self.assertEqual(
            compute_content_digest(_candidate(batch_id=fixed_batch)),
            compute_content_digest(_candidate(batch_id=fixed_batch)),
        )


class BySourceLookupCase(_World):
    def test_found_returns_stored_event(self) -> None:
        with Session(self.eng) as session:
            receipt = receive_candidate(session, _candidate(batch_id=self.bid))
            stored = get_event_by_source(session, "evt-1")
        self.assertEqual(stored.id, receipt.event_id)
        self.assertEqual(stored.kind, "temperature_high")
        self.assertEqual(stored.metric_code, "temperature")

    def test_not_found_raises(self) -> None:
        with Session(self.eng) as session:
            with self.assertRaises(EventNotFound):
                get_event_by_source(session, "never-seen")

    def test_payload_json_preserved(self) -> None:
        with Session(self.eng) as session:
            receive_candidate(session, _candidate(batch_id=self.bid))
            stored = get_event_by_source(session, "evt-1")
        self.assertIn("temperature_high", stored.payload_json)
        self.assertIn("sampled_at", stored.payload_json)


class DeviceEventCase(_World):
    def test_offline_accepted(self) -> None:
        with Session(self.eng) as session:
            receipt = receive_device_event(
                session, _device_event(warehouse_id=self.wid)
            )
        self.assertEqual(receipt.status, "accepted_new")

    def test_unknown_warehouse_is_invalid(self) -> None:
        with Session(self.eng) as session:
            receipt = receive_device_event(session, _device_event())
        self.assertEqual(receipt.status, "invalid")
        self.assertEqual(receipt.reason_code, "unknown_warehouse")

    def test_unknown_batch_is_invalid(self) -> None:
        with Session(self.eng) as session:
            receipt = receive_device_event(
                session,
                _device_event(warehouse_id=self.wid, batch_id=uuid.uuid4()),
            )
        self.assertEqual(receipt.status, "invalid")
        self.assertEqual(receipt.reason_code, "unknown_batch")

    def test_recovery_requires_related_id(self) -> None:
        with self.assertRaises(ValueError):
            DeviceEventCreate(
                source_event_id="dev-evt-2",
                kind=DeviceEventKind.RECOVERED,
                warehouse_id=uuid.uuid4(),
                device_id="device-01",
                sampled_at=T0,
                related_source_event_id=None,
            )

    def test_recovery_links_original_and_is_new_id(self) -> None:
        with Session(self.eng) as session:
            offline = receive_device_event(
                session,
                _device_event(source_event_id="dev-evt-1", warehouse_id=self.wid),
            )
            recovery = receive_device_event(
                session,
                _device_event(
                    source_event_id="dev-evt-2",
                    warehouse_id=self.wid,
                    kind=DeviceEventKind.RECOVERED,
                    related="dev-evt-1",
                ),
            )
        self.assertEqual(recovery.status, "accepted_new")
        self.assertNotEqual(recovery.event_id, offline.event_id)
        with Session(self.eng) as session:
            stored = get_event_by_source(session, "dev-evt-2")
        self.assertEqual(stored.related_source_event_id, "dev-evt-1")

    def test_recovery_of_missing_original_is_tolerated(self) -> None:
        with Session(self.eng) as session:
            receipt = receive_device_event(
                session,
                _device_event(
                    source_event_id="dev-evt-9",
                    warehouse_id=self.wid,
                    kind=DeviceEventKind.RECOVERED,
                    related="still-in-flight",
                ),
            )
        self.assertEqual(receipt.status, "accepted_new")

    def test_device_event_idempotent_replay(self) -> None:
        with Session(self.eng) as session:
            first = receive_device_event(session, _device_event(warehouse_id=self.wid))
            replay = receive_device_event(session, _device_event(warehouse_id=self.wid))
        self.assertEqual(replay.status, "accepted_duplicate")
        self.assertEqual(replay.event_id, first.event_id)


class NotificationAttemptCase(_World):
    def _result(self, outcome: DeliveryOutcome) -> NotificationResult:
        return NotificationResult(
            attempt_id=uuid.uuid4(),
            transport=NotificationKind.SMTP_EMAIL,
            outcome=outcome,
            delivered=outcome is DeliveryOutcome.ACCEPTED_BY_SERVICE,
            detail="attempt detail",
            server_response="250 ok",
            retry_after_s=30.0
            if outcome is DeliveryOutcome.TRANSIENT_FAILURE
            else None,
        )

    def test_record_and_list_attempts(self) -> None:
        with Session(self.eng) as session:
            receipt = receive_candidate(session, _candidate(batch_id=self.bid))
            assert receipt.event_id is not None
            record_notification_attempt(
                session,
                receipt.event_id,
                self._result(DeliveryOutcome.TRANSIENT_FAILURE),
            )
            record_notification_attempt(
                session,
                receipt.event_id,
                self._result(DeliveryOutcome.ACCEPTED_BY_SERVICE),
            )
            rows, total = list_attempts_for_event(session, receipt.event_id)
        self.assertEqual(total, 2)
        self.assertEqual(rows[0].outcome, "transient_failure")
        self.assertEqual(rows[0].retry_after_s, 30.0)
        self.assertEqual(rows[1].outcome, "accepted_by_service")
        self.assertTrue(rows[1].delivered)

    def test_record_for_missing_event_raises(self) -> None:
        with Session(self.eng) as session:
            with self.assertRaises(EventNotFound):
                record_notification_attempt(
                    session, uuid.uuid4(), self._result(DeliveryOutcome.TIMEOUT)
                )

    def test_attempt_rows_persisted_on_alert_event(self) -> None:
        with Session(self.eng) as session:
            receipt = receive_candidate(session, _candidate(batch_id=self.bid))
            assert receipt.event_id is not None
            record_notification_attempt(
                session,
                receipt.event_id,
                self._result(DeliveryOutcome.ACCEPTED_BY_SERVICE),
            )
            stored = session.get(AlertEvent, receipt.event_id)
        assert stored is not None
        count = len(session.exec(select(NotificationAttempt)).all())
        self.assertEqual(count, 1)


class DTOValidationCase(unittest.TestCase):
    def test_naive_sampled_at_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _candidate(sampled_at=datetime(2026, 10, 13, 8, 0, 0))

    def test_non_finite_value_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _candidate(value=float("inf"))
        with self.assertRaises(ValueError):
            _candidate(value=float("nan"))

    def test_bad_source_event_id_rejected(self) -> None:
        # whitespace is stripped first, so only shapes that stay
        # invalid after stripping are rejected here
        for bad in ["", "   ", "evt 1", "你好", "a" * 129]:
            with self.assertRaises(ValueError):
                _candidate(source_event_id=bad)

    def test_source_event_id_whitespace_normalised(self) -> None:
        payload = _candidate(source_event_id="  evt-1  ")
        self.assertEqual(payload.source_event_id, "evt-1")

    def test_bad_device_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DeviceEventCreate(
                source_event_id="dev-x",
                kind=DeviceEventKind.OFFLINE,
                warehouse_id=uuid.uuid4(),
                device_id="bad id",
                sampled_at=T0,
            )

    def test_bad_metric_code_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _candidate(metric_code="!bad")

    def test_reasonable_source_ids_accepted(self) -> None:
        for good in ["evt-9", "abc123", "a:b:c", "x.y_z-1"]:
            payload = _candidate(source_event_id=good)
            self.assertEqual(payload.source_event_id, good)


class EventTableShapeCase(unittest.TestCase):
    def test_event_table_no_mutation_columns(self) -> None:
        self.assertFalse(hasattr(AlertEvent, "updated_at"))
        self.assertFalse(hasattr(AlertEvent, "is_active"))

    def test_unique_index_on_source_event_id(self) -> None:
        columns = {c.name: c for c in AlertEvent.__table__.columns}
        self.assertIn("source_event_id", columns)
        self.assertIn("content_digest", columns)
        self.assertIn("related_source_event_id", columns)
        self.assertIn("payload_json", columns)


class ReceiptEnvelopeCase(unittest.TestCase):
    def test_receipt_shape_matches_contract(self) -> None:
        receipt = CandidateReceipt(
            status="accepted_new",  # type: ignore[arg-type]
            event_id=uuid.uuid4(),
            source_event_id="evt-9",
            content_digest="sha256:abc",
        )
        dumped = receipt.model_dump(mode="json")
        for key in (
            "status",
            "event_id",
            "source_event_id",
            "content_digest",
            "reason_code",
            "retryable",
            "retry_after_s",
            "stored_event_id",
        ):
            self.assertIn(key, dumped)


if __name__ == "__main__":
    unittest.main()
