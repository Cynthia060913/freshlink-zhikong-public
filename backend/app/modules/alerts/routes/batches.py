"""Batch HTTP routes.

Mounted under ``/warehouses/{wid}/batches`` for nested routes and
``/batches/{bid}`` for the direct-by-id read. Batches are write-
guarded like warehouses (superuser only); reads are open to active
users.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.modules.alerts.models import Batch, BatchCreate, BatchPublic
from app.modules.alerts.services.batches import (
    BatchCodeConflict,
    BatchNotFound,
    create_batch,
    get_batch,
    list_batches_for_warehouse,
)
from app.modules.alerts.services.warehouses import WarehouseNotFound

router = APIRouter(tags=["batches"])


@router.post(
    "/warehouses/{warehouse_id}/batches",
    response_model=BatchPublic,
    status_code=201,
    dependencies=[Depends(get_current_active_superuser)],
)
def register_batch(
    session: SessionDep,
    warehouse_id: uuid.UUID,
    payload: BatchCreate,
) -> Batch:
    try:
        return create_batch(session, warehouse_id, payload)
    except WarehouseNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BatchCodeConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Batch code {exc.code!r} already exists for "
                f"warehouse {exc.warehouse_id}"
            ),
        ) from exc


@router.get(
    "/warehouses/{warehouse_id}/batches",
    response_model=list[BatchPublic],
)
def list_batches_endpoint(
    session: SessionDep,
    _current_user: CurrentUser,
    warehouse_id: uuid.UUID,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[BatchPublic]:
    try:
        rows, _total = list_batches_for_warehouse(
            session, warehouse_id, skip=skip, limit=limit
        )
    except WarehouseNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [BatchPublic.model_validate(r) for r in rows]


@router.get("/batches/{batch_id}", response_model=BatchPublic)
def read_batch(
    session: SessionDep,
    _current_user: CurrentUser,
    batch_id: uuid.UUID,
) -> BatchPublic:
    try:
        batch = get_batch(session, batch_id)
    except BatchNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return BatchPublic.model_validate(batch)
