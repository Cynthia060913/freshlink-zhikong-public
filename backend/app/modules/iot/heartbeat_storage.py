"""Atomic PostgreSQL heartbeat state, replay evidence and event outbox.

Call only from an adapter which has verified broker credentials and ACLs.
Use a dedicated clean Session; no network sends occur in the transaction.
"""

from datetime import datetime
from uuid import UUID

from sqlmodel import Session, select

from app.modules.iot.heartbeat import (
    ConnectivityState,
    HeartbeatPolicy,
    TransitionPlan,
    plan_heartbeat,
    plan_offline,
)
from app.modules.iot.ingress import MAX_PAYLOAD_BYTES, IngressRejected
from app.modules.iot.models import (
    Device,
    DeviceConnectivityEvent,
    HeartbeatConflict,
    HeartbeatMessage,
)
from app.modules.iot.registry import receive_registered_heartbeat


def _require_clean_session(session: Session) -> None:
    if session.new or session.dirty or session.deleted:
        raise ValueError("heartbeat_session_has_pending_changes")


def _locked_device(session: Session, device_id: UUID, payload: bytes = b"") -> Device:
    device = session.exec(
        select(Device)
        .where(Device.id == device_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if device is None:
        raise IngressRejected("device_unknown", payload)
    return device


def _state(device: Device) -> ConnectivityState:
    if device.connectivity == "unknown":
        # Legacy A01 last_seen alone never established an authenticated history.
        return ConnectivityState(device.id, device.warehouse_id)
    if device.connectivity not in {"online", "offline"}:
        raise ValueError("invalid_persistent_connectivity")
    return ConnectivityState(
        device_id=device.id,
        warehouse_id=device.warehouse_id,
        status="online" if device.connectivity == "online" else "offline",
        last_seen_at=device.last_seen_at,
        last_sent_at=device.last_sent_at,
        last_message_id=device.last_heartbeat_message_id,
        offline_source_event_id=device.offline_source_event_id,
    )


def _store_plan(session: Session, device: Device, plan: TransitionPlan) -> None:
    if plan.outcome != "accepted":
        return
    state = plan.state
    device.connectivity = state.status
    device.last_seen_at = state.last_seen_at
    device.last_sent_at = state.last_sent_at
    device.last_heartbeat_message_id = state.last_message_id
    device.offline_source_event_id = state.offline_source_event_id
    session.add(device)
    for event in plan.events:
        session.add(
            DeviceConnectivityEvent(
                source_event_id=event.source_event_id,
                device_id=event.device_id,
                warehouse_id=event.warehouse_id,
                occurred_at=event.occurred_at,
                kind=event.kind,
                reason=event.reason,
                related_source_event_id=event.related_source_event_id,
                delivery_state="held_unbound"
                if event.warehouse_id is None
                else "pending",
            )
        )


def persist_registered_heartbeat(
    session: Session,
    *,
    authenticated_device_id: UUID,
    credential_enabled: bool,
    topic: str,
    payload: bytes,
    received_at: datetime,
    retained: bool,
    policy: HeartbeatPolicy = HeartbeatPolicy(),
) -> TransitionPlan:
    _require_clean_session(session)
    # Reject obvious invalid metadata without taking a DB row lock.
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise IngressRejected("payload_too_large", payload)
    if credential_enabled is not True:
        raise IngressRejected("device_disabled", payload)
    if retained is not False:
        raise IngressRejected("retained_heartbeat", payload)
    try:
        device = _locked_device(session, authenticated_device_id, payload)
        heartbeat = receive_registered_heartbeat(
            session,
            authenticated_device_id=authenticated_device_id,
            credential_enabled=credential_enabled,
            topic=topic,
            payload=payload,
            received_at=received_at,
            retained=retained,
            policy=policy,
        )
        existing = session.get(HeartbeatMessage, (device.id, heartbeat.message_id))
        plan = plan_heartbeat(
            _state(device),
            heartbeat,
            previous_fingerprint=existing.fingerprint if existing else None,
            policy=policy,
        )
        if existing is None:
            session.add(
                HeartbeatMessage(
                    device_id=device.id,
                    message_id=heartbeat.message_id,
                    fingerprint=heartbeat.fingerprint,
                    outcome=plan.outcome,
                    received_at=heartbeat.received_at,
                )
            )
        elif (
            plan.outcome == "conflict"
            and session.get(
                HeartbeatConflict,
                (device.id, heartbeat.message_id, heartbeat.fingerprint),
            )
            is None
        ):
            session.add(
                HeartbeatConflict(
                    device_id=device.id,
                    message_id=heartbeat.message_id,
                    fingerprint=heartbeat.fingerprint,
                    received_at=heartbeat.received_at,
                )
            )
        _store_plan(session, device, plan)
        session.commit()
        return plan
    except Exception:
        session.rollback()
        raise


def scan_registered_device(
    session: Session,
    *,
    device_id: UUID,
    now: datetime,
    policy: HeartbeatPolicy = HeartbeatPolicy(),
) -> TransitionPlan:
    _require_clean_session(session)
    try:
        device = _locked_device(session, device_id)
        state = _state(device)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("scanner_time_must_be_aware")
        # A heartbeat can commit after the scan's start timestamp. That race is
        # not a clock error and must not turn a freshly seen device offline.
        if state.last_seen_at is not None and now < state.last_seen_at:
            plan = TransitionPlan(state, "unchanged")
        else:
            plan = plan_offline(state, now=now, policy=policy)
        _store_plan(session, device, plan)
        session.commit()
        return plan
    except Exception:
        session.rollback()
        raise
