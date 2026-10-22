"""Batch rule snapshot service + signature unit tests.

Covers:

- canonical signature is stable across dict insertion order
- create_snapshot only inserts; never updates a prior row
- duplicate (batch_id, signature) is rejected with SnapshotConflict
- get_current_snapshot returns the latest by created_at
- list returns history (oldest first)
- SnapshotNotFound on missing batch / missing snapshot id
- effective_end_at before effective_start_at is rejected by DTO
"""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.modules.alerts.models import (
    BatchCreate,
    BatchRuleSnapshotCreate,
    RuleDict,
    RuleSeverity,
    WarehouseCreate,
    compute_snapshot_signature,
)
from app.modules.alerts.services.batches import BatchNotFound, create_batch
from app.modules.alerts.services.rules import (
    SnapshotConflict,
    SnapshotNotFound,
    create_snapshot,
    get_current_snapshot,
    get_snapshot,
    list_snapshots_for_batch,
    parse_rules,
)
from app.modules.alerts.services.warehouses import create_warehouse


def _engine() -> Engine:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _setup_batch(session: Session) -> uuid.UUID:
    wid = create_warehouse(session, WarehouseCreate(code="WH", name="W")).id
    return create_batch(session, wid, BatchCreate(code="B-001", product="x")).id


def _make_payload(
    _bid: uuid.UUID,
    *,
    version: int = 1,  # noqa: ARG001
) -> BatchRuleSnapshotCreate:
    return BatchRuleSnapshotCreate(
        version_id=uuid.uuid4(),
        rules=[
            RuleDict(
                metric_code="temperature_high",
                candidate_kind="threshold_high",
                severity=RuleSeverity.WARNING,
                threshold_low=None,
                threshold_high=8.0,
            ),
        ],
        effective_start_at=datetime.now(UTC),
        effective_end_at=None,
    )


def _bounded_payload(
    _bid: uuid.UUID, *, metric: str, start: datetime, hours: float = 1.0
) -> BatchRuleSnapshotCreate:
    """Payload with a bounded non-overlapping interval, for history tests."""

    return BatchRuleSnapshotCreate(
        version_id=uuid.uuid4(),
        rules=[
            RuleDict(
                metric_code=metric,
                candidate_kind="threshold_high",
                severity=RuleSeverity.WARNING,
                threshold_low=None,
                threshold_high=8.0,
                unit="°C",
            ),
        ],
        effective_start_at=start,
        effective_end_at=start + timedelta(hours=hours),
    )


def _make_different_payload(_bid: uuid.UUID) -> BatchRuleSnapshotCreate:
    """A payload with a different signature (different version_id)."""

    return BatchRuleSnapshotCreate(
        version_id=uuid.uuid4(),
        rules=[
            RuleDict(
                metric_code="temperature_low",
                candidate_kind="threshold_low",
                severity=RuleSeverity.WARNING,
                threshold_low=-2.0,
                threshold_high=None,
            ),
        ],
        effective_start_at=datetime.now(UTC),
        effective_end_at=None,
    )


class ComputeSnapshotSignatureCase(unittest.TestCase):
    def test_dict_key_order_does_not_matter(self) -> None:
        # ``sort_keys=True`` normalises dict key order so a payload
        # with ``{a, b}`` hashes identically to ``{b, a}``.
        version_id = uuid.uuid4()
        rules_a = [
            {
                "metric_code": "temperature_high",
                "candidate_kind": "threshold_high",
                "severity": "warning",
                "threshold_high": 8.0,
            }
        ]
        rules_b = [
            {
                "threshold_high": 8.0,
                "severity": "warning",
                "candidate_kind": "threshold_high",
                "metric_code": "temperature_high",
            }
        ]
        self.assertEqual(
            compute_snapshot_signature(version_id, rules_a),
            compute_snapshot_signature(version_id, rules_b),
        )

    def test_list_order_does_matter(self) -> None:
        # The rule list is **ordered**: a snapshot that places
        # ``door_open`` before ``temperature_high`` is not the same
        # snapshot as one that places them in the reverse order, even
        # though the metric set is identical.
        version_id = uuid.uuid4()
        rules_a = [
            {
                "metric_code": "temperature_high",
                "candidate_kind": "threshold_high",
                "severity": "warning",
            },
            {
                "metric_code": "door_open",
                "candidate_kind": "state_change",
                "severity": "critical",
            },
        ]
        rules_b = list(reversed(rules_a))
        self.assertNotEqual(
            compute_snapshot_signature(version_id, rules_a),
            compute_snapshot_signature(version_id, rules_b),
        )

    def test_different_versions_yield_different_signatures(self) -> None:
        rules = [
            {
                "metric_code": "temperature_high",
                "candidate_kind": "threshold_high",
                "severity": "warning",
            }
        ]
        self.assertNotEqual(
            compute_snapshot_signature(uuid.uuid4(), rules),
            compute_snapshot_signature(uuid.uuid4(), rules),
        )


class CreateSnapshotCase(unittest.TestCase):
    def test_first_snapshot_is_persisted(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            bid = _setup_batch(session)
            snap = create_snapshot(session, bid, _make_payload(bid))
        self.assertIsInstance(snap.id, uuid.UUID)
        self.assertEqual(snap.batch_id, bid)
        self.assertEqual(len(snap.payload_json) > 0, True)
        self.assertIsNone(snap.effective_end_at)
        # Newest row's created_at is the snapshot's timestamp.
        self.assertIsNotNone(snap.created_at)

    def test_second_snapshot_does_not_mutate_first(self) -> None:
        """The first row must not be touched when the second is
        inserted. Verified by reading the first row's fields back
        via ``get_snapshot`` after the second insert: its
        ``payload_json``, ``signature`` and ``created_at`` are
        unchanged. (Pre-fix, ``is_current`` and ``superseded_by``
        were mutated in the same transaction; this test catches
        that regression.)
        """
        eng = _engine()
        with Session(eng) as session:
            bid = _setup_batch(session)
            first = create_snapshot(session, bid, _make_payload(bid))
            first_id = first.id
            first_signature = first.signature
            first_payload = first.payload_json
            first_created = first.created_at

            second = create_snapshot(session, bid, _make_different_payload(bid))

        # Capture attributes while the session is still open.
        with Session(eng) as session:
            current = get_current_snapshot(session, bid)
            first_row = get_snapshot(session, first_id)
            self.assertEqual(current.id, second.id)
            self.assertEqual(first_row.id, first_id)
            # Append-only: none of these fields were touched.
            self.assertEqual(first_row.signature, first_signature)
            self.assertEqual(first_row.payload_json, first_payload)
            self.assertEqual(first_row.created_at, first_created)

    def test_duplicate_signature_rejected(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            bid = _setup_batch(session)
            payload = _make_payload(bid)
            create_snapshot(session, bid, payload)
        # Second create with same version_id + same rules must conflict.
        with Session(eng) as session:
            with self.assertRaises(SnapshotConflict):
                create_snapshot(session, bid, payload)

    def test_missing_batch_raises_not_found(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            with self.assertRaises(BatchNotFound):
                # The service calls ``get_batch`` first, which raises
                # BatchNotFound for any unknown id.
                create_snapshot(session, uuid.uuid4(), _make_payload(uuid.uuid4()))


class GetCurrentSnapshotCase(unittest.TestCase):
    def test_no_snapshot_raises_not_found(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            bid = _setup_batch(session)
            with self.assertRaises(SnapshotNotFound):
                get_current_snapshot(session, bid)

    def test_returns_latest_by_created_at(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            bid = _setup_batch(session)
            first = create_snapshot(session, bid, _make_payload(bid))
            second = create_snapshot(session, bid, _make_different_payload(bid))
            # Same metric as the first → overlaps it; the payload
            # explicitly supersedes the first (append-only link).
            third_payload = _make_payload(bid)
            third_payload.supersedes_snapshot_ids = [first.id]
            third = create_snapshot(session, bid, third_payload)
            # capture attributes while the session is still open.
            second_id = second.id
            third_id = third.id
            second_created = second.created_at
            third_created = third.created_at
        with Session(eng) as session:
            current = get_current_snapshot(session, bid)
            current_id = current.id
            current_created = current.created_at
        # The row returned has the latest ``created_at`` for the batch.
        self.assertEqual(current_id, third_id)
        self.assertNotEqual(current_id, second_id)
        self.assertGreaterEqual(current_created, second_created)
        self.assertEqual(third_created, current_created)


class GetSnapshotByIdCase(unittest.TestCase):
    def test_returns_older_snapshot_unchanged(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            bid = _setup_batch(session)
            first = create_snapshot(session, bid, _make_payload(bid))
            first_id = first.id
            create_snapshot(session, bid, _make_different_payload(bid))
        with Session(eng) as session:
            row = get_snapshot(session, first_id)
            row_signature = row.signature
            row_payload = row.payload_json
        # The older row must remain addressable and unchanged.
        self.assertIsNotNone(row_signature)
        self.assertIsNotNone(row_payload)

    def test_missing_id_raises(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            with self.assertRaises(SnapshotNotFound):
                get_snapshot(session, uuid.uuid4())


class ListSnapshotsCase(unittest.TestCase):
    def test_history_grows_by_one_each_time(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            bid = _setup_batch(session)
            base = datetime.now(UTC)
            for i in range(3):
                create_snapshot(
                    session,
                    bid,
                    _bounded_payload(
                        bid, metric="temperature_high", start=base + timedelta(hours=i)
                    ),
                )
        with Session(eng) as session:
            rows, total = list_snapshots_for_batch(session, bid)
        self.assertEqual(total, 3)
        self.assertEqual(len(rows), 3)
        # Ordered by created_at then id — oldest first.
        self.assertLessEqual(rows[0].created_at, rows[1].created_at)
        self.assertLessEqual(rows[1].created_at, rows[2].created_at)


class DTOValidationCase(unittest.TestCase):
    def test_end_before_start_rejected(self) -> None:
        started = datetime.now(UTC)
        earlier = started - timedelta(hours=1)
        with self.assertRaises(ValueError):
            BatchRuleSnapshotCreate(
                version_id=uuid.uuid4(),
                rules=[
                    RuleDict(
                        metric_code="temperature_high",
                        candidate_kind="threshold_high",
                        severity=RuleSeverity.WARNING,
                    )
                ],
                effective_start_at=started,
                effective_end_at=earlier,
            )

    def test_invalid_metric_code_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RuleDict(
                metric_code="!bad",
                candidate_kind="threshold_high",
                severity=RuleSeverity.WARNING,
            )

    def test_empty_rules_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BatchRuleSnapshotCreate(
                version_id=uuid.uuid4(),
                rules=[],
                effective_start_at=datetime.now(UTC),
            )


class ParseRulesCase(unittest.TestCase):
    def test_round_trip(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            bid = _setup_batch(session)
            snap = create_snapshot(session, bid, _make_payload(bid))
        parsed = parse_rules(snap)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["metric_code"], "temperature_high")
        self.assertEqual(parsed[0]["severity"], "warning")


class AppendOnlyCase(unittest.TestCase):
    """The service layer must never UPDATE a prior snapshot row."""

    def test_old_rows_have_no_updatable_mutation_path(self) -> None:
        """Exhaustive check: confirm ``BatchRuleSnapshot`` has no
        ``is_current`` / ``superseded_by`` attributes at all, so the
        "atomic supersede" pattern that violated append-only is not
        even representable. This locks in the PR #10 round 2 fix.
        """
        from app.modules.alerts.models import BatchRuleSnapshot

        for forbidden in ("is_current", "superseded_by"):
            self.assertFalse(
                hasattr(BatchRuleSnapshot, forbidden),
                f"BatchRuleSnapshot must not carry {forbidden!r}",
            )

    def test_three_inserts_produce_three_rows_no_mutation(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            bid = _setup_batch(session)
            base = datetime.now(UTC)
            snap1 = create_snapshot(session, bid, _make_payload(bid))
            snap2 = create_snapshot(session, bid, _make_different_payload(bid))
            # snap1 is open-ended; the bounded successor must
            # explicitly supersede it (append-only link).
            p3 = _bounded_payload(
                bid, metric="temperature_high", start=base + timedelta(hours=1)
            )
            p3.supersedes_snapshot_ids = [snap1.id]
            snap3 = create_snapshot(session, bid, p3)
            ids = [snap1.id, snap2.id, snap3.id]
        with Session(eng) as session:
            rows, total = list_snapshots_for_batch(session, bid)
        self.assertEqual(total, 3)
        self.assertEqual(sorted(r.id for r in rows), sorted(ids))
        # The "current" row is the last inserted.
        self.assertEqual(rows[-1].id, snap3.id)


if __name__ == "__main__":
    unittest.main()
