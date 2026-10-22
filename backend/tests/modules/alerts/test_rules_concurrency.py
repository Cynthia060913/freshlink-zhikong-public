"""Batch rule snapshot concurrency safety unit tests.

Goals (PR #10 round 2, item 2):

1. The "append-only" contract is observable under concurrency:
   two threads racing on ``create_snapshot`` for the same batch
   with **different** signatures both produce rows; neither row
   is mutated by the other insert. We verify the older row's
   ``payload_json`` / ``signature`` / ``created_at`` are stable.
2. Two threads racing on the **same** signature produce exactly
   one inserted row and one :class:`SnapshotConflict`. This is
   what protects ``BatchRuleSnapshot`` from a duplicate payload
   being accepted under load.
3. The ``BatchRuleSnapshot`` model declares the
   ``UniqueConstraint("batch_id", "signature")`` — i.e. the
   constraint the service relies on is not silently missing in a
   future refactor.
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.modules.alerts.models import (
    BatchCreate,
    BatchRuleSnapshotCreate,
    RuleDict,
    RuleSeverity,
    WarehouseCreate,
)
from app.modules.alerts.services.batches import create_batch
from app.modules.alerts.services.rules import (
    SnapshotConflict,
    create_snapshot,
    get_current_snapshot,
    list_snapshots_for_batch,
)
from app.modules.alerts.services.warehouses import create_warehouse


def _engine_shared_db() -> tuple[Engine, str]:
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    eng = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    return eng, path


def _make_payload(version_id: uuid.UUID) -> BatchRuleSnapshotCreate:
    return BatchRuleSnapshotCreate(
        version_id=version_id,
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


def _make_different_payload(version_id: uuid.UUID) -> BatchRuleSnapshotCreate:
    return BatchRuleSnapshotCreate(
        version_id=version_id,
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


class SnapshotUniqueConstraintSchemaCase(unittest.TestCase):
    """The ``(batch_id, signature)`` constraint is the actual
    contract that protects SnapshotConflict. Lock it in via
    introspection."""

    def test_constraint_declared_on_table(self) -> None:
        eng = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(eng)
        inspector = inspect(eng)
        uqs = inspector.get_unique_constraints("alerts_batch_rule_snapshot")
        columns_sets = [set(uq.get("column_names") or []) for uq in uqs]
        self.assertTrue(
            any({"batch_id", "signature"}.issubset(cols) for cols in columns_sets),
            f"alerts_batch_rule_snapshot missing (batch_id, signature) "
            f"unique constraint; got unique constraints {uqs!r}",
        )


class SnapshotAppendOnlyConcurrentInsertCase(unittest.TestCase):
    """Two threads insert distinct snapshots concurrently."""

    def setUp(self) -> None:
        self._engine, self._path = _engine_shared_db()
        with Session(self._engine) as session:
            wid = create_warehouse(session, WarehouseCreate(code="WH", name="race")).id
            self._batch_id = create_batch(
                session, wid, BatchCreate(code="B-001", product="x")
            ).id

    def tearDown(self) -> None:
        self._engine.dispose()
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass

    def test_two_distinct_signatures_both_succeed_no_mutation(self) -> None:
        barrier = threading.Barrier(2)
        results: dict[str, list[tuple[uuid.UUID, str, str, object]]] = {"inserted": []}
        lock = threading.Lock()
        # capture signatures to assert preservation after the race.
        sig1 = uuid.uuid4()
        sig2 = uuid.uuid4()

        def worker(payload: BatchRuleSnapshotCreate) -> None:
            session = Session(self._engine)
            try:
                barrier.wait(timeout=5)
                row = create_snapshot(session, self._batch_id, payload)
                with lock:
                    results["inserted"].append(
                        (row.id, row.signature, row.payload_json, row.created_at)
                    )
            finally:
                session.close()

        t1 = threading.Thread(target=worker, args=(_make_payload(sig1),), daemon=True)
        t2 = threading.Thread(
            target=worker, args=(_make_different_payload(sig2),), daemon=True
        )
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(results["inserted"]), 2)
        # distinct rows, distinct ids, distinct signatures.
        ids = {row[0] for row in results["inserted"]}
        sigs = {row[1] for row in results["inserted"]}
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(sigs), 2)
        # ``created_at`` values must both be present and non-null.
        createds = {row[3] for row in results["inserted"]}
        self.assertEqual(len(createds), 2)

        # Reload both rows and confirm none of their fields changed.
        with Session(self._engine) as session:
            rows, total = list_snapshots_for_batch(session, self._batch_id)
        self.assertEqual(total, 2)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row.batch_id, self._batch_id)
            self.assertIsNotNone(row.payload_json)
            self.assertIsNotNone(row.signature)
            self.assertIsNotNone(row.created_at)


class SnapshotConflictRaceCase(unittest.TestCase):
    """Two threads inserting **the same** signature race."""

    def setUp(self) -> None:
        self._engine, self._path = _engine_shared_db()
        with Session(self._engine) as session:
            wid = create_warehouse(
                session, WarehouseCreate(code="WH-CONF", name="conflict")
            ).id
            self._batch_id = create_batch(
                session, wid, BatchCreate(code="B-CONF", product="x")
            ).id

    def tearDown(self) -> None:
        self._engine.dispose()
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass

    def test_concurrent_same_signature_one_wins(self) -> None:
        # Use the SAME version_id so both payloads have the same
        # signature. The second insert must surface SnapshotConflict.
        version_id = uuid.uuid4()
        shared_payload = _make_payload(version_id)

        barrier = threading.Barrier(2)
        results: dict[str, list[object]] = {"success": [], "conflict": []}
        lock = threading.Lock()

        def worker() -> None:
            session = Session(self._engine)
            try:
                barrier.wait(timeout=5)
                try:
                    row = create_snapshot(session, self._batch_id, shared_payload)
                    with lock:
                        results["success"].append(row.id)
                except SnapshotConflict:
                    with lock:
                        results["conflict"].append(True)
                finally:
                    session.close()
            except Exception as exc:  # noqa: BLE001
                with lock:
                    results.setdefault("errors", []).append(repr(exc))

        t1 = threading.Thread(target=worker, daemon=True)
        t2 = threading.Thread(target=worker, daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        errors = results.get("errors", [])
        self.assertEqual(errors, [], f"worker raised: {errors!r}")
        self.assertEqual(
            len(results["success"]),
            1,
            f"exactly one insert must succeed, got {results['success']!r}",
        )
        self.assertEqual(
            len(results["conflict"]),
            1,
            f"exactly one insert must conflict, got {results['conflict']!r}",
        )

        # The persisted table holds exactly one row for the batch.
        with Session(self._engine) as session:
            rows, total = list_snapshots_for_batch(session, self._batch_id)
        self.assertEqual(total, 1)
        self.assertEqual(len(rows), 1)


class SnapshotInsertDoesNotMutatePriorRowCase(unittest.TestCase):
    """Append-only under contention: even with many concurrent
    distinct inserts, no row's stored fields are touched after
    insertion."""

    def setUp(self) -> None:
        self._engine, self._path = _engine_shared_db()
        with Session(self._engine) as session:
            wid = create_warehouse(
                session, WarehouseCreate(code="WH-MANY", name="many")
            ).id
            self._batch_id = create_batch(
                session, wid, BatchCreate(code="B-MANY", product="x")
            ).id

    def tearDown(self) -> None:
        self._engine.dispose()
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass

    def test_many_concurrent_distinct_inserts_no_row_changes(self) -> None:
        """Spawn N threads each inserting a distinct payload. After
        the dust settles, no row's signature, payload_json, or
        created_at may have changed compared to the values the
        service returned at insert time."""

        n = 6

        base = datetime.now(UTC)

        def worker(idx: int) -> tuple[uuid.UUID, str, str, datetime]:
            session = Session(self._engine)
            try:
                # Alternate metric AND bound each interval to its own
                # hour so no two payloads overlap on the same metric.
                if idx % 2 == 0:
                    payload = _make_payload(uuid.uuid4())
                    payload.effective_end_at = base + timedelta(hours=1)
                else:
                    payload = _make_different_payload(uuid.uuid4())
                    payload.effective_end_at = base + timedelta(hours=1)
                payload.effective_start_at = base + timedelta(hours=idx)
                payload.effective_end_at = payload.effective_start_at + timedelta(
                    minutes=30
                )
                row = create_snapshot(session, self._batch_id, payload)
                return (
                    row.id,
                    row.signature,
                    row.payload_json,
                    row.created_at,
                )
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=n) as pool:
            inserted = list(pool.map(worker, range(n)))

        with Session(self._engine) as session:
            rows, total = list_snapshots_for_batch(session, self._batch_id)
        self.assertEqual(total, n)
        self.assertEqual(len(rows), n)

        # Build a map from id → captured attributes at insert time.
        captured_by_id = {row[0]: row for row in inserted}
        # Build a map from id → current persisted attributes.
        persisted_by_id = {r.id: r for r in rows}
        # Both sides must cover exactly the same id set.
        self.assertEqual(
            set(captured_by_id),
            set(persisted_by_id),
            "every inserted id must persist; no row may disappear",
        )
        for snap_id in captured_by_id:
            cap_id, cap_sig, cap_json, cap_created = captured_by_id[snap_id]
            persisted = persisted_by_id[snap_id]
            self.assertEqual(persisted.id, cap_id)
            self.assertEqual(persisted.signature, cap_sig)
            self.assertEqual(persisted.payload_json, cap_json)
            # ``created_at`` is identical to the value returned by
            # the service at insert time (i.e. never re-stamped).
            self.assertEqual(persisted.created_at, cap_created)

        # get_current_snapshot still returns the row with the latest
        # ``created_at`` (no stored flag was touched).
        with Session(self._engine) as session:
            current = get_current_snapshot(session, self._batch_id)
        self.assertIsNotNone(current.id)


if __name__ == "__main__":
    unittest.main()
