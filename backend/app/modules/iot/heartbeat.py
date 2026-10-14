"""A-owned heartbeat validation and immutable connectivity transition plans.

Not a listener or persistence adapter. The future adapter MUST lock the device,
read a durable message fingerprint, then atomically save this state, fingerprint
and event outbox. Returning a plan is not a database acknowledgement.
"""

import hashlib
import json
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid5

from app.modules.iot.ingress import (
    MAX_PAYLOAD_BYTES,
    DeviceContext,
    IngressRejected,
    _bounded_depth,
    _matches_uuid,
    _reject_constant,
    _unique_object,
)

EVENT_NAMESPACE = UUID("0387bebe-eed2-4ddb-ae46-0dfbd876b43f")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("server/state timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class HeartbeatPolicy:
    offline_after_seconds: int = 90
    maximum_age_seconds: int = 90
    maximum_future_skew_seconds: int = 30

    def __post_init__(self) -> None:
        for value in (
            self.offline_after_seconds,
            self.maximum_age_seconds,
            self.maximum_future_skew_seconds,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("heartbeat durations must be nonnegative integers")
        if self.offline_after_seconds == 0 or self.maximum_age_seconds == 0:
            raise ValueError("offline threshold and maximum age must be positive")


@dataclass(frozen=True)
class Heartbeat:
    device_id: UUID
    message_id: UUID
    sent_at: datetime
    received_at: datetime
    fingerprint: str


@dataclass(frozen=True)
class ConnectivityState:
    device_id: UUID
    warehouse_id: UUID | None = None
    status: Literal["unknown", "online", "offline"] = "unknown"
    last_seen_at: datetime | None = None
    last_sent_at: datetime | None = None
    last_message_id: UUID | None = None
    offline_source_event_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.status not in {"unknown", "online", "offline"}:
            raise ValueError("invalid connectivity status")
        values = (self.last_seen_at, self.last_sent_at, self.last_message_id)
        if self.status == "unknown":
            if any(v is not None for v in values) or self.offline_source_event_id:
                raise ValueError("never-seen state cannot contain heartbeat history")
        elif any(v is None for v in values):
            raise ValueError("established state requires complete heartbeat history")
        if self.status == "offline" and self.offline_source_event_id is None:
            raise ValueError("offline state requires its original event ID")
        if self.status != "offline" and self.offline_source_event_id is not None:
            raise ValueError("only offline state may carry an open offline event")
        for value in (self.last_seen_at, self.last_sent_at):
            if value is not None:
                _utc(value)


@dataclass(frozen=True)
class ConnectivityEvent:
    source_event_id: UUID
    device_id: UUID
    warehouse_id: UUID | None
    occurred_at: datetime
    kind: Literal["offline", "recovered"]
    reason: str
    related_source_event_id: UUID | None = None


@dataclass(frozen=True)
class TransitionPlan:
    state: ConnectivityState
    outcome: Literal["accepted", "duplicate", "conflict", "out_of_order", "unchanged"]
    events: tuple[ConnectivityEvent, ...] = ()


def receive_heartbeat(
    *,
    topic: str,
    payload: bytes,
    context: DeviceContext,
    received_at: datetime,
    retained: bool,
    policy: HeartbeatPolicy = HeartbeatPolicy(),
) -> Heartbeat:
    """Validate trusted adapter metadata; an unbound registered device is allowed.

    Reject retained/stale messages before consulting replay storage. Credentials
    and broker ACLs are external prerequisites, never inferred from the topic.
    Unknown extension fields are included in content identity, not discarded.
    """
    received_at = _utc(received_at)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise IngressRejected("payload_too_large", payload)
    if context.registered is not True:
        raise IngressRejected("device_unknown", payload)
    if context.enabled is not True:
        raise IngressRejected("device_disabled", payload)
    if retained is not False:
        raise IngressRejected("retained_heartbeat", payload)
    if topic != f"freshlink/v1/devices/{context.device_id}/heartbeat":
        raise IngressRejected("topic_mismatch", payload)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise IngressRejected("invalid_encoding", payload) from None
    try:
        raw = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except ValueError, RecursionError:
        raise IngressRejected("invalid_json", payload) from None
    if not isinstance(raw, dict) or not _bounded_depth(raw):
        raise IngressRejected("invalid_payload", payload)
    if raw.get("schema_version") != "1.0":
        raise IngressRejected("invalid_schema", payload)
    if not _matches_uuid(raw.get("device_id"), context.device_id):
        raise IngressRejected("identity_mismatch", payload)
    if "warehouse_id" in raw and (
        context.warehouse_id is None
        or not _matches_uuid(raw["warehouse_id"], context.warehouse_id)
    ):
        raise IngressRejected("identity_mismatch", payload)
    try:
        if not isinstance(raw.get("message_id"), str):
            raise ValueError("message_id")
        message_id = UUID(raw["message_id"])
        timestamp = raw.get("sent_at")
        # datetime.fromisoformat accepts date-only/local forms; require the
        # explicit RFC3339-like date-time separator and timezone here.
        if not isinstance(timestamp, str) or "T" not in timestamp:
            raise ValueError("sent_at")
        sent_at = _utc(datetime.fromisoformat(timestamp))
    except ValueError, TypeError, OverflowError:
        raise IngressRejected("invalid_heartbeat", payload) from None
    age = received_at - sent_at
    if age >= timedelta(seconds=policy.maximum_age_seconds):
        raise IngressRejected("stale_heartbeat", payload)
    if age < -timedelta(seconds=policy.maximum_future_skew_seconds):
        raise IngressRejected("future_heartbeat", payload)
    raw["device_id"] = str(context.device_id)
    raw["message_id"] = str(message_id)
    raw["sent_at"] = sent_at.isoformat()
    if "warehouse_id" in raw:
        raw["warehouse_id"] = str(context.warehouse_id)
    canonical = json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return Heartbeat(
        context.device_id,
        message_id,
        sent_at,
        received_at,
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def plan_offline(
    state: ConnectivityState,
    *,
    now: datetime,
    policy: HeartbeatPolicy = HeartbeatPolicy(),
) -> TransitionPlan:
    """One deterministic offline event per missed-heartbeat interval.

    Repeated/restarted scanners using the same durable state reproduce an event
    ID. Unknown devices never become offline just because they were registered.
    """
    now = _utc(now)
    if state.last_seen_at is None:
        return TransitionPlan(state, "unchanged")
    last_seen_at = _utc(state.last_seen_at)
    if now < last_seen_at:
        raise ValueError("server clock cannot precede last_seen_at")
    deadline = last_seen_at + timedelta(seconds=policy.offline_after_seconds)
    if state.status == "offline" or now < deadline:
        return TransitionPlan(state, "unchanged")
    event_id = uuid5(
        EVENT_NAMESPACE,
        f"offline:{state.device_id}:{state.last_message_id}:{deadline.isoformat()}",
    )
    event = ConnectivityEvent(
        event_id,
        state.device_id,
        state.warehouse_id,
        deadline,
        "offline",
        "heartbeat_timeout",
    )
    return TransitionPlan(
        replace(state, status="offline", offline_source_event_id=event_id),
        "accepted",
        (event,),
    )


def plan_heartbeat(
    state: ConnectivityState,
    heartbeat: Heartbeat,
    *,
    previous_fingerprint: str | None,
    policy: HeartbeatPolicy = HeartbeatPolicy(),
) -> TransitionPlan:
    """Consume the durable lookup for (device_id,message_id), not a local cache.

    Duplicate/conflicting/out-of-order messages cannot renew last_seen. If an
    outage scanner was delayed, infer the offline+recovered pair atomically on
    the next fresh heartbeat rather than silently erasing the missed interval.
    Caller must persist both events and the heartbeat in one transaction.
    """
    if heartbeat.device_id != state.device_id:
        raise ValueError("heartbeat and state device differ")
    if previous_fingerprint is not None:
        outcome: Literal["duplicate", "conflict"] = (
            "duplicate" if previous_fingerprint == heartbeat.fingerprint else "conflict"
        )
        return TransitionPlan(state, outcome)
    received_at, sent_at = _utc(heartbeat.received_at), _utc(heartbeat.sent_at)
    if state.last_seen_at is not None and received_at < _utc(state.last_seen_at):
        return TransitionPlan(state, "out_of_order")
    if state.last_sent_at is not None and sent_at <= _utc(state.last_sent_at):
        return TransitionPlan(state, "out_of_order")
    scan = plan_offline(state, now=received_at, policy=policy)
    current = scan.state
    events = scan.events
    if current.status == "offline":
        original = current.offline_source_event_id
        recovery = ConnectivityEvent(
            uuid5(EVENT_NAMESPACE, f"recovered:{original}:{heartbeat.message_id}"),
            state.device_id,
            state.warehouse_id,
            received_at,
            "recovered",
            "fresh_heartbeat",
            original,
        )
        events += (recovery,)
    return TransitionPlan(
        replace(
            current,
            status="online",
            last_seen_at=received_at,
            last_sent_at=sent_at,
            last_message_id=heartbeat.message_id,
            offline_source_event_id=None,
        ),
        "accepted",
        events,
    )
