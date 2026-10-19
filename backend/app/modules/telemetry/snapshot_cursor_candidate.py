"""Internal signed snapshot cursor; caller-supplied actor is not authentication.

The signing key is injected by the caller and is never generated, logged or
stored here. Production still needs trusted CurrentUser, key derivation,
capacity cleanup, reviewed tables and HTTP error mapping.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Engine

from app.modules.telemetry.history_candidate import HistoryFiltersCandidate
from app.modules.telemetry.snapshot_candidate import (
    _filters_digest,
    _policy,
    read_snapshot_page_candidate,
)

_FIELDS = frozenset({"v", "kid", "snapshot", "actor", "filters", "policy", "after"})


@dataclass(frozen=True)
class SignedSnapshotPageCandidate:
    snapshot_id: UUID
    items: list[dict[str, Any]]
    total: int
    next_cursor: str | None


def _secret(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) < 32:
        raise ValueError("cursor signing key must contain at least 32 bytes")
    return value


def _key_id(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 32:
        raise ValueError("cursor key_id must contain 1-32 characters")
    if not value.isascii() or any(
        not (char.isalnum() or char in "-_") for char in value
    ):
        raise ValueError("cursor key_id must be ASCII letters, digits, - or _")
    return value


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    raw = value.encode("ascii")
    return base64.b64decode(raw + b"=" * (-len(raw) % 4), altchars=b"-_", validate=True)


def _encode(
    *,
    snapshot_id: UUID,
    actor_id: UUID,
    filters: HistoryFiltersCandidate,
    policy_version: str,
    after_ordinal: int,
    signing_key: bytes,
    key_id: str,
) -> str:
    payload = {
        "v": 1,
        "kid": _key_id(key_id),
        "snapshot": str(snapshot_id),
        "actor": str(actor_id),
        "filters": _filters_digest(filters),
        "policy": _policy(policy_version),
        "after": after_ordinal,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(_secret(signing_key), encoded, hashlib.sha256).digest()
    return f"{_b64(encoded)}.{_b64(signature)}"


def _decode(
    token: str,
    *,
    actor_id: UUID,
    filters: HistoryFiltersCandidate,
    policy_version: str,
    signing_key: bytes,
    key_id: str,
) -> tuple[UUID, int]:
    if not isinstance(token, str) or not 1 <= len(token) <= 2048:
        raise ValueError("invalid_snapshot_cursor")
    try:
        encoded_payload, encoded_signature = token.split(".")
        payload_bytes = _unb64(encoded_payload)
        signature = _unb64(encoded_signature)
        expected = hmac.new(
            _secret(signing_key), payload_bytes, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid_signature")
        payload = json.loads(payload_bytes)
        if not isinstance(payload, dict) or set(payload) != _FIELDS:
            raise ValueError("invalid_fields")
        if (
            type(payload["v"]) is not int
            or payload["v"] != 1
            or payload["kid"] != _key_id(key_id)
            or payload["actor"] != str(actor_id)
            or payload["filters"] != _filters_digest(filters)
            or payload["policy"] != _policy(policy_version)
            or isinstance(payload["after"], bool)
            or not isinstance(payload["after"], int)
            or payload["after"] < 0
            or payload["after"] > 9999
        ):
            raise ValueError("invalid_scope")
        return UUID(payload["snapshot"]), payload["after"]
    except (
        UnicodeError,
        binascii.Error,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError("invalid_snapshot_cursor") from exc


def read_signed_snapshot_page_candidate(
    engine: Engine,
    *,
    actor_id: UUID,
    filters: HistoryFiltersCandidate,
    policy_version: str,
    now: datetime,
    signing_key: bytes,
    key_id: str,
    snapshot_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> SignedSnapshotPageCandidate:
    """Page fixed test members with tamper-evident ordinal, not public auth."""
    _secret(signing_key)
    _key_id(key_id)
    if cursor is None:
        if not isinstance(snapshot_id, UUID):
            raise ValueError("snapshot_required")
        selected_snapshot = snapshot_id
        after = -1
    else:
        selected_snapshot, after = _decode(
            cursor,
            actor_id=actor_id,
            filters=filters,
            policy_version=policy_version,
            signing_key=signing_key,
            key_id=key_id,
        )
        if snapshot_id is not None and snapshot_id != selected_snapshot:
            raise ValueError("invalid_snapshot_cursor")
    page = read_snapshot_page_candidate(
        engine,
        snapshot_id=selected_snapshot,
        actor_id=actor_id,
        filters=filters,
        policy_version=policy_version,
        now=now,
        limit=limit,
        after_ordinal=after,
    )
    next_cursor = (
        _encode(
            snapshot_id=selected_snapshot,
            actor_id=actor_id,
            filters=filters,
            policy_version=policy_version,
            after_ordinal=page.next_after_ordinal,
            signing_key=signing_key,
            key_id=key_id,
        )
        if page.next_after_ordinal is not None
        else None
    )
    return SignedSnapshotPageCandidate(
        snapshot_id=selected_snapshot,
        items=page.items,
        total=page.total,
        next_cursor=next_cursor,
    )
