"""Atomic command queue/dispatch checkpoints and authenticated execution ACKs.

MQTT adapter must verify broker ACLs. No network send happens inside a DB lock.
WarehouseExists must call B's real service in integration, not a client claim.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from app.models import User
from app.modules.iot.control_models import (
    CommandAttempt,
    CommandCreate,
    CommandDetail,
    CommandPublic,
    CommandReceipt,
    DeviceCommand,
    ExecutionAck,
    ReceiptPublic,
)
from app.modules.iot.heartbeat_storage import _require_clean_session
from app.modules.iot.ingress import _reject_constant, _unique_object
from app.modules.iot.models import Device
from app.modules.iot.registry import WarehouseExists
from app.modules.iot.simulator import CoolingCommand


class ControlError(ValueError):
    """Only fixed reason codes, no raw payload or DB/credential details."""


ACTIVE = {"queued", "sent"}


def get_command(session: Session, command_id: UUID) -> CommandDetail | None:
    """Read-only B trace lookup; caller must already enforce user permissions."""
    command = session.get(DeviceCommand, command_id)
    if command is None:
        return None
    receipts = session.exec(
        select(CommandReceipt)
        .where(CommandReceipt.command_id == command_id)
        .order_by(col(CommandReceipt.received_at), col(CommandReceipt.fingerprint))
    ).all()
    return CommandDetail(
        **CommandPublic.model_validate(command).model_dump(),
        receipts=[ReceiptPublic.model_validate(row) for row in receipts],
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ControlError("control_time_requires_timezone")
    return value.astimezone(UTC)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def _device(session: Session, identity: UUID) -> Device:
    device = session.exec(
        select(Device)
        .where(Device.id == identity)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if device is None:
        raise ControlError("device_unknown")
    return device


def _command(session: Session, command_id: UUID) -> DeviceCommand:
    preliminary = session.get(DeviceCommand, command_id)
    if preliminary is None:
        raise ControlError("command_unknown")
    _device(session, preliminary.device_id)
    return session.exec(
        select(DeviceCommand)
        .where(DeviceCommand.command_id == command_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def _authorized(session: Session, actor_id: UUID) -> bool:
    actor = session.exec(
        select(User)
        .where(User.id == actor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    return actor is not None and actor.is_active and actor.is_superuser


def _timeout(command: DeviceCommand, now: datetime) -> None:
    if command.status in ACTIVE and now >= command.expires_at:
        command.status = "timeout"
        command.failure_reason = "execution_deadline_exceeded"
        command.terminal_at = command.expires_at
        command.lease_id = None
        command.lease_until = None


def create_command(
    session: Session,
    *,
    actor: User,
    device_id: UUID,
    request: CommandCreate,
    now: datetime,
    warehouse_exists: WarehouseExists,
) -> DeviceCommand:
    _require_clean_session(session)
    current = _utc(now)
    fingerprint = _digest(
        {
            "actor_id": str(actor.id),
            "device_id": str(device_id),
            **request.model_dump(mode="json"),
        }
    )
    try:
        device = _device(session, device_id)
        if not _authorized(session, actor.id):
            raise ControlError("control_forbidden")
        prior = session.get(DeviceCommand, request.command_id)
        if prior is not None:
            if prior.request_fingerprint != fingerprint:
                raise ControlError("command_id_conflict")
            _timeout(prior, current)
            session.commit()
            session.refresh(prior)
            return prior
        if device.kind != "cooling_actuator":
            raise ControlError("device_not_actuator")
        if (
            device.warehouse_id is None
            or warehouse_exists(device.warehouse_id) is not True
        ):
            raise ControlError("device_unbound")
        if (
            device.connectivity != "online"
            or device.last_seen_at is None
            or not 0 <= (current - device.last_seen_at).total_seconds() < 90
        ):
            raise ControlError("device_not_online")
        active = session.exec(
            select(DeviceCommand).where(
                DeviceCommand.device_id == device_id,
                col(DeviceCommand.status).in_(ACTIVE),
            )
        ).all()
        for pending in active:
            _timeout(pending, current)
        if any(pending.status in ACTIVE for pending in active):
            raise ControlError("device_command_in_flight")
        uncertain = session.exec(
            select(DeviceCommand).where(
                DeviceCommand.device_id == device_id,
                DeviceCommand.status == "failed",
                col(DeviceCommand.expires_at) > current,
                col(DeviceCommand.attempt_count) > 0,
                col(DeviceCommand.ack_received_at).is_(None),
            )
        ).first()
        if uncertain is not None:
            # An uncertain old delivery might still reach the device. Do not
            # issue a contradictory new command while the old one is valid.
            raise ControlError("prior_delivery_uncertain")
        session.flush()  # Release the old partial-unique slot before INSERT.
        count = session.exec(
            select(func.count())
            .select_from(DeviceCommand)
            .where(DeviceCommand.device_id == device_id)
        ).one()
        if count >= 10000:
            raise ControlError("command_capacity_reached")
        payload = CoolingCommand(
            schema_version="1.0",
            command_id=request.command_id,
            device_id=device.id,
            warehouse_id=device.warehouse_id,
            action=request.action,
            parameters={"enabled": request.enabled},
            issued_at=current,
            expires_at=current + timedelta(seconds=request.lifetime_seconds),
        )
        command = DeviceCommand(
            command_id=request.command_id,
            device_id=device.id,
            warehouse_id=device.warehouse_id,
            actor_id=actor.id,
            action=request.action,
            enabled=request.enabled,
            lifetime_seconds=request.lifetime_seconds,
            request_fingerprint=fingerprint,
            payload_text=payload.model_dump_json(),
            issued_at=current,
            expires_at=payload.expires_at,
        )
        session.add(command)
        session.commit()
        session.refresh(command)
        return command
    except IntegrityError:
        session.rollback()
        if session.get(DeviceCommand, request.command_id) is not None:
            raise ControlError("command_id_conflict") from None
        raise
    except Exception:
        session.rollback()
        raise


@dataclass(frozen=True)
class Dispatch:
    command_id: UUID
    attempt_id: UUID
    topic: str
    payload: bytes


def claim_dispatch(
    session: Session,
    *,
    command_id: UUID,
    now: datetime,
    warehouse_exists: WarehouseExists,
) -> Dispatch | None:
    _require_clean_session(session)
    current = _utc(now)
    try:
        command = _command(session, command_id)
        device = _device(session, command.device_id)
        if current < command.issued_at:
            raise ControlError("dispatch_before_issue")
        _timeout(command, current)
        if command.status not in ACTIVE:
            session.commit()
            return None
        if (
            not _authorized(session, command.actor_id)
            or device.warehouse_id != command.warehouse_id
            or warehouse_exists(command.warehouse_id) is not True
        ):
            command.status, command.failure_reason, command.terminal_at = (
                "failed",
                "dispatch_authority_or_binding_invalid",
                current,
            )
            session.commit()
            return None
        if (command.lease_until is not None and current < command.lease_until) or (
            command.next_retry_at is not None and current < command.next_retry_at
        ):
            session.commit()
            return None
        if (
            device.connectivity != "online"
            or device.last_seen_at is None
            or not 0 <= (current - device.last_seen_at).total_seconds() < 90
        ):
            session.commit()
            return None
        if command.attempt_count >= 10:
            command.status, command.failure_reason, command.terminal_at = (
                "failed",
                "dispatch_retry_budget_exhausted",
                current,
            )
            session.commit()
            return None
        attempt = uuid4()
        command.attempt_count += 1
        command.lease_id, command.lease_until = attempt, current + timedelta(seconds=30)
        command.next_retry_at = current + timedelta(
            seconds=min(2 ** (command.attempt_count - 1), 30)
        )
        session.add(
            CommandAttempt(
                attempt_id=attempt, command_id=command_id, started_at=current
            )
        )
        dispatch = Dispatch(
            command_id,
            attempt,
            f"freshlink/v1/devices/{command.device_id}/commands",
            command.payload_text.encode(),
        )
        session.commit()
        return dispatch
    except Exception:
        session.rollback()
        raise


def finish_dispatch(
    session: Session,
    *,
    dispatch: Dispatch,
    outcome: Literal["accepted", "transient", "permanent"],
    now: datetime,
) -> None:
    _require_clean_session(session)
    current = _utc(now)
    if outcome not in {"accepted", "transient", "permanent"}:
        raise ControlError("invalid_dispatch_outcome")
    try:
        command = _command(session, dispatch.command_id)
        attempt = session.get(CommandAttempt, dispatch.attempt_id)
        if attempt is None or attempt.command_id != command.command_id:
            raise ControlError("dispatch_unknown")
        if current < attempt.started_at:
            raise ControlError("dispatch_clock_reversed")
        if attempt.outcome != "pending":
            if attempt.outcome != outcome:
                raise ControlError("dispatch_outcome_conflict")
            session.commit()
            return
        attempt.outcome, attempt.completed_at = outcome, current
        _timeout(command, current)
        current_lease = command.lease_id == attempt.attempt_id
        if current_lease:
            command.lease_id, command.lease_until = None, None
        if outcome == "accepted":
            if command.transport_accepted_at is None:
                command.transport_accepted_at = current
            if command.status == "queued":
                command.status = "sent"
        elif outcome == "permanent" and current_lease and command.status in ACTIVE:
            command.status, command.failure_reason, command.terminal_at = (
                "failed",
                "broker_publish_rejected",
                current,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise


def expire_command(
    session: Session, *, command_id: UUID, now: datetime
) -> DeviceCommand:
    _require_clean_session(session)
    try:
        command = _command(session, command_id)
        _timeout(command, _utc(now))
        session.commit()
        session.refresh(command)
        return command
    except Exception:
        session.rollback()
        raise


def receive_command_ack(
    session: Session,
    *,
    authenticated_device_id: UUID,
    credential_enabled: bool,
    topic: str,
    payload: bytes,
    received_at: datetime,
    retained: bool,
) -> str:
    _require_clean_session(session)
    current = _utc(received_at)
    if (
        credential_enabled is not True
        or retained is not False
        or topic != f"freshlink/v1/devices/{authenticated_device_id}/acks"
        or len(payload) > 4096
    ):
        raise ControlError("invalid_ack_transport")
    try:
        ack = ExecutionAck.model_validate(
            json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        )
    except ValueError, RecursionError:
        raise ControlError("invalid_ack_payload") from None
    if ack.device_id != authenticated_device_id:
        raise ControlError("ack_identity_mismatch")
    canonical = ack.model_dump(mode="json")
    if ack.executed_at is not None:
        canonical["executed_at"] = ack.executed_at.astimezone(UTC).isoformat()
    fingerprint = _digest(canonical)
    try:
        command = _command(session, ack.command_id)
        if command.device_id != authenticated_device_id:
            raise ControlError("ack_command_mismatch")
        if command.attempt_count == 0:
            raise ControlError("ack_before_dispatch")
        if current < command.issued_at:
            raise ControlError("ack_received_before_issue")
        if ack.executed_at is not None and (
            not command.issued_at <= ack.executed_at < command.expires_at
            or ack.executed_at > current + timedelta(seconds=30)
            or ack.state.cooling_enabled != command.enabled
        ):
            raise ControlError("ack_execution_mismatch")
        previous = session.get(CommandReceipt, (command.command_id, fingerprint))
        if previous is not None:
            session.commit()
            return "duplicate"
        count = session.exec(
            select(func.count())
            .select_from(CommandReceipt)
            .where(CommandReceipt.command_id == command.command_id)
        ).one()
        if count >= 32:
            raise ControlError("ack_evidence_capacity_reached")
        _timeout(command, current)
        outcome = (
            "conflict"
            if command.ack_fingerprint is not None
            else "late"
            if command.status not in ACTIVE
            else "accepted"
        )
        session.add(
            CommandReceipt(
                command_id=command.command_id,
                fingerprint=fingerprint,
                received_at=current,
                executed_at=ack.executed_at,
                result=ack.result,
                cooling_enabled=ack.state.cooling_enabled,
                reason=ack.reason,
                outcome=outcome,
            )
        )
        if command.ack_fingerprint is None:
            command.ack_fingerprint = fingerprint
        if outcome == "accepted":
            command.status = "acknowledged" if ack.result == "executed" else "failed"
            command.ack_received_at, command.terminal_at = current, current
            command.failure_reason = ack.reason
            command.lease_id, command.lease_until = None, None
        session.commit()
        return outcome
    except Exception:
        session.rollback()
        raise
