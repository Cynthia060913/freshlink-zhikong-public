"""Batch rule snapshot SQLModel + create / public DTOs.

A :class:`BatchRuleSnapshot` is an immutable list of rule dicts that
govern what counts as an alert for one batch. Per the B → A reply on
Issue #5 (comment 6108448780):

- Snapshots are **strictly append-only**. Once a row is committed,
  the service layer never UPDATEs or DELETEs any column. The alembic
  migration that lands them (deferred until PR #4 merges) installs a
  PostgreSQL ``BEFORE UPDATE / DELETE`` trigger to enforce this at
  the DB level too. Reviewed and confirmed on PR #10 round 2:
  ``is_current`` and ``superseded_by`` are no longer stored columns
  — they lived only to express a previous "atomic supersede" design
  that conflicted with append-only.
- "Current snapshot" is defined as the row with the latest
  ``created_at`` for a given ``batch_id``. No flag, no FK link —
  the latest row wins by insertion order. There is therefore no
  mutation when a new snapshot is inserted: only an INSERT.
- ``created_at`` is set in-process on insert and is treated as the
  authoritative ordering. Server clock drift is mitigated by
  insisting on monotonic UTC timestamps in the service layer (B2).

A snapshot's content is a list of dicts; each dict must contain
``metric_code``, ``candidate_kind``, ``severity``, ``threshold_low``
and ``threshold_high`` so the ingest service can decide which rule
to apply to a given telemetry observation.
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

from pydantic import ValidationInfo, field_validator, model_validator
from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel

_METRIC_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class RuleSeverity(enum.StrEnum):
    """Severity tier aligned with C's candidate_kind values.

    Kept intentionally narrow; the ingest service can map from
    additional C sample codes via :mod:`app.modules.alerts.rules.mapping`
    (B2 work).
    """

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RecoverySpec(SQLModel):
    """Recovery condition for one rule (F02 contract, B-side 6127332268).

    A candidate is considered recovered only after ``min_samples``
    consecutive samples satisfy ``comparison threshold``. No data or
    no rule never auto-recovers.
    """

    comparison: Literal[">", "<", ">=", "<="]
    threshold: float
    min_samples: int = Field(default=1, ge=1, le=1000)

    @field_validator("threshold")
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("recovery threshold must be finite")
        return value


class RuleDict(SQLModel):
    """One rule inside a snapshot's list.

    The dict is **canonical** in service-level JSON: keys are sorted
    by ``json.dumps(sort_keys=True)`` and ``metric_code`` is required.
    Units follow INTERFACES v0.1: temperature °C, humidity %RH.
    """

    metric_code: str = Field(min_length=1, max_length=64)
    candidate_kind: str = Field(min_length=1, max_length=32)
    severity: RuleSeverity
    threshold_low: float | None = None
    threshold_high: float | None = None
    unit: str | None = Field(default=None, max_length=8)
    recovery: RecoverySpec | None = None

    @field_validator("metric_code", mode="before")
    @classmethod
    def normalize_metric_code(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("metric_code must be text")
        normalized = value.strip().lower()
        if not _METRIC_CODE_RE.fullmatch(normalized):
            raise ValueError(
                "metric_code must be 1-64 lowercase ASCII letters, "
                "digits, dots, underscores or hyphens"
            )
        return normalized

    @field_validator("candidate_kind", mode="before")
    @classmethod
    def normalize_candidate_kind(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("candidate_kind must be text")
        stripped = value.strip()
        if not stripped:
            raise ValueError("candidate_kind cannot be blank")
        return stripped

    @field_validator("unit", mode="before")
    @classmethod
    def normalize_unit(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("unit must be non-empty text when provided")
        return value.strip()

    @field_validator("threshold_low", "threshold_high")
    @classmethod
    def finite_thresholds(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("thresholds must be finite numbers")
        return value

    @model_validator(mode="after")
    def check_threshold_pair(self) -> RuleDict:
        low = self.threshold_low
        high = self.threshold_high
        if low is not None and high is not None and low >= high:
            raise ValueError("threshold_low must be strictly below threshold_high")
        if low is None and high is None:
            raise ValueError("at least one of threshold_low/threshold_high is required")
        return self


def _canonical_rule_payload(rules: list[dict[str, Any]]) -> str:
    """Canonical JSON used to compute the snapshot's signature.

    Sorting keys + a stable separators produces the same byte stream
    regardless of dict-insertion order, which lets the same rule set
    always hash to the same signature.
    """

    return json.dumps(rules, sort_keys=True, separators=(",", ":"))


def compute_snapshot_signature(
    version_id: uuid.UUID, rules: list[dict[str, Any]]
) -> str:
    """SHA-256 over ``version_id`` + canonical rules JSON.

    Returned as a hex digest. Two snapshots with identical rules and
    version_id are guaranteed to collide.
    """

    payload = f"{version_id}:{_canonical_rule_payload(rules)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_RULE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _require_aware(value: datetime, field_name: str) -> datetime:
    """Reject naive datetimes; normalise aware input to UTC.

    Mixed naive/aware comparisons raise ``TypeError`` at runtime
    (A review 6110998037), so naive values are rejected at the DTO
    boundary. Aware non-UTC input is normalised to UTC so SQLite's
    string comparison of stored values stays consistent.
    """

    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be timezone-aware (UTC expected)")
    return value.astimezone(UTC)


class BatchRuleSnapshotCreate(SQLModel):
    """Payload accepted by ``POST /batches/{bid}/rule-snapshots``.

    F02 contract (B-side 6127332268): intervals are half-open
    ``[start, end)`` in UTC; ``end`` must be strictly after ``start``
    (an empty interval is rejected); ``rule_version`` is caller-
    provided and stored verbatim; ``supersedes_snapshot_ids`` is the
    append-only escape hatch that lets a successor snapshot replace
    overlapping predecessors without mutating them.
    """

    version_id: uuid.UUID
    rules: list[RuleDict] = Field(min_length=1, max_length=200)
    effective_start_at: datetime
    effective_end_at: datetime | None = None
    rule_version: str | None = Field(default=None, max_length=64)
    supersedes_snapshot_ids: list[uuid.UUID] = Field(default_factory=list)

    @field_validator("effective_start_at")
    @classmethod
    def start_aware_utc(cls, value: datetime) -> datetime:
        return _require_aware(value, "effective_start_at")

    @field_validator("effective_end_at")
    @classmethod
    def end_aware_and_after_start(
        cls, value: datetime | None, info: ValidationInfo
    ) -> datetime | None:
        if value is None:
            return None
        normalized = _require_aware(value, "effective_end_at")
        started = info.data.get("effective_start_at")
        if started is not None and normalized <= started:
            raise ValueError(
                "effective_end_at must be strictly after effective_start_at "
                "(half-open intervals cannot be empty)"
            )
        return normalized

    @field_validator("rule_version")
    @classmethod
    def rule_version_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not _RULE_VERSION_RE.fullmatch(stripped):
            raise ValueError(
                "rule_version must be 1-64 chars: letters, digits, "
                "dots, underscores or hyphens, starting alphanumeric"
            )
        return stripped


class BatchRuleSnapshot(SQLModel, table=True):
    """Persistent snapshot row. Strictly append-only.

    The "current snapshot" for a batch is the row with the largest
    ``created_at``. There are no ``is_current`` / ``superseded_by``
    columns because their only role was to drive a previous "atomic
    supersede" design that mutated old rows on each new insert —
    incompatible with the append-only contract reviewed in PR #10
    round 2.

    ``supersedes_json`` is a JSON list of predecessor snapshot ids
    that this row replaces. It lives on the SUCCESSOR row, so closing
    a predecessor's window never mutates the predecessor itself
    (A section 5: "追加版本链接").
    """

    __tablename__ = "alerts_batch_rule_snapshot"
    __table_args__ = (
        # (batch_id, signature) is the contract that backs
        # :class:`SnapshotConflict`. Without this DB-level constraint
        # the service-layer pre-check is racy under concurrent
        # inserts (PR #10 round 2, item 2).
        UniqueConstraint(
            "batch_id",
            "signature",
            name="uq_alerts_batch_rule_snapshot_batch_signature",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    batch_id: uuid.UUID = Field(foreign_key="alerts_batch.id", index=True)
    version_id: uuid.UUID = Field(index=True)
    rule_version: str | None = Field(default=None, max_length=64, index=True)
    payload_json: str  # canonical list[dict] JSON
    signature: str = Field(max_length=64)
    effective_start_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    effective_end_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    supersedes_json: str = Field(default="[]")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


class BatchRuleSnapshotPublic(SQLModel):
    """Read-only shape returned by the rule-snapshot API.

    The "is this the current snapshot?" question is answered by the
    GET ``/rule-snapshots/{id}`` vs ``/batches/{bid}/rule-snapshots/current``
    comparison — the row itself carries no flag. ``created_at``
    ordering is insertion order; uuid4 ids are NOT monotonic and are
    only used as a deterministic tie-break.
    """

    id: uuid.UUID
    batch_id: uuid.UUID
    version_id: uuid.UUID
    rule_version: str | None
    rules: list[dict[str, Any]]
    signature: str
    effective_start_at: datetime
    effective_end_at: datetime | None
    supersedes_snapshot_ids: list[uuid.UUID]
    created_at: datetime
