"""Warehouse service layer.

Pure-Python functions that take a SQLModel Session and return ORM
instances. Route handlers wrap them and translate exceptions into
HTTP responses.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from app.modules.alerts.models import (
    Warehouse,
    WarehouseCreate,
)


class WarehouseNotFound(Exception):
    """Raised when a warehouse id does not resolve to a row."""


class WarehouseCodeConflict(Exception):
    """Raised when :attr:`Warehouse.code` already exists."""

    def __init__(self, code: str) -> None:
        super().__init__(f"Warehouse code already exists: {code}")
        self.code = code


def create_warehouse(session: Session, payload: WarehouseCreate) -> Warehouse:
    """Insert a new warehouse row.

    Translates the unique-code constraint violation into a domain
    exception so the route layer can return HTTP 409.
    """

    warehouse = Warehouse(**payload.model_dump())
    session.add(warehouse)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if session.exec(
            select(Warehouse).where(Warehouse.code == payload.code)
        ).first():
            raise WarehouseCodeConflict(payload.code) from None
        raise
    session.refresh(warehouse)
    return warehouse


def get_warehouse(session: Session, warehouse_id: uuid.UUID) -> Warehouse:
    """Fetch one warehouse by id, or raise :class:`WarehouseNotFound`."""

    warehouse = session.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise WarehouseNotFound(f"Warehouse {warehouse_id} not found")
    return warehouse


def list_warehouses(
    session: Session, *, skip: int = 0, limit: int = 50
) -> tuple[list[Warehouse], int]:
    """Return (rows, total_count) for pagination."""

    total = session.exec(select(func.count()).select_from(Warehouse)).one()
    rows = session.exec(
        select(Warehouse)
        .order_by(col(Warehouse.created_at), col(Warehouse.id))
        .offset(skip)
        .limit(limit)
    ).all()
    return list(rows), int(total)
