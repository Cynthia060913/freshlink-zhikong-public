"""Explicit C port from saved observations to a durable recovery window.

The caller supplies the original anomaly ID, a trusted batch association, and
the historical B rule. This port does not schedule itself or resolve B alerts.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.modules.telemetry.f03_body_candidate import (
    f03_digest_candidate,
    normalize_f03_body_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.quality import _number, process_batch
from app.modules.telemetry.recovery_outbox_candidate import _instant
from app.modules.telemetry.recovery_progress_candidate import (
    advance_persisted_recovery_window,
)
from app.modules.telemetry.recovery_rule_candidate import bind_original_recovery_rule
from app.modules.telemetry.recovery_window_candidate import (
    RecoveryDecision,
    RecoverySampleCandidate,
    RecoveryWindowCandidate,
)
from app.modules.telemetry.storage_candidate import observations

RecoveryAdvanceStatus = Literal[
    "advanced", "waiting_rule", "held_invalid_evidence", "retryable_failure"
]


@dataclass(frozen=True)
class RecoveryAdvanceResultCandidate:
    status: RecoveryAdvanceStatus
    decision: RecoveryDecision | None = None
    reason: str = ""


def _sample_from_saved(row: Any, *, batch_id: UUID) -> RecoverySampleCandidate:
    raw = row["raw_payload"]
    received_at = row["first_received_at_utc"]
    if not isinstance(raw, dict) or not isinstance(received_at, datetime):
        raise ValueError("stored observation evidence is invalid")
    if received_at.tzinfo is None:  # SQLite's test driver drops tzinfo.
        received_at = received_at.replace(tzinfo=UTC)
    view = deepcopy(raw)
    view["warehouse_id"] = str(row["warehouse_id"])
    view["received_at"] = received_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    processed = process_batch([view])[0]
    normalized = row["normalized"]
    quality = row["quality"]
    if (
        not isinstance(normalized, dict)
        or not isinstance(quality, list)
        or any(not isinstance(tag, str) for tag in quality)
    ):
        raise ValueError("stored observation quality is invalid")
    if processed["normalized"] != normalized or processed["quality"] != quality:
        raise ValueError("stored quality differs from raw observation")
    if (
        normalized.get("device_id") != str(row["device_id"])
        or normalized.get("warehouse_id") != str(row["warehouse_id"])
        or normalized.get("message_id") != str(row["message_id"])
    ):
        raise ValueError("stored observation identity differs from raw observation")
    sampled_text = normalized.get("sampled_at")
    sampled_at = _instant(sampled_text) if sampled_text is not None else None
    stored_at = row["sampled_at_utc"]
    if stored_at is not None and stored_at.tzinfo is None:
        stored_at = stored_at.replace(tzinfo=UTC)
    if stored_at != sampled_at:
        raise ValueError("stored sampled_at differs from raw observation")
    metrics = normalized.get("metrics")
    value = metrics.get("temperature") if isinstance(metrics, dict) else None
    temperature = _number(value)
    return RecoverySampleCandidate(
        observation_id=row["id"],
        device_id=row["device_id"],
        warehouse_id=row["warehouse_id"],
        batch_id=batch_id,
        sampled_at=sampled_at,
        temperature_c=temperature,
        quality=tuple(quality),
        duplicate="new",  # Only accepted_new rows enter observations.
    )


def advance_saved_recovery_observation_candidate(
    engine: Engine,
    *,
    original_source_event_id: UUID,
    observation_id: UUID,
    batch_id: UUID,
    historical_rule: dict[str, Any] | None,
) -> RecoveryAdvanceResultCandidate:
    """Advance one saved sample; original evidence and rule stay immutable.

    A replay of the same observation cannot add another recovery sample. An
    invalid/missing reading resets or holds the window according to its saved
    quality; it never proves recovery. B acceptance is checked at later staging.
    """
    if not all(
        isinstance(value, UUID)
        for value in (original_source_event_id, observation_id, batch_id)
    ):
        raise ValueError("recovery IDs must be UUIDs")
    try:
        with engine.connect() as connection:
            original = (
                connection.execute(
                    select(outbox).where(
                        outbox.c.source_event_id == original_source_event_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            original_observation = (
                connection.execute(
                    select(observations).where(
                        observations.c.id == original["observation_id"]
                    )
                )
                .mappings()
                .one_or_none()
                if original is not None
                else None
            )
            observation = (
                connection.execute(
                    select(observations).where(observations.c.id == observation_id)
                )
                .mappings()
                .one_or_none()
            )
    except SQLAlchemyError:
        return RecoveryAdvanceResultCandidate(
            "retryable_failure", reason="storage_unavailable"
        )
    try:
        if original is None or original_observation is None or observation is None:
            raise ValueError("original or recovery observation is unavailable")
        body = normalize_f03_body_candidate(original["body"])
        original_sample = _sample_from_saved(original_observation, batch_id=batch_id)
        if (
            f03_digest_candidate(body).removeprefix("sha256:")
            != original["body_sha256"]
            or body["kind"] != "temperature_high"
            or body["related_source_event_id"] is not None
            or body["source_event_id"] != str(original_source_event_id)
            or body["observation_ids"] != [str(original["observation_id"])]
            or body["batch_id"] != str(batch_id)
            or body["warehouse_id"] != str(original_observation["warehouse_id"])
            or original_observation["device_id"] != observation["device_id"]
            or original_observation["warehouse_id"] != observation["warehouse_id"]
            or _instant(body["sampled_at"]) != original_sample.sampled_at
            or body["value"] != original_sample.temperature_c
        ):
            raise ValueError("original anomaly identity differs from saved observation")
        rule = bind_original_recovery_rule(body, historical_rule)
        if rule is None:
            return RecoveryAdvanceResultCandidate("waiting_rule")
        sample = _sample_from_saved(observation, batch_id=batch_id)
        initial = RecoveryWindowCandidate(
            original_source_event_id=original_source_event_id,
            device_id=original_observation["device_id"],
            warehouse_id=original_observation["warehouse_id"],
            batch_id=batch_id,
            anomaly_sampled_at=_instant(body["sampled_at"]),
        )
        _, decision = advance_persisted_recovery_window(engine, initial, rule, sample)
    except ValueError as exc:
        return RecoveryAdvanceResultCandidate("held_invalid_evidence", reason=str(exc))
    except SQLAlchemyError:
        return RecoveryAdvanceResultCandidate(
            "retryable_failure", reason="storage_unavailable"
        )
    return RecoveryAdvanceResultCandidate("advanced", decision)
