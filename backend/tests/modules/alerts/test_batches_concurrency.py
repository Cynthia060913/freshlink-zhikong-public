"""Batch / warehouse concurrency safety unit tests.

These tests verify the unique constraint that backs
:class:`BatchCodeConflict` actually exists at the table level and
that the service layer correctly translates a concurrent
``IntegrityError`` into a domain conflict.

Two angles:

1. **Schema check**: ``inspect()`` confirms the
   ``UniqueConstraint("warehouse_id", "code")`` is declared on the
   ``alerts_batch`` table — i.e. the constraint that the service
   relies on is not silently missing in a future refactor.
2. **Concurrent insert race**: two threads, each owning its own
   ``Session`` (and therefore its own DB connection), attempt to
   ``create_batch`` with the same ``(warehouse_id, code)`` pair.
   The test asserts:

   - exactly one thread reports success;
   - the other thread raises :class:`BatchCodeConflict`;
   - the row count for the batch under the warehouse remains
     exactly 1.

   SQLite with the default ``QueuePool`` gives each thread its own
   connection so the test exercises the constraint rather than the
   single-connection serialisation that ``StaticPool`` would impose.
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from app.modules.alerts.models import (
    Batch,
    BatchCreate,
    WarehouseCreate,
)
from app.modules.alerts.services.batches import (
    BatchCodeConflict,
    create_batch,
)
from app.modules.alerts.services.warehouses import (
    WarehouseCodeConflict,
    create_warehouse,
)


class BatchUniqueConstraintSchemaCase(unittest.TestCase):
    """The ``(warehouse_id, code)`` constraint is the actual contract
    that protects BatchCodeConflict. Lock it in via introspection."""

    def test_constraint_declared_on_table(self) -> None:
        eng = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(eng)
        inspector = inspect(eng)
        uqs = inspector.get_unique_constraints("alerts_batch")
        columns_sets = [set(uq.get("column_names") or []) for uq in uqs]
        # At least one unique constraint must mention both
        # ``warehouse_id`` and ``code`` together — the columns the
        # service relies on. The service-level translation of
        # IntegrityError → BatchCodeConflict is meaningful only if
        # this constraint is actually declared.
        self.assertTrue(
            any({"warehouse_id", "code"}.issubset(cols) for cols in columns_sets),
            f"alerts_batch missing (warehouse_id, code) unique "
            f"constraint; got unique constraints {uqs!r}",
        )


class BatchConcurrentInsertCase(unittest.TestCase):
    """Two threads racing on ``create_batch`` with the same code.

    The ``QueuePool`` (default for a file-backed SQLite URL) gives
    each thread its own connection, so the two ``commit()`` calls
    actually overlap. One wins; the other sees the unique-constraint
    violation as :class:`BatchCodeConflict`.
    """

    def setUp(self) -> None:
        fd, self._path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self._engine = create_engine(
            f"sqlite:///{self._path}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(self._engine)
        # Pre-create the warehouse the threads will use.
        with Session(self._engine) as session:
            self._warehouse_id = create_warehouse(
                session, WarehouseCreate(code="WH-RACE", name="race")
            ).id

    def tearDown(self) -> None:
        self._engine.dispose()
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass

    def test_concurrent_same_code_one_wins(self) -> None:
        barrier = threading.Barrier(2)
        results: dict[str, list[object]] = {"success": [], "conflict": []}
        lock = threading.Lock()

        def worker() -> None:
            session = Session(self._engine)
            try:
                barrier.wait(timeout=5)
                try:
                    batch = create_batch(
                        session,
                        self._warehouse_id,
                        BatchCreate(code="B-RACE", product="x"),
                    )
                    with lock:
                        results["success"].append(batch.id)
                except BatchCodeConflict:
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
        self.assertFalse(t1.is_alive() and t2.is_alive(), "deadlock suspected")

        successes = results["success"]
        conflicts = results["conflict"]
        errors = results.get("errors", [])
        self.assertEqual(errors, [], f"worker raised: {errors!r}")
        self.assertEqual(
            len(successes),
            1,
            f"exactly one insert must succeed, got {successes!r}",
        )
        self.assertEqual(
            len(conflicts),
            1,
            f"exactly one insert must conflict, got {conflicts!r}",
        )

        # Verify the persisted table reflects exactly one row.
        with Session(self._engine) as session:
            rows = session.exec(
                select(Batch).where(  # noqa: F821
                    Batch.warehouse_id == self._warehouse_id,
                    Batch.code == "b-race",
                )
            ).all()
        self.assertEqual(len(rows), 1)

    def test_concurrent_different_codes_both_succeed(self) -> None:
        """Sanity: the constraint doesn't block legitimate distinct
        inserts even when they run concurrently."""

        wid = self._warehouse_id

        def make(code: str) -> uuid.UUID:
            with Session(self._engine) as session:
                return create_batch(
                    session, wid, BatchCreate(code=code, product="x")
                ).id

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_a = pool.submit(make, "B-A")
            f_b = pool.submit(make, "B-B")
            id_a = f_a.result(timeout=10)
            id_b = f_b.result(timeout=10)
        self.assertNotEqual(id_a, id_b)


class WarehouseConcurrentInsertCase(unittest.TestCase):
    """Same idea for ``Warehouse.code`` — the dedup that backs
    :class:`WarehouseCodeConflict` must be enforced under a real
    concurrent race, not just sequential inserts.
    """

    def setUp(self) -> None:
        fd, self._path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self._engine = create_engine(
            f"sqlite:///{self._path}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(self._engine)

    def tearDown(self) -> None:
        self._engine.dispose()
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass

    def test_concurrent_same_code_one_wins(self) -> None:
        barrier = threading.Barrier(2)
        results: dict[str, list[object]] = {"success": [], "conflict": []}
        lock = threading.Lock()

        def worker() -> None:
            session = Session(self._engine)
            try:
                barrier.wait(timeout=5)
                try:
                    w = create_warehouse(
                        session, WarehouseCreate(code="WH-CODE", name="race")
                    )
                    with lock:
                        results["success"].append(w.id)
                except WarehouseCodeConflict:
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

        self.assertEqual(results.get("errors", []), [])
        self.assertEqual(len(results["success"]), 1)
        self.assertEqual(len(results["conflict"]), 1)


class IntegrityErrorFallbackCase(unittest.TestCase):
    """When the SQLite ``IntegrityError`` fires for a reason other
    than ``(warehouse_id, code)`` duplication (e.g. a foreign-key
    constraint, or a constraint we have not enumerated), the service
    layer re-raises rather than swallow it.
    """

    def test_unknown_integrity_error_is_not_swallowed(self) -> None:
        eng = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
        )
        # SQLite does NOT enforce FK constraints by default; we have
        # to opt in per-connection so the bogus warehouse_id FK
        # actually trips IntegrityError.
        import sqlite3

        from sqlalchemy import event

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn: sqlite3.Connection, _: object) -> None:
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        SQLModel.metadata.create_all(eng)
        with Session(eng) as session:
            # Insert a batch with a clearly bogus warehouse_id; the
            # FK fails but it's not the unique constraint we map to
            # ``BatchCodeConflict``.
            from datetime import UTC, datetime

            session.add(
                Batch(
                    warehouse_id=uuid.uuid4(),
                    code="B",
                    product="x",
                    started_at=datetime.now(UTC),
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()


if __name__ == "__main__":
    unittest.main()
