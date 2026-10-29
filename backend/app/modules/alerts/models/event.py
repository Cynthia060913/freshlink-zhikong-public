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
from typing import Any, Literal

from pydantic import field_validator, model_validator
from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from .rule import _METRIC_CODE_RE

# Stable idempotency key: 1-128 ASCII, starts alnum, then [A-Za-z0-9._:-].
_SOURCE_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

# F03-RC2 input bounds (contracts/F03-RC2.md): at most 64 observation
# refs per candidate and 32768 bytes for the canonical payload; larger
# inputs are rejected explicitly, never truncated.
_MAX_OBSERVATION_IDS = 64
_MAX_PAYLOAD_BYTES = 32768
_CANDIDATE_SCHEMA_VERSION = "1.0"

# F03 severity is a CLOSED set aligned with :class:`RuleSeverity`
# (info / warning / critical). Free text was a round-3 review defect
# (PR #10 review 5535120012 item 3): B's alert records and digest
# round-trip must never carry an unknown tier, and C gets a stable
# rejection instead of silently stored garbage.
SeverityLevel = Literal["info", "warning", "critical"]


# Alert lifecycle states (S0 f98 §3 decision 3). The event row itself
# is append-only; lifecycle lives in a separate append-only
# transition table, current status = latest transition's to_status
# (``open`` when no transition exists yet). StrEnum (not Literal) so
# it can back both table columns and JSON DTOs.
class AlertStatus(enum.StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class CandidateKind(enum.StrEnum):
    """C-side candidate kinds. ``humidity_*`` values are reserved."""

    TEMPERATURE_HIGH = "temperature_high"
    TEMPERATURE_LOW = "temperature_low"
    HUMIDITY_HIGH = "humidity_high"
    HUMIDITY_LOW = "humidity_low"
    DATA_QUALITY = "data_quality"
    # S0 f98 §3 decision 3: C posts a recovery as a brand-new
    # source_event_id whose related_source_event_id points at the
    # original anomaly; B resolves the alert in the same transaction.
    TEMPERATURE_RECOVERED = "temperature_recovered"


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
    """Reject naive datetimes, normalize aware ones to UTC.

    HTTP JSON arrives as ISO-8601 text. A ``mode="before"`` validator
    runs before pydantic's own datetime parsing, so text input must be
    parsed here or the whole JSON path breaks with a misleading
    "must be a datetime" 422 (PR #10 defect 1; F03-RC2 requires both
    ISO strings and internal datetimes). Naive values are never
    guessed — they are rejected.
    """

    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                f"{field_name} must be an ISO 8601 datetime with timezone"
            ) from exc
        value = parsed
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


def _strip_or_none(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    stripped = value.strip()
    return stripped or None


def canonical_payload_json(payload: SQLModel) -> str:
    """Deterministic JSON used for both the digest and audit storage.

    Field order is normalized via ``sort_keys``; datetimes were already
    normalized to UTC by the DTO validators, so the same logical event
    always digests identically regardless of input key order.

    Compat (PR #10 reviews 5563581508 / 5563695226): a NULL
    ``related_source_event_id`` on a CANDIDATE stays OUT of the
    canonical payload. The field was added to ``CandidateCreate`` for
    recoveries only, and ``model_dump`` emitting it as ``null`` for
    every ordinary candidate changed the digest of bodies that had
    already been received and stored — replaying the exact old body
    against an old row answered ``conflict`` instead of
    ``accepted_duplicate`` (A's cross-version probe). Per the #5
    compatibility rule we keep the pre-recovery field set for
    empty-related candidates and do NOT rewrite old rows, source ids
    or stored digests. A NON-EMPTY related id (recovery candidates)
    IS part of the digest. ``DeviceEventCreate`` is untouched: it
    always carried the field, so its digests are unchanged either way —
    the exclusion is deliberately scoped to candidates instead of a
    global ``exclude_none`` (which would silently alter every other
    nullable field's digest).
    """

    data = payload.model_dump(mode="json")
    if (
        isinstance(payload, CandidateCreate)
        and data.get("related_source_event_id") is None
    ):
        data.pop("related_source_event_id", None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_content_digest(payload: SQLModel) -> str:
    """``sha256:<hex>`` of the canonical payload JSON."""

    raw = canonical_payload_json(payload).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


class CandidateCreate(SQLModel):
    """Payload accepted by ``POST /candidates`` (pushed by C).

    F03-RC2 (contracts/F03-RC2.md) shape: alongside the core
    measurement the candidate carries the full traceability body —
    ``observation_ids``, ``rule_snapshot_id`` / ``rule_version``,
    ``algorithm_version``, ``severity`` and free-form ``evidence``.
    Every field is part of the canonical payload, so it is covered
    by the content digest: replaying the same ``source_event_id``
    with different traceability is a ``conflict``, never a
    ``duplicate`` (PR #10 defect 2).
    """

    schema_version: str = _CANDIDATE_SCHEMA_VERSION
    source_event_id: str = Field(min_length=1, max_length=128)
    kind: CandidateKind
    warehouse_id: uuid.UUID
    batch_id: uuid.UUID
    observation_ids: list[str] = Field(default_factory=list)
    metric_code: str = Field(min_length=1, max_length=64)
    sampled_at: datetime
    value: float | None = None
    unit: str | None = Field(default=None, max_length=8)
    severity: SeverityLevel
    rule_snapshot_id: uuid.UUID | None = None
    rule_version: str | None = Field(default=None, max_length=64)
    algorithm_version: str | None = Field(default=None, max_length=64)
    evidence: dict[str, Any] | None = None
    # Recovery candidates only (S0 f98 §3 decision 3): points at the
    # ORIGINAL anomaly's source_event_id; part of the canonical
    # payload and therefore of the digest.
    related_source_event_id: str | None = Field(default=None, max_length=128)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _norm_schema_version(cls, value: object) -> str:
        if value is None:
            return _CANDIDATE_SCHEMA_VERSION
        if value != _CANDIDATE_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {_CANDIDATE_SCHEMA_VERSION!r}")
        return str(value)

    @field_validator("source_event_id", mode="before")
    @classmethod
    def _norm_source_event_id(cls, value: object) -> str:
        return _validated_source_event_id(value)

    @field_validator("observation_ids", mode="before")
    @classmethod
    def _norm_observation_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("observation_ids must be a list")
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("each observation id must be text")
            stripped = item.strip()
            if not stripped or len(stripped) > 128:
                raise ValueError("each observation id must be 1-128 characters")
            cleaned.append(stripped)
        if len(cleaned) > _MAX_OBSERVATION_IDS:
            raise ValueError(
                f"observation_ids must hold at most {_MAX_OBSERVATION_IDS} ids"
            )
        return cleaned

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

    @field_validator("severity", mode="before")
    @classmethod
    def _norm_severity(cls, value: object) -> SeverityLevel:
        if not isinstance(value, str):
            raise ValueError("severity must be text")
        stripped = value.strip().lower()
        # Explicit literal returns instead of a cast: mypy does not
        # narrow ``str`` through a tuple-membership check while ty
        # flags the cast as redundant — this satisfies both.
        if stripped == "info":
            return "info"
        if stripped == "warning":
            return "warning"
        if stripped == "critical":
            return "critical"
        raise ValueError("severity must be one of: info, warning, critical")

    @field_validator("rule_version", mode="before")
    @classmethod
    def _norm_rule_version(cls, value: object) -> str | None:
        return _strip_or_none(value, "rule_version")

    @field_validator("algorithm_version", mode="before")
    @classmethod
    def _norm_algorithm_version(cls, value: object) -> str | None:
        return _strip_or_none(value, "algorithm_version")

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
    def _recovery_kind_and_related(self) -> CandidateCreate:
        if self.kind is CandidateKind.TEMPERATURE_RECOVERED and not (
            self.related_source_event_id
        ):
            raise ValueError(
                "temperature_recovered candidates must carry related_source_event_id"
            )
        if self.related_source_event_id and self.kind not in (
            CandidateKind.TEMPERATURE_RECOVERED,
            CandidateKind.DATA_QUALITY,
        ):
            raise ValueError(
                "related_source_event_id is only valid on temperature_recovered "
                "and on data_quality correction events"
            )
        return self

    @model_validator(mode="after")
    def _payload_within_bound(self) -> CandidateCreate:
        encoded = canonical_payload_json(self).encode("utf-8")
        if len(encoded) > _MAX_PAYLOAD_BYTES:
            raise ValueError("canonical payload exceeds the 32768-byte F03-RC2 bound")
        return self


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
    identity is ``content_digest``. Rows are never updated or deleted;
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
    severity: str | None = Field(default=None, max_length=32)
    observation_ids_json: str | None = Field(default=None, max_length=8192)
    rule_snapshot_id: uuid.UUID | None = Field(default=None, index=True)
    rule_version: str | None = Field(default=None, max_length=64)
    algorithm_version: str | None = Field(default=None, max_length=64)
    evidence_json: str | None = Field(default=None)
    content_digest: str = Field(max_length=71)
    payload_json: str
    related_source_event_id: str | None = Field(default=None, max_length=128)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AlertStatusTransition(SQLModel, table=True):
    """One append-only alert lifecycle transition (S0 f98 §3 decision 3).

    ``alerts_event`` rows are immutable; the lifecycle of the ALERT an
    event opened lives here. Current status = latest transition's
    ``to_status`` by ``(created_at, id)``; no transition row means
    ``open``. A recovery candidate commits its own event row and the
    original alert's ``open/acknowledged -> resolved`` transition in
    the SAME transaction; history (including the original anomaly and
    this audit trail) is never rewritten or deleted, and a machine
    ``resolved`` is not the same thing as a human ``closed``.
    """

    __tablename__ = "alerts_status_transition"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    alert_event_id: uuid.UUID = Field(foreign_key="alerts_event.id", index=True)
    from_status: AlertStatus
    to_status: AlertStatus
    # source_event_id of the event that caused the transition (e.g.
    # the recovery candidate), for traceability back to the trigger.
    trigger_source_event_id: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=128)
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
    """Read-only shape returned by the by-source lookup.

    F03-RC2: the lookup must return the full stored traceability body
    (observation refs, rule/algorithm identity, evidence), not just
    the event id and value.
    """

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
    severity: str | None
    observation_ids: list[str] = Field(default_factory=list)
    rule_snapshot_id: uuid.UUID | None = None
    rule_version: str | None = None
    algorithm_version: str | None = None
    evidence: dict[str, Any] | None = None
    content_digest: str
    related_source_event_id: str | None
    # Current lifecycle status of the alert this event belongs to
    # (S0 f98 §3; computed from the transition table, "open" when no
    # transition exists). Lets C verify the original alert actually
    # reached ``resolved`` after posting a recovery.
    status: AlertStatus = AlertStatus.OPEN
    created_at: datetime


class AlertEventSummary(SQLModel):
    """One row of the disposition list (anomaly events only).

    Recovery candidates and device lifecycle events are NOT alerts
    themselves; they are traceability/ingestion records. The list
    carries the current lifecycle ``status`` so the console can filter
    by ``open / acknowledged / resolved`` without fetching each
    alert's history.
    """

    alert_event_id: uuid.UUID
    source_event_id: str
    # Optional traceability link (#5 6394581672, A review 5644143081
    # P2, PR #83): a data_quality correction names the original anomaly
    # it corrects. Read-only display field — no state machine change,
    # no history rewrite.
    related_source_event_id: str | None
    kind: str
    severity: str | None
    warehouse_id: uuid.UUID | None
    batch_id: uuid.UUID | None
    device_id: str | None
    metric_code: str | None
    sampled_at: datetime
    value: float | None
    unit: str | None
    status: AlertStatus
    created_at: datetime


class AlertEventsPublic(SQLModel):
    """Paginated alert list envelope (data + total count)."""

    data: list[AlertEventSummary]
    count: int


class AlertStatusTransitionPublic(SQLModel):
    """Read-only shape of one lifecycle transition (append-only audit)."""

    id: uuid.UUID
    alert_event_id: uuid.UUID
    from_status: AlertStatus
    to_status: AlertStatus
    trigger_source_event_id: str | None = None
    reason: str | None = None
    created_at: datetime


class AlertLifecyclePublic(SQLModel):
    """Current status plus the full ordered transition history."""

    alert_event_id: uuid.UUID
    status: AlertStatus
    transition_created: bool = False
    transitions: list[AlertStatusTransitionPublic] = Field(default_factory=list)


class NotificationAttempt(SQLModel, table=True):
    """One ``send`` attempt against one alert event, for audit/retry.

    Written by the dispatch layer after a transport returns a
    :class:`~app.modules.alerts.notifications.transport.NotificationResult`;
    rows are append-only.

    ``outcome`` normally carries the four ``DeliveryOutcome`` values;
    the dispatch layer additionally persists two fixed sentinels. A row
    created as ``send_intent`` is the pre-send dispatch intent
    ("预占"): written in its own transaction BEFORE any network call,
    then completed forward one-way (exact row id, unfinished state
    only) to the final result; an UNFINISHED ``send_intent`` row means
    the send started but its final state was never persisted — every
    later dispatch and pending scan holds the channel for manual
    verification instead of resending (A review 5682106843). A row
    completed as ``acceptance_unknown`` means the send's acceptance
    could not be proven (connection broke mid-DATA): such a channel is
    likewise held and never auto-resent (C review 5678438821).
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
