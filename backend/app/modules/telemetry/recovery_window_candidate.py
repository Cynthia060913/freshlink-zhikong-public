"""Isolated recovery-window decision for an already persisted temperature alert.

The caller must load the original immutable rule and persist the returned window
before staging a recovery. This module neither reads B nor closes an alert.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Literal
from uuid import UUID

RecoveryDecision = Literal[
    "progress", "eligible", "reset", "duplicate", "late", "unrelated", "frozen"
]


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("sampled_at must be timezone aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class RecoveryRuleCandidate:
    """Explicit rule values from the original anomaly; never inferred from it."""

    comparison: Literal["<", "<="]
    threshold_c: float
    min_samples: int
    max_gap_seconds: int

    def __post_init__(self) -> None:
        if self.comparison not in ("<", "<="):
            raise ValueError("recovery comparison must be explicit")
        if (
            isinstance(self.threshold_c, bool)
            or not isinstance(self.threshold_c, (int, float))
            or not isfinite(self.threshold_c)
        ):
            raise ValueError("recovery threshold must be finite")
        if (
            isinstance(self.min_samples, bool)
            or not isinstance(self.min_samples, int)
            or self.min_samples < 3
        ):
            raise ValueError("min_samples must be at least 3")
        if (
            isinstance(self.max_gap_seconds, bool)
            or not isinstance(self.max_gap_seconds, int)
            or not 1 <= self.max_gap_seconds <= 90
        ):
            raise ValueError("max_gap_seconds must be 1-90")


@dataclass(frozen=True)
class RecoveryWindowCandidate:
    original_source_event_id: UUID
    device_id: UUID
    warehouse_id: UUID
    batch_id: UUID
    anomaly_sampled_at: datetime
    last_sampled_at: datetime | None = None
    observation_ids: tuple[UUID, ...] = ()
    last_matching_at: datetime | None = None
    eligible: bool = False
    last_received_at: datetime | None = None
    last_received_observation_id: UUID | None = None

    def __post_init__(self) -> None:
        _utc(self.anomaly_sampled_at)
        if self.last_sampled_at is not None:
            _utc(self.last_sampled_at)
        if self.last_matching_at is not None:
            _utc(self.last_matching_at)
        if self.last_received_at is not None:
            _utc(self.last_received_at)
        if (self.last_received_at is None) != (
            self.last_received_observation_id is None
        ):
            raise ValueError("recovery receipt watermark must have both parts")


@dataclass(frozen=True)
class RecoverySampleCandidate:
    observation_id: UUID
    device_id: UUID
    warehouse_id: UUID
    batch_id: UUID
    sampled_at: datetime | None
    temperature_c: float | None
    quality: tuple[str, ...]
    duplicate: Literal["new", "duplicate", "conflict"]
    received_at: datetime | None = None


def advance_recovery_window(
    window: RecoveryWindowCandidate,
    sample: RecoverySampleCandidate,
    rule: RecoveryRuleCandidate,
) -> tuple[RecoveryWindowCandidate, RecoveryDecision]:
    """Advance in receipt order; late data is retained elsewhere, never backfilled.

    A completed window is frozen so retries use the same three-or-more IDs.
    `eligible` only authorizes local staging; B acceptance and resolved status
    must still be checked through the durable outbox.
    """
    if window.eligible:
        return window, "frozen"
    if (
        sample.device_id != window.device_id
        or sample.warehouse_id != window.warehouse_id
        or sample.batch_id != window.batch_id
    ):
        return window, "unrelated"
    received_at = _utc(sample.received_at) if sample.received_at is not None else None
    if window.last_received_at is not None and received_at is None:
        raise ValueError("received_at required after a receipt watermark is stored")
    if received_at is not None and window.last_received_at is not None:
        previous_id = window.last_received_observation_id
        if previous_id is None:
            raise ValueError("recovery receipt watermark is incomplete")
        if (received_at, sample.observation_id.int) <= (
            _utc(window.last_received_at),
            previous_id.int,
        ):
            return (
                window,
                "duplicate" if sample.observation_id == previous_id else "late",
            )
    advanced, decision = _advance_evidence(window, sample, rule)
    if received_at is None or sample.duplicate == "duplicate":
        return advanced, decision
    return (
        replace(
            advanced,
            last_received_at=received_at,
            last_received_observation_id=sample.observation_id,
        ),
        decision,
    )


def _advance_evidence(
    window: RecoveryWindowCandidate,
    sample: RecoverySampleCandidate,
    rule: RecoveryRuleCandidate,
) -> tuple[RecoveryWindowCandidate, RecoveryDecision]:
    """Apply sampling evidence after the optional receipt-order gate."""
    if sample.duplicate == "duplicate":
        return window, "duplicate"
    if sample.duplicate == "conflict":
        sampled_at = None if sample.sampled_at is None else _utc(sample.sampled_at)
        last_sampled_at = window.last_sampled_at
        if sampled_at is not None and (
            last_sampled_at is None or sampled_at > _utc(last_sampled_at)
        ):
            last_sampled_at = sampled_at
        return (
            replace(
                window,
                last_sampled_at=last_sampled_at,
                observation_ids=(),
                last_matching_at=None,
            ),
            "reset",
        )
    sampled_at = None if sample.sampled_at is None else _utc(sample.sampled_at)
    if sampled_at is None:
        return replace(window, observation_ids=(), last_matching_at=None), "reset"
    if sampled_at <= _utc(window.anomaly_sampled_at) or (
        window.last_sampled_at is not None
        and sampled_at <= _utc(window.last_sampled_at)
    ):
        return window, "late"
    if sample.observation_id in window.observation_ids:
        return window, "duplicate"
    if sample.quality != ("ok",) or (
        sample.temperature_c is None
        or isinstance(sample.temperature_c, bool)
        or not isinstance(sample.temperature_c, (int, float))
        or not isfinite(sample.temperature_c)
    ):
        return (
            replace(
                window,
                last_sampled_at=sampled_at,
                observation_ids=(),
                last_matching_at=None,
            ),
            "reset",
        )
    matches = (
        sample.temperature_c < rule.threshold_c
        if rule.comparison == "<"
        else sample.temperature_c <= rule.threshold_c
    )
    if not matches:
        return (
            replace(
                window,
                last_sampled_at=sampled_at,
                observation_ids=(),
                last_matching_at=None,
            ),
            "reset",
        )
    continues = window.last_matching_at is not None and sampled_at - _utc(
        window.last_matching_at
    ) <= timedelta(seconds=rule.max_gap_seconds)
    ids = (
        (*window.observation_ids, sample.observation_id)
        if continues
        else (sample.observation_id,)
    )
    eligible = len(ids) >= rule.min_samples
    return (
        replace(
            window,
            last_sampled_at=sampled_at,
            last_matching_at=sampled_at,
            observation_ids=ids,
            eligible=eligible,
        ),
        "eligible" if eligible else "progress",
    )
