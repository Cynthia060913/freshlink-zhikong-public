"""Authenticated command API using the actual request-scoped B warehouse lookup."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.models import User
from app.modules.iot.control_models import (
    CommandCreate,
    CommandDetail,
    CommandPublic,
    CommandsPublic,
    DeviceCommand,
)
from app.modules.iot.control_storage import ControlError, create_command, get_command
from app.modules.iot.models import Device
from app.modules.iot.registry import WarehouseExists
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session

router = APIRouter(prefix="/devices", tags=["device-control"])


def control_warehouse_lookup(session: SessionDep) -> WarehouseExists:
    return warehouse_lookup_for_session(session)


@router.post("/{device_id}/commands", response_model=CommandPublic, status_code=201)
def queue_command(
    session: SessionDep,
    actor: Annotated[User, Depends(get_current_active_superuser)],
    warehouse_exists: Annotated[WarehouseExists, Depends(control_warehouse_lookup)],
    device_id: UUID,
    request: CommandCreate,
) -> DeviceCommand:
    try:
        return create_command(
            session,
            actor=actor,
            device_id=device_id,
            request=request,
            now=datetime.now(UTC),
            warehouse_exists=warehouse_exists,
        )
    except ControlError as error:
        reason = str(error)
        status = (
            403
            if reason == "control_forbidden"
            else 404
            if reason == "device_unknown"
            else 429
            if reason == "command_capacity_reached"
            else 409
        )
        raise HTTPException(status, detail={"reason_code": reason}) from None
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(
            503,
            detail={"reason_code": "storage_unavailable"},
            headers={"Retry-After": "30"},
        ) from None


@router.get("/{device_id}/commands", response_model=CommandsPublic)
def list_commands(
    session: SessionDep,
    actor: CurrentUser,
    device_id: UUID,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CommandsPublic:
    _ = actor
    if session.get(Device, device_id) is None:
        raise HTTPException(404, detail="Device not found")
    count = session.exec(
        select(func.count())
        .select_from(DeviceCommand)
        .where(DeviceCommand.device_id == device_id)
    ).one()
    rows = session.exec(
        select(DeviceCommand)
        .where(DeviceCommand.device_id == device_id)
        .order_by(
            col(DeviceCommand.issued_at).desc(), col(DeviceCommand.command_id).desc()
        )
        .offset(skip)
        .limit(limit)
    ).all()
    return CommandsPublic(
        data=[CommandPublic.model_validate(r) for r in rows], count=count
    )


@router.get("/{device_id}/commands/{command_id}", response_model=CommandDetail)
def read_command(
    session: SessionDep, actor: CurrentUser, device_id: UUID, command_id: UUID
) -> CommandDetail:
    _ = actor
    command = session.get(DeviceCommand, command_id)
    if command is None or command.device_id != device_id:
        raise HTTPException(404, detail="Command not found")
    result = get_command(session, command_id)
    assert result is not None
    return result
