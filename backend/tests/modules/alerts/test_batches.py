"""Batch service unit tests.

Same in-memory SQLite pattern as :mod:`test_warehouses`. Covers:
- create + dedup on (warehouse_id, code)
- get / list
- ended_at before started_at rejection
- warehouse missing → WarehouseNotFound
"""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.modules.alerts.models import BatchCreate, WarehouseCreate
from app.modules.alerts.services.batches import (
    BatchCodeConflict,
    BatchNotFound,
    create_batch,
    get_batch,
    list_batches_for_warehouse,
)
from app.modules.alerts.services.warehouses import (
    WarehouseNotFound,
    create_warehouse,
)


def _engine() -> Engine:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _warehouse(session: Session) -> uuid.UUID:
    w = create_warehouse(session, WarehouseCreate(code="WH", name="Warehouse"))
    return w.id


class CreateBatchCase(unittest.TestCase):
    def test_creates_under_warehouse(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            wid = _warehouse(session)
            batch = create_batch(
                session,
                wid,
                BatchCreate(code="B-001", product="frozen-tuna"),
            )
        self.assertEqual(batch.code, "b-001")
        self.assertEqual(batch.product, "frozen-tuna")
        self.assertEqual(batch.warehouse_id, wid)
        self.assertIsNone(batch.ended_at)

    def test_missing_warehouse_raises_not_found(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            with self.assertRaises(WarehouseNotFound):
                create_batch(
                    session,
                    uuid.uuid4(),
                    BatchCreate(code="B-001", product="x"),
                )

    def test_duplicate_code_under_same_warehouse_conflicts(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            wid = _warehouse(session)
            create_batch(
                session,
                wid,
                BatchCreate(code="B-DUP", product="x"),
            )
        with Session(eng) as session:
            with self.assertRaises(BatchCodeConflict):
                create_batch(
                    session,
                    wid,
                    BatchCreate(code="b-dup", product="y"),
                )

    def test_same_code_different_warehouses_allowed(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            wid_a = create_warehouse(session, WarehouseCreate(code="WA", name="A")).id
            wid_b = create_warehouse(session, WarehouseCreate(code="WB", name="B")).id
            create_batch(
                session,
                wid_a,
                BatchCreate(code="B-shared", product="x"),
            )
            other = create_batch(
                session,
                wid_b,
                BatchCreate(code="B-shared", product="y"),
            )
        self.assertEqual(other.warehouse_id, wid_b)

    def test_ended_at_before_started_at_rejected(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            wid = _warehouse(session)
            started = datetime.now(UTC)
            earlier = started - timedelta(hours=1)
            with self.assertRaises(ValueError):
                create_batch(
                    session,
                    wid,
                    BatchCreate(
                        code="B-001",
                        product="x",
                        started_at=started,
                        ended_at=earlier,
                    ),
                )

    def test_blank_product_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BatchCreate(code="B", product="   ")


class GetBatchCase(unittest.TestCase):
    def test_returns_persisted_batch(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            wid = _warehouse(session)
            batch = create_batch(
                session,
                wid,
                BatchCreate(code="B-G", product="x"),
            )
        with Session(eng) as session:
            fetched = get_batch(session, batch.id)
        self.assertEqual(fetched.id, batch.id)

    def test_missing_id_raises_not_found(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            with self.assertRaises(BatchNotFound):
                get_batch(session, uuid.uuid4())


class ListBatchesForWarehouseCase(unittest.TestCase):
    def test_pagination_per_warehouse(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            wid_a = create_warehouse(session, WarehouseCreate(code="WA", name="A")).id
            wid_b = create_warehouse(session, WarehouseCreate(code="WB", name="B")).id
            for i in range(3):
                create_batch(
                    session,
                    wid_a,
                    BatchCreate(code=f"A-{i}", product="x"),
                )
            for i in range(2):
                create_batch(
                    session,
                    wid_b,
                    BatchCreate(code=f"B-{i}", product="x"),
                )

        with Session(eng) as session:
            rows_a, total_a = list_batches_for_warehouse(
                session, wid_a, skip=0, limit=10
            )
            rows_b, total_b = list_batches_for_warehouse(
                session, wid_b, skip=0, limit=10
            )
        self.assertEqual(total_a, 3)
        self.assertEqual(total_b, 2)
        self.assertEqual(len(rows_a), 3)
        self.assertEqual(len(rows_b), 2)

    def test_missing_warehouse_raises(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            with self.assertRaises(WarehouseNotFound):
                list_batches_for_warehouse(session, uuid.uuid4())


if __name__ == "__main__":
    unittest.main()
