"""Warehouse service unit tests.

Use an in-memory SQLite engine so the tests run without PostgreSQL
fixtures. Pure-Python: no FastAPI, no SMTP, no notification.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.modules.alerts.models import (
    WarehouseCreate,
)
from app.modules.alerts.services.warehouses import (
    WarehouseCodeConflict,
    WarehouseNotFound,
    create_warehouse,
    get_warehouse,
    list_warehouses,
)


def _engine() -> Engine:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


class CreateWarehouseCase(unittest.TestCase):
    def test_creates_and_returns_warehouse(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            warehouse = create_warehouse(
                session, WarehouseCreate(code="WH-001", name="Cold Room A")
            )
        self.assertIsInstance(warehouse.id, uuid.UUID)
        self.assertEqual(warehouse.code, "wh-001")  # normalized
        self.assertEqual(warehouse.name, "Cold Room A")
        self.assertIsNone(warehouse.location)

    def test_normalizes_code_to_lowercase(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            warehouse = create_warehouse(
                session, WarehouseCreate(code="  WH-A  ", name="x")
            )
        self.assertEqual(warehouse.code, "wh-a")

    def test_blank_name_rejected(self) -> None:
        eng = _engine()
        with Session(eng):
            with self.assertRaises(ValueError):
                WarehouseCreate(code="WH", name="   ")

    def test_blank_code_rejected(self) -> None:
        with self.assertRaises(ValueError):
            WarehouseCreate(code="   ", name="x")

    def test_invalid_code_rejected(self) -> None:
        with self.assertRaises(ValueError):
            WarehouseCreate(code="!bad", name="x")


class DuplicateCodeCase(unittest.TestCase):
    def test_duplicate_code_raises_conflict(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            create_warehouse(session, WarehouseCreate(code="WH-X", name="first"))
        with Session(eng) as session:
            with self.assertRaises(WarehouseCodeConflict):
                create_warehouse(session, WarehouseCreate(code="WH-X", name="second"))


class GetWarehouseCase(unittest.TestCase):
    def test_returns_persisted_warehouse(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            created = create_warehouse(session, WarehouseCreate(code="WH-G", name="G"))
        with Session(eng) as session:
            fetched = get_warehouse(session, created.id)
        self.assertEqual(fetched.id, created.id)
        self.assertEqual(fetched.code, "wh-g")

    def test_missing_id_raises_not_found(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            with self.assertRaises(WarehouseNotFound):
                get_warehouse(session, uuid.uuid4())


class ListWarehousesCase(unittest.TestCase):
    def test_pagination_and_count(self) -> None:
        eng = _engine()
        with Session(eng) as session:
            for i in range(5):
                create_warehouse(
                    session,
                    WarehouseCreate(code=f"WH-{i:03d}", name=f"N{i}"),
                )
        with Session(eng) as session:
            page1, total = list_warehouses(session, skip=0, limit=2)
            page2, _ = list_warehouses(session, skip=2, limit=2)
        self.assertEqual(total, 5)
        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 2)
        # Ensure deterministic ordering by created_at + id.
        self.assertLess(page1[0].created_at, page2[0].created_at)


if __name__ == "__main__":
    unittest.main()
