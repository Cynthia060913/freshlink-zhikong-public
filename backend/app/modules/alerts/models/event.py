"""Alert event + notification attempt models (F03 / F04 receive persistence).

Two ingestion shapes share one append-only ``alerts_event`` table:

- ``CandidateCreate``  — C pushes telemetry-derived alert candidates
  (temperature / humidity threshold breaches, data-quality markers).
  Idempotency key is ``source_event_id``; content identity is the
  sha256 digest of the canonical payload JSON.
- ``DeviceEventCreate`` — A pushes device lifecycle events
  (offline / fault / recovered). A recovery carries
  ``related_source_event_id`` pointing at the original event's
  source id; the recovery itself is a brand-new ``source_event_id``.

Five-way receipt contract (pinned in #5 6139896658, B3-3 target):

- ``accepted_new``       — first arrival, row inserted
- ``accepted_duplicate`` — same source_event_id AND same digest
- ``conflict``           — same source_event_id, different digest
- ``invalid``            — explicit semantic rejection (not retriable)
- ``retryable_failure``  — transient storage error (retriable)

``not_found`` on the by-source lookup is NOT ``invalid``: the caller
must retry the same ``source_event_id`` with bounded backoff.

This module is framework-agnostic (no FastAPI imports) so alembic
env.py and unit tests can import it freely.
"""

from __future__ import annotations

import enum
import hashlib
import json
import math
import re
import uuid
from datetime import UTC, datetime

from pydantic import field_validator, model_validator
from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from .rule import _METRIC_CODE_RE

# Stable idempotency key: 1-128 ASCII, starts alnum, then [A-Za-z0-9._:-].
_SOURCE_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class CandidateKind(enum.StrEnum):
    """C-side candidate kinds. ``humidity_*`` values are reserved."""

    TEMPERATURE_HIGH = "temperature_high"
    TEMPERATURE_LOW = "temperature_low"
    HUMIDITY_HIGH = "humidity_high"
    HUMIDITY_LOW = "humidity_low"
    DATA_QUALITY = "data_quality"


class DeviceEventKind(enum.StrEnum):
    """A-side device lifecycle kinds, kept separate from candidates."""

    OFFLINE = "offline"
    FAULT = "fault"
    RECOVERED = "recovered"


class ReceiptStatus(enum.StrEnum):
    """Five-way receipt envelope shared by both ingestion shapes."""

    ACCEPTED_NEW = "accepted_new"
    ACCEPTED_DUPLICATE = "accepted_duplicate"
    CONFLICT = "conflict"
    INVALID = "invalid"
    RETRYABLE_FAILURE = "retryable_failure"


def _require_aware(value: object, field_name: str) -> datetime:
    """Reject naive datetimes, normalize aware ones to UTC."""

    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must carry an explicit timezone")
    return value.astimezone(UTC)


def _validated_source_event_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("source_event_id must be text")
    stripped = value.strip()
    if not _SOURCE_EVENT_ID_RE.fullmatch(stripped):
        raise ValueError(
            "source_event_id must be 1-128 ASCII letters, digits, "
            "'.', '_', ':' or '-', starting with a letter or digit"
        )
    return stripped


def _validated_device_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("device_id must be text")
    stripped = value.strip()
    if not _DEVICE_ID_RE.fullmatch(stripped):
        raise ValueError(
            "device_id must be 1-64 ASCII letters, digits, '.', '_' or "
            "':', starting with a letter or digit"
        )
    return stripped


def _finite_or_none(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number or null")
    as_float = float(value)
    if not math.isfinite(as_float):
        raise ValueError(f"{field_name} must be finite")
    return as_float


def canonical_payload_json(payload: SQLModel) -> str:
    """Deterministic JSON used for both the digest and audit storage.

    Field order is normalized via ``sort_keys``; datetimes were already
    normalized to UTC by the DTO validators, so the same logical event
    always digests identically regardless of input key order.
    """

    data = payload.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_content_digest(payload: SQLModel) -> str:
    """``sha256:<hex>`` of the canonical payload JSON."""

    raw = canonical_payload_json(payload).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


class CandidateCreate(SQLModel):
    """Payload accepted by ``POST /candidates`` (pushed by C)."""

    source_event_id: str = Field(min_length=1, max_length=128)
    kind: CandidateKind
    batch_id: uuid.UUID
    metric_code: str = Field(min_length=1, max_length=64)
    sampled_at: datetime
    value: float | None = None
    unit: str | None = Field(default=None, max_length=8)

    @field_validator("source_event_id", mode="before")
    @classmethod
    def _norm_source_event_id(cls, value: object) -> str:
        return _validated_source_event_id(value)

    @field_validator("metric_code", mode="before")
    @classmethod
    def _norm_metric_code(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("metric_code must be text")
        stripped = value.strip()
        if not _METRIC_CODE_RE.fullmatch(stripped):
            raise ValueError("metric_code must match the shared metric code pattern")
        return stripped

    @field_validator("sampled_at", mode="before")
    @classmethod
    def _norm_sampled_at(cls, value: object) -> datetime:
        return _require_aware(value, "sampled_at")

    @field_validator("value", mode="before")
    @classmethod
    def _norm_value(cls, value: object) -> float | None:
        return _finite_or_none(value, "value")

    @field_validator("unit", mode="before")
    @classmethod
    def _norm_unit(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("unit must be text")
        stripped = value.strip()
        return stripped or None


class DeviceEventCreate(SQLModel):
    """Payload accepted by ``POST /device-events`` (pushed by A)."""

    source_event_id: str = Field(min_length=1, max_length=128)
    kind: DeviceEventKind
    warehouse_id: uuid.UUID
    device_id: str = Field(min_length=1, max_length=64)
    batch_id: uuid.UUID | None = None
    sampled_at: datetime
    related_source_event_id: str | None = Field(default=None, max_length=128)

    @field_validator("source_event_id", mode="before")
    @classmethod
    def _norm_source_event_id(cls, value: object) -> str:
        return _validated_source_event_id(value)

    @field_validator("device_id", mode="before")
    @classmethod
    def _norm_device_id(cls, value: object) -> str:
        return _validated_device_id(value)

    @field_validator("sampled_at", mode="before")
    @classmethod
    def _norm_sampled_at(cls, value: object) -> datetime:
        return _require_aware(value, "sampled_at")

    @field_validator("related_source_event_id", mode="before")
    @classmethod
    def _norm_related(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("related_source_event_id must be text")
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _recovery_needs_related(self) -> DeviceEventCreate:
        if self.kind is DeviceEventKind.RECOVERED and not self.related_source_event_id:
            raise ValueError(
                "recovered device events must carry related_source_event_id"
            )
        return self


class AlertEvent(SQLModel, table=True):
    """Append-only ingested event row (candidate or device event).

    ``source_event_id`` is the idempotency key (unique); content
    identity is ``content_digest``. Rows are never UPDATEd or DELETEd;
    a correction is a new event.
    """

    __tablename__ = "alerts_event"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source_event_id: str = Field(unique=True, index=True, max_length=128)
    kind: str = Field(max_length=32)
    batch_id: uuid.UUID | None = Field(
        default=None, foreign_key="alerts_batch.id", index=True
    )
    warehouse_id: uuid.UUID | None = Field(
        default=None, foreign_key="alerts_warehouse.id", index=True
    )
    device_id: str | None = Field(default=None, max_length=64)
    metric_code: str | None = Field(default=None, max_length=64)
    sampled_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    value: float | None = None
    unit: str | None = Field(default=None, max_length=8)
    content_digest: str = Field(max_length=71)
    payload_json: str
    related_source_event_id: str | None = Field(default=None, max_length=128)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CandidateReceipt(SQLModel):
    """Uniform five-way receipt envelope (all ingestion outcomes)."""

    status: ReceiptStatus
    event_id: uuid.UUID | None = None
    source_event_id: str
    content_digest: str | None = None
    reason_code: str | None = None
    retryable: bool = False
    retry_after_s: float | None = None
    stored_event_id: uuid.UUID | None = None
    created_at: datetime | None = None


class AlertEventPublic(SQLModel):
    """Read-only shape returned by the by-source lookup."""

    id: uuid.UUID
    source_event_id: str
    kind: str
    batch_id: uuid.UUID | None
    warehouse_id: uuid.UUID | None
    device_id: str | None
    metric_code: str | None
    sampled_at: datetime
    value: float | None
    unit: str | None
    content_digest: str
    related_source_event_id: str | None
    created_at: datetime


class NotificationAttempt(SQLModel, table=True):
    """One ``send`` attempt against one alert event, for audit/retry.

    Written by the dispatch layer after a transport returns a
    :class:`~app.modules.alerts.notifications.transport.NotificationResult`;
    rows are append-only.
    """

    __tablename__ = "alerts_notification_attempt"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    alert_event_id: uuid.UUID = Field(foreign_key="alerts_event.id", index=True)
    channel: str = Field(max_length=32)
    outcome: str = Field(max_length=32)
    delivered: bool
    detail: str | None = Field(default=None, max_length=1000)
    server_response: str | None = Field(default=None, max_length=1000)
    retry_after_s: float | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class NotificationAttemptPublic(SQLModel):
    """Read-only shape for attempt listings."""

    id: uuid.UUID
    alert_event_id: uuid.UUID
    channel: str
    outcome: str
    delivered: bool
    detail: str | None
    server_response: str | None
    retry_after_s: float | None
    created_at: datetime
