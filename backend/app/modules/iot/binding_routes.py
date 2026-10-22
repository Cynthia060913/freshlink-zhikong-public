"""One-time device binding to a real B warehouse; no silent rebinding."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import SessionDep, get_current_active_superuser
from app.models import User
from app.modules.iot.models import Device, DevicePublic
from app.modules.iot.registry import DeviceBindingError, bind_registered_device
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    warehouse_id: UUID


@router.post("/{device_id}/binding", response_model=DevicePublic)
def bind_device(
    session: SessionDep,
    actor: Annotated[User, Depends(get_current_active_superuser)],
    device_id: UUID,
    request: DeviceBinding,
) -> Device:
    """Same device/warehouse is retry-safe; moving to another returns 409.

    Registration stays unbound. Binding neither claims online nor rewrites old
    heartbeat/outbox/command evidence. A committed write whose refresh fails
    returns retryable 503; resending the same binding safely reads that write.
    """
    try:
        return bind_registered_device(
            session,
            actor=actor,
            device_id=device_id,
            warehouse_id=request.warehouse_id,
            warehouse_exists=warehouse_lookup_for_session(session),
        )
    except DeviceBindingError as error:
        session.rollback()
        reason = str(error)
        status = (
            403
            if reason == "binding_forbidden"
            else 409
            if reason == "binding_conflict"
            else 404
        )
        raise HTTPException(status, detail={"reason_code": reason}) from None
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(
            503,
            detail={"reason_code": "storage_unavailable"},
            headers={"Retry-After": "30"},
        ) from None
