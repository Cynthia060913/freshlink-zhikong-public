"""Provisional RC1 telemetry boundary; not a MQTT listener or durable inbox.

Only a trusted adapter may construct DeviceContext, after broker ACL/identity
checks and a current registration/binding lookup. A topic is NOT authentication.
Quality checks and persistence belong to C; no acknowledgement is emitted here.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

MAX_PAYLOAD_BYTES = 32768
MAX_JSON_DEPTH = 32


@dataclass(frozen=True)
class DeviceContext:
    """Server-owned identity and binding, never populated from message claims."""

    device_id: UUID
    warehouse_id: UUID | None
    registered: bool = True
    enabled: bool = True


@dataclass(frozen=True)
class ReceptionEnvelope:
    raw_text: str
    authenticated_device_id: UUID
    warehouse_id: UUID
    received_at: datetime
    reception_id: UUID

    @property
    def raw_payload(self) -> dict[str, Any]:
        """Return a fresh snapshot so downstream mutation cannot change evidence."""
        result: dict[str, Any] = json.loads(self.raw_text)
        return result


class IngressRejected(ValueError):
    """Bounded diagnostic metadata only; never expose attacker text or topic."""

    def __init__(self, reason_code: str, payload: bytes) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.payload_bytes = len(payload)
        self.payload_sha256 = hashlib.sha256(payload).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-JSON constant: {value}")


def _bounded_depth(value: Any) -> bool:
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, dict | list):
            if depth > MAX_JSON_DEPTH:
                return False
            children = item.values() if isinstance(item, dict) else item
            pending.extend((child, depth + 1) for child in children)
    return True


def _matches_uuid(value: Any, expected: UUID) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return UUID(value) == expected
    except ValueError:
        return False


def receive_telemetry(
    *, topic: str, payload: bytes, context: DeviceContext, received_at: datetime
) -> ReceptionEnvelope:
    """Validate A-owned boundaries without changing C-owned raw observations.

    received_at is supplied by the trusted adapter's clock, not by the device.
    Reuse this returned envelope for storage retries; a network redelivery creates
    a new reception, but keeps the device's original message_id in raw evidence.
    Invalid sampling times/units/values remain available for C's quality checks.
    """
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("received_at must be a server-generated aware timestamp")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise IngressRejected("payload_too_large", payload)
    if not context.registered:
        raise IngressRejected("device_unknown", payload)
    if not context.enabled:
        raise IngressRejected("device_disabled", payload)
    if context.warehouse_id is None:
        raise IngressRejected("device_unbound", payload)
    expected_topic = f"freshlink/v1/devices/{context.device_id}/telemetry"
    if topic != expected_topic:
        raise IngressRejected("topic_mismatch", payload)
    try:
        raw_text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise IngressRejected("invalid_encoding", payload) from None
    try:
        raw = json.loads(
            raw_text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except ValueError, RecursionError:
        raise IngressRejected("invalid_json", payload) from None
    if not isinstance(raw, dict) or not _bounded_depth(raw):
        raise IngressRejected("invalid_payload", payload)
    if raw.get("schema_version") != "1.0":
        raise IngressRejected("invalid_schema", payload)
    if not _matches_uuid(raw.get("device_id"), context.device_id):
        raise IngressRejected("identity_mismatch", payload)
    # Missing warehouse is supplied only in the trusted envelope, never patched
    # into raw. Explicit null or another warehouse is an invalid identity claim.
    if "warehouse_id" in raw and not _matches_uuid(
        raw["warehouse_id"], context.warehouse_id
    ):
        raise IngressRejected("identity_mismatch", payload)
    return ReceptionEnvelope(
        raw_text=raw_text,
        authenticated_device_id=context.device_id,
        warehouse_id=context.warehouse_id,
        received_at=received_at.astimezone(UTC),
        reception_id=uuid4(),
    )
