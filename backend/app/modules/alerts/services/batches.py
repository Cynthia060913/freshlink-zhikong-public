"""Batch service layer.

A batch belongs to exactly one warehouse. ``(warehouse_id, code)``
must be unique — the alembic migration will install the constraint;
the service translates a violation into :class:`BatchCodeConflict`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from app.modules.alerts.models import Batch, BatchCreate
from app.modules.alerts.services.warehouses import (
    get_warehouse,
)


class BatchNotFound(Exception):
    """Raised when a batch id does not resolve to a row."""


class BatchCodeConflict(Exception):
    """Raised when ``(warehouse_id, code)`` already exists."""

    def __init__(self, warehouse_id: uuid.UUID, code: str) -> None:
        super().__init__(f"Batch {code!r} already exists for warehouse {warehouse_id}")
        self.warehouse_id = warehouse_id
        self.code = code


def create_batch(
    session: Session,
    warehouse_id: uuid.UUID,
    payload: BatchCreate,
) -> Batch:
    """Insert a new batch under ``warehouse_id``.

    The warehouse must exist (FK integrity); the service layer raises
    :class:`WarehouseNotFound` early so the route layer returns 404.
    """

    get_warehouse(session, warehouse_id)
    data = payload.model_dump()
    # ``Batch.started_at`` is NOT NULL; the DTO accepts ``None`` to
    # signal "use now", which we translate here.
    if data.get("started_at") is None:
        data["started_at"] = datetime.now(UTC)
    batch = Batch(warehouse_id=warehouse_id, **data)
    session.add(batch)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if session.exec(
            select(Batch).where(
                Batch.warehouse_id == warehouse_id,
                Batch.code == payload.code,
            )
        ).first():
            raise BatchCodeConflict(warehouse_id, payload.code) from None
        raise
    session.refresh(batch)
    return batch


def get_batch(session: Session, batch_id: uuid.UUID) -> Batch:
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise BatchNotFound(f"Batch {batch_id} not found")
    return batch


def list_batches_for_warehouse(
    session: Session,
    warehouse_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[Batch], int]:
    """Return (rows, total_count) for batches under one warehouse."""

    # Verify warehouse exists so empty result is unambiguous.
    get_warehouse(session, warehouse_id)
    total = session.exec(
        select(func.count())
        .select_from(Batch)
        .where(Batch.warehouse_id == warehouse_id)
    ).one()
    rows = session.exec(
        select(Batch)
        .where(Batch.warehouse_id == warehouse_id)
        .order_by(col(Batch.created_at), col(Batch.id))
        .offset(skip)
        .limit(limit)
    ).all()
    return list(rows), int(total)
