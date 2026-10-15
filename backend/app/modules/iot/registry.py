"""Registration-backed adapter for the provisional A ingress boundary.

The broker adapter must first establish the authenticated UUID using enforced
ACLs/credentials. Warehouse checks are supplied by B's server-side service.
These functions are not mounted as public routes and do not authenticate MQTT.
"""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlmodel import Session, select

from app.models import User
from app.modules.iot.heartbeat import Heartbeat, HeartbeatPolicy, receive_heartbeat
from app.modules.iot.ingress import (
    MAX_PAYLOAD_BYTES,
    DeviceContext,
    IngressRejected,
    ReceptionEnvelope,
    receive_telemetry,
)
from app.modules.iot.models import Device

WarehouseExists = Callable[[UUID], bool]


class DeviceBindingError(ValueError):
    """Fixed codes suitable for translation by a future authenticated route."""


def bind_registered_device(
    session: Session,
    *,
    actor: User,
    device_id: UUID,
    warehouse_id: UUID,
    warehouse_exists: WarehouseExists,
) -> Device:
    """Bind once after a real warehouse check; do not silently move a device.

    actor must come from the existing CurrentUser authentication dependency.
    Rebinding is deferred until in-flight observations/control/history have a
    defined policy. The row lock serializes concurrent first-binding requests.
    The caller supplies a request-scoped session and closes it on errors.
    """
    if not actor.is_active or not actor.is_superuser:
        raise DeviceBindingError("binding_forbidden")
    device = session.exec(
        select(Device)
        .where(Device.id == device_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if device is None:
        raise DeviceBindingError("device_unknown")
    if warehouse_exists(warehouse_id) is not True:
        raise DeviceBindingError("warehouse_unknown")
    if device.warehouse_id is not None and device.warehouse_id != warehouse_id:
        raise DeviceBindingError("binding_conflict")
    device.warehouse_id = warehouse_id
    session.add(device)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(device)
    return device


def receive_registered_telemetry(
    session: Session,
    *,
    authenticated_device_id: UUID,
    credential_enabled: bool,
    warehouse_exists: WarehouseExists,
    topic: str,
    payload: bytes,
    received_at: datetime,
) -> ReceptionEnvelope:
    """Load current identity/binding without trusting device payload claims.

    Configuration/credential revocation is checked by the trusted adapter.
    DB/warehouse-service failures propagate for retry; they are not converted
    into successful receptions or permanent invalid-payload results. This code
    reads registration data only; C still owns observation persistence.
    """
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise IngressRejected("payload_too_large", payload)
    if credential_enabled is not True:
        raise IngressRejected("device_disabled", payload)
    # Re-read even if this Session previously cached the device. Binding changes
    # committed by another request must not be masked by the identity map.
    device = session.exec(
        select(Device)
        .where(Device.id == authenticated_device_id)
        .execution_options(populate_existing=True)
    ).first()
    if device is None:
        raise IngressRejected("device_unknown", payload)
    if device.warehouse_id is None or warehouse_exists(device.warehouse_id) is not True:
        raise IngressRejected("device_unbound", payload)
    return receive_telemetry(
        topic=topic,
        payload=payload,
        context=DeviceContext(device.id, device.warehouse_id),
        received_at=received_at,
    )


def receive_registered_heartbeat(
    session: Session,
    *,
    authenticated_device_id: UUID,
    credential_enabled: bool,
    topic: str,
    payload: bytes,
    received_at: datetime,
    retained: bool,
    policy: HeartbeatPolicy = HeartbeatPolicy(),
) -> Heartbeat:
    """Read current registration, then validate an authenticated heartbeat.

    Broker credentials/ACLs are a trusted adapter prerequisite. Unlike telemetry,
    heartbeat does not require a warehouse: registering a device precedes its
    binding. Explicit payload warehouse claims must still match the current DB
    binding. This read-only step neither updates last_seen nor commits a plan.
    Storage failures propagate; do not turn an unavailable DB into success.
    """
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise IngressRejected("payload_too_large", payload)
    if credential_enabled is not True:
        raise IngressRejected("device_disabled", payload)
    if retained is not False:
        raise IngressRejected("retained_heartbeat", payload)
    device = session.exec(
        select(Device)
        .where(Device.id == authenticated_device_id)
        .execution_options(populate_existing=True)
    ).first()
    if device is None:
        raise IngressRejected("device_unknown", payload)
    return receive_heartbeat(
        topic=topic,
        payload=payload,
        context=DeviceContext(device.id, device.warehouse_id),
        received_at=received_at,
        retained=retained,
        policy=policy,
    )
