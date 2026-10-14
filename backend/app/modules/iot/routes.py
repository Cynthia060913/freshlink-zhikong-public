import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.modules.iot.models import Device, DeviceCreate, DevicePublic, DevicesPublic

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post(
    "/",
    response_model=DevicePublic,
    status_code=201,
    dependencies=[Depends(get_current_active_superuser)],
)
def register_device(session: SessionDep, device_in: DeviceCreate) -> Device:
    """Register a simulated, unbound device without claiming it is online."""
    device = Device(**device_in.model_dump())
    session.add(device)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        # Confirm a code conflict; do not hide other database integrity errors.
        if session.exec(select(Device).where(Device.code == device_in.code)).first():
            raise HTTPException(status_code=409, detail="Device code already exists")
        raise
    session.refresh(device)
    return device


@router.get("/", response_model=DevicesPublic)
def list_devices(
    session: SessionDep,
    current_user: CurrentUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DevicesPublic:
    """Active authenticated users share read-only access to the contest devices."""
    _ = current_user
    count = session.exec(select(func.count()).select_from(Device)).one()
    records = session.exec(
        select(Device)
        .order_by(col(Device.created_at), col(Device.id))
        .offset(skip)
        .limit(limit)
    ).all()
    return DevicesPublic(
        data=[DevicePublic.model_validate(r) for r in records], count=count
    )


@router.get("/{device_id}", response_model=DevicePublic)
def read_device(
    session: SessionDep,
    current_user: CurrentUser,
    device_id: uuid.UUID,
) -> Device:
    _ = current_user
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return device
