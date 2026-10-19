"""Bridge a persisted C recovery window to a frozen F03 outbox candidate.

This is an isolated, explicitly invoked port. It does not poll observations,
query B, send HTTP, migrate production tables, or resolve an alert. The caller
must supply B's historical rule for the original anomaly, not today's rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Literal
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.modules.telemetry.f03_body_candidate import normalize_f03_body_candidate
from app.modules.telemetry.outbox_candidate import outbox, stage_f03_candidate
from app.modules.telemetry.recovery_progress_candidate import recovery_progress
from app.modules.telemetry.recovery_rule_candidate import bind_original_recovery_rule
from app.modules.telemetry.recovery_window_candidate import RecoveryRuleCandidate
from app.modules.telemetry.storage_candidate import observations

_RECOVERY_NAMESPACE = UUID("6852de32-3271-4786-a913-6c6a5039069e")
RecoveryStageStatus = Literal[
    "staged",
    "duplicate",
    "conflict",
    "not_eligible",
    "waiting_original",
    "waiting_rule",
    "held_invalid_evidence",
    "retryable_failure",
]


@dataclass(frozen=True)
class RecoveryStageResultCandidate:
    status: RecoveryStageStatus
    source_event_id: UUID | None = None
    reason: str = ""


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("sampled_at must be an aware timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("sampled_at must be an aware timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("sampled_at must be an aware timestamp")
    return result.astimezone(UTC)


def _temperature(
    row: Any, *, device_id: UUID, warehouse_id: UUID
) -> tuple[datetime, float]:
    if (
        row is None
        or row["device_id"] != device_id
        or row["warehouse_id"] != warehouse_id
    ):
        raise ValueError("recovery observation identity mismatch")
    if row["quality"] != ["ok"]:
        raise ValueError("recovery observation quality is not ok")
    normalized = row["normalized"]
    if not isinstance(normalized, dict) or not isinstance(
        normalized.get("metrics"), dict
    ):
        raise ValueError("recovery observation has no normalized metrics")
    value = normalized["metrics"].get("temperature")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
    ):
        raise ValueError("recovery observation has no finite temperature")
    sampled_at = _instant(normalized.get("sampled_at"))
    stored_time = row["sampled_at_utc"]
    if stored_time is None:
        raise ValueError("recovery observation has no stored sampled_at")
    if stored_time.tzinfo is None:
        stored_time = stored_time.replace(tzinfo=UTC)  # SQLite drops tzinfo.
    if stored_time.astimezone(UTC) != sampled_at:
        raise ValueError("recovery observation sampled_at mismatch")
    return sampled_at, float(value)


def _verified_evidence(
    progress: Any,
    linked_rows: dict[UUID, Any],
    rule: RecoveryRuleCandidate,
) -> tuple[tuple[UUID, ...], datetime, float]:
    ids = _stored_observation_ids(progress["observation_ids"])
    if len(ids) < rule.min_samples or len(ids) > 64 or len(ids) != len(set(ids)):
        raise ValueError("recovery window has invalid observation membership")
    anomaly_at = _instant(progress["anomaly_sampled_at_utc"])
    previous_at = anomaly_at
    last_value = 0.0
    for observation_id in ids:
        sampled_at, value = _temperature(
            linked_rows.get(observation_id),
            device_id=progress["device_id"],
            warehouse_id=progress["warehouse_id"],
        )
        if sampled_at <= previous_at or (
            previous_at != anomaly_at
            and (sampled_at - previous_at).total_seconds() > rule.max_gap_seconds
        ):
            raise ValueError("recovery window contains late or gapped samples")
        if not (
            value < rule.threshold_c
            if rule.comparison == "<"
            else value <= rule.threshold_c
        ):
            raise ValueError("recovery window contains an unrecovered sample")
        previous_at, last_value = sampled_at, value
    if previous_at != _instant(
        progress["last_matching_at_utc"]
    ) or previous_at != _instant(progress["last_sampled_at_utc"]):
        raise ValueError("recovery window watermark differs from evidence")
    return ids, previous_at, last_value


def _stored_observation_ids(value: object) -> tuple[UUID, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) for item in value)
    ):
        raise ValueError("recovery window has invalid observation IDs")
    try:
        return tuple(UUID(item) for item in value)
    except ValueError as exc:
        raise ValueError("recovery window has invalid observation IDs") from exc


def stage_eligible_recovery_candidate(
    engine: Engine,
    *,
    original_source_event_id: UUID,
    historical_rule: dict[str, Any] | None,
    now: datetime,
) -> RecoveryStageResultCandidate:
    """Stage one deterministic recovery after checking all persisted evidence.

    A crash after the window becomes eligible but before staging is recoverable:
    call this again with the same original ID and historical rule. Its source ID
    and frozen body are stable, and the outbox reports duplicate instead of
    making a second business event. The caller still must schedule this work.
    """
    if not isinstance(original_source_event_id, UUID):
        raise ValueError("original_source_event_id must be a UUID")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    try:
        with engine.connect() as connection:
            progress = (
                connection.execute(
                    select(recovery_progress).where(
                        recovery_progress.c.original_source_event_id
                        == original_source_event_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            original = (
                connection.execute(
                    select(outbox).where(
                        outbox.c.source_event_id == original_source_event_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if progress is None or not progress["eligible"]:
                return RecoveryStageResultCandidate("not_eligible")
            if original is None or original["state"] != "delivered":
                return RecoveryStageResultCandidate("waiting_original")
            original_observation = (
                connection.execute(
                    select(observations).where(
                        observations.c.id == original["observation_id"]
                    )
                )
                .mappings()
                .one_or_none()
            )
            ids = _stored_observation_ids(progress["observation_ids"])
            linked_rows = {
                row["id"]: row
                for row in connection.execute(
                    select(observations).where(observations.c.id.in_(ids))
                )
                .mappings()
                .all()
            }
    except ValueError as exc:
        return RecoveryStageResultCandidate("held_invalid_evidence", reason=str(exc))
    except SQLAlchemyError:
        return RecoveryStageResultCandidate(
            "retryable_failure", reason="storage_unavailable"
        )

    try:
        body = normalize_f03_body_candidate(original["body"])
        if (
            body["kind"] != "temperature_high"
            or body["related_source_event_id"] is not None
        ):
            raise ValueError("original is not a temperature-high anomaly")
        if (
            body["source_event_id"] != str(original_source_event_id)
            or body["observation_ids"] != [str(original["observation_id"])]
            or body["warehouse_id"] != str(progress["warehouse_id"])
            or body["batch_id"] != str(progress["batch_id"])
            or _instant(body["sampled_at"])
            != _instant(progress["anomaly_sampled_at_utc"])
            or original_observation is None
            or original_observation["device_id"] != progress["device_id"]
            or original_observation["warehouse_id"] != progress["warehouse_id"]
        ):
            raise ValueError("original anomaly and persisted window differ")
        rule = bind_original_recovery_rule(body, historical_rule)
        if rule is None:
            return RecoveryStageResultCandidate("waiting_rule")
        if (
            rule.comparison,
            rule.threshold_c,
            rule.min_samples,
            rule.max_gap_seconds,
        ) != (
            progress["comparison"],
            progress["threshold_c"],
            progress["min_samples"],
            progress["max_gap_seconds"],
        ):
            raise ValueError("historical recovery rule differs from frozen window")
        evidence_ids, sampled_at, value = _verified_evidence(
            progress, linked_rows, rule
        )
        source_id = uuid5(_RECOVERY_NAMESPACE, str(original_source_event_id))
        recovery_body = {
            **body,
            "source_event_id": str(source_id),
            "kind": "temperature_recovered",
            "related_source_event_id": str(original_source_event_id),
            "observation_ids": [str(value) for value in evidence_ids],
            "sampled_at": sampled_at.isoformat().replace("+00:00", "Z"),
            "value": value,
            "algorithm_version": "c-recovery-window/1",
            "evidence": {
                "comparison": rule.comparison,
                "threshold": rule.threshold_c,
                "min_samples": rule.min_samples,
                "max_gap_seconds": rule.max_gap_seconds,
                "quality": ["ok"],
                "original_source_event_id": str(original_source_event_id),
            },
        }
        staged = stage_f03_candidate(
            engine,
            source_event_id=source_id,
            observation_id=evidence_ids[-1],
            body=recovery_body,
            now=now,
        )
    except ValueError as exc:
        return RecoveryStageResultCandidate("held_invalid_evidence", reason=str(exc))
    except SQLAlchemyError:
        return RecoveryStageResultCandidate(
            "retryable_failure", reason="storage_unavailable"
        )
    return RecoveryStageResultCandidate(staged, source_id)
