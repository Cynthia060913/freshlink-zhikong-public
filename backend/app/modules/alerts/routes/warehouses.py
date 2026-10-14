"""Warehouse HTTP routes.

Mounted under ``/warehouses`` and aggregated by
:mod:`app.modules.alerts.routes`. Writes require the active
superuser; reads are available to any active user.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.modules.alerts.models import (
    Warehouse,
    WarehouseCreate,
    WarehousePublic,
)
from app.modules.alerts.services.warehouses import (
    WarehouseCodeConflict,
    WarehouseNotFound,
    create_warehouse,
    get_warehouse,
    list_warehouses,
)

router = APIRouter(prefix="/warehouses", tags=["warehouses"])


@router.post(
    "/",
    response_model=WarehousePublic,
    status_code=201,
    dependencies=[Depends(get_current_active_superuser)],
)
def register_warehouse(session: SessionDep, payload: WarehouseCreate) -> Warehouse:
    try:
        return create_warehouse(session, payload)
    except WarehouseCodeConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Warehouse code already exists: {exc.code}",
        ) from exc


@router.get("/", response_model=list[WarehousePublic])
def list_warehouses_endpoint(
    session: SessionDep,
    _current_user: CurrentUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[WarehousePublic]:
    rows, _total = list_warehouses(session, skip=skip, limit=limit)
    return [WarehousePublic.model_validate(r) for r in rows]


@router.get("/{warehouse_id}", response_model=WarehousePublic)
def read_warehouse(
    session: SessionDep,
    _current_user: CurrentUser,
    warehouse_id: uuid.UUID,
) -> WarehousePublic:
    try:
        warehouse = get_warehouse(session, warehouse_id)
    except WarehouseNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WarehousePublic.model_validate(warehouse)
