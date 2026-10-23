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

from sqlalchemy import select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from app.modules.iot.stream_frontier import (
    FrontierStamp,
    begin_evidence_write,
    mark_uncertain,
    read_complete_frontier,
    stamp_is_current,
)
from app.modules.telemetry.f03_body_candidate import normalize_f03_body_candidate
from app.modules.telemetry.outbox_candidate import (
    outbox,
    stage_f03_on_connection_candidate,
)
from app.modules.telemetry.recovery_progress_candidate import recovery_progress
from app.modules.telemetry.recovery_reception_evidence_candidate import (
    has_unresolved_reception_evidence_candidate,
)
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


def freeze_recovery_frontier_on_connection_candidate(
    connection: Connection,
    *,
    recovery_source_event_id: UUID,
    original_source_event_id: UUID,
    stamp: FrontierStamp,
) -> Literal["frozen", "duplicate"]:
    """Persist a complete A frontier on a pending C recovery, without commit.

    The caller must first obtain A's write gate on this same Connection, then
    stage the recovery and call this before the outer commit. A's frontier
    check retains the stream lock through that commit. Legacy candidates with
    null fields cannot authorize automatic delivery; this port does not turn
    on the sender or settle a previously delivered B recovery.
    """
    if not connection.in_transaction():
        raise ValueError("caller must start the transaction before freezing")
    if (
        not isinstance(recovery_source_event_id, UUID)
        or not isinstance(original_source_event_id, UUID)
        or not isinstance(stamp, FrontierStamp)
        or stamp.admitted_seq <= 0
        or stamp.admitted_seq != stamp.settled_through
    ):
        raise ValueError("invalid frozen recovery identity or frontier")
    if not stamp_is_current(connection, stamp):
        raise ValueError("recovery frontier is incomplete or changed")
    recovery = (
        connection.execute(
            select(outbox)
            .where(outbox.c.source_event_id == recovery_source_event_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    original = (
        connection.execute(
            select(outbox).where(outbox.c.source_event_id == original_source_event_id)
        )
        .mappings()
        .one_or_none()
    )
    if recovery is None or original is None:
        raise ValueError("recovery or original anomaly missing")
    recovery_body, original_body = recovery["body"], original["body"]
    if (
        recovery["state"] != "pending"
        or original["state"] != "delivered"
        or recovery["related_source_event_id"] != original_source_event_id
        or not isinstance(recovery_body, dict)
        or not isinstance(original_body, dict)
        or recovery_body.get("kind") != "temperature_recovered"
        or recovery_body.get("source_event_id") != str(recovery_source_event_id)
        or recovery_body.get("related_source_event_id") != str(original_source_event_id)
        or not isinstance(recovery_body.get("observation_ids"), list)
        or str(recovery["observation_id"]) not in recovery_body["observation_ids"]
        or original_body.get("kind") not in {"temperature_high", "temperature_low"}
        or original_body.get("source_event_id") != str(original_source_event_id)
        or original_body.get("observation_ids") != [str(original["observation_id"])]
        or recovery_body.get("warehouse_id") != str(stamp.warehouse_id)
        or original_body.get("warehouse_id") != str(stamp.warehouse_id)
    ):
        raise ValueError("recovery is not a pending child of this anomaly")
    for observation_id in (original["observation_id"], recovery["observation_id"]):
        owner = (
            connection.execute(
                select(observations.c.device_id, observations.c.warehouse_id).where(
                    observations.c.id == observation_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            owner is None
            or owner["device_id"] != stamp.device_id
            or owner["warehouse_id"] != stamp.warehouse_id
        ):
            raise ValueError("recovery observation does not belong to frontier")
    columns = (
        "frozen_device_id",
        "frozen_warehouse_id",
        "frozen_stream_epoch",
        "frozen_admitted_seq",
        "frozen_settled_through",
        "frozen_evidence_revision",
    )
    values = (
        stamp.device_id,
        stamp.warehouse_id,
        stamp.stream_epoch,
        stamp.admitted_seq,
        stamp.settled_through,
        stamp.evidence_revision,
    )
    previous = tuple(recovery[name] for name in columns)
    if previous == values:
        return "duplicate"
    if any(value is not None for value in previous):
        raise ValueError("recovery has a different or partial frozen frontier")
    connection.execute(
        update(outbox)
        .where(outbox.c.source_event_id == recovery_source_event_id)
        .values(**dict(zip(columns, values, strict=True)))
    )
    return "frozen"


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


def stage_eligible_recovery_on_connection_candidate(
    connection: Connection,
    *,
    original_source_event_id: UUID,
    historical_rule: dict[str, Any] | None,
    now: datetime,
) -> RecoveryStageResultCandidate:
    """Verify evidence and stage recovery in the caller's open transaction.

    The caller owns all locking, commit and rollback. In particular this does
    not itself prove an A admission frontier or prevent unguarded C writes.
    A database error propagates so a caller cannot accidentally commit a
    failed PostgreSQL transaction. No B network call occurs under the lock.
    """
    if not connection.in_transaction():
        raise ValueError("caller must start the transaction before staging")
    if not isinstance(original_source_event_id, UUID):
        raise ValueError("original_source_event_id must be a UUID")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    try:
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
        if (
            original_observation is not None
            and has_unresolved_reception_evidence_candidate(
                connection,
                device_id=original_observation["device_id"],
                warehouse_id=original_observation["warehouse_id"],
                original_received_at=original_observation["first_received_at_utc"],
            )
        ):
            return RecoveryStageResultCandidate(
                "held_invalid_evidence", reason="unresolved_reception_evidence"
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
        staged = stage_f03_on_connection_candidate(
            connection,
            source_event_id=source_id,
            observation_id=evidence_ids[-1],
            body=recovery_body,
            now=now,
        )
    except ValueError as exc:
        return RecoveryStageResultCandidate("held_invalid_evidence", reason=str(exc))
    return RecoveryStageResultCandidate(staged, source_id)


def stage_eligible_recovery_candidate(
    engine: Engine,
    *,
    original_source_event_id: UUID,
    historical_rule: dict[str, Any] | None,
    now: datetime,
) -> RecoveryStageResultCandidate:
    """Standalone wrapper; one local transaction, without an A frontier lock."""
    try:
        with engine.begin() as connection:
            return stage_eligible_recovery_on_connection_candidate(
                connection,
                original_source_event_id=original_source_event_id,
                historical_rule=historical_rule,
                now=now,
            )
    except SQLAlchemyError:
        return RecoveryStageResultCandidate(
            "retryable_failure", reason="storage_unavailable"
        )


def stage_gated_recovery_candidate(
    engine: Engine,
    *,
    device_id: UUID,
    warehouse_id: UUID,
    stream_epoch: UUID,
    original_source_event_id: UUID,
    historical_rule: dict[str, Any] | None,
    now: datetime,
) -> RecoveryStageResultCandidate:
    """Stage and freeze a recovery under A's write gate in one transaction.

    B's historical rule is selected before this call; there is no network
    activity or B read while holding the device lock. A's stream revision,
    C's outbox row and the complete frontier stamp commit together. This is
    an opt-in candidate port, not an automatic sender or worker.
    """
    if not all(
        isinstance(value, UUID)
        for value in (
            device_id,
            warehouse_id,
            stream_epoch,
            original_source_event_id,
        )
    ):
        raise ValueError("trusted device, warehouse, epoch and anomaly UUIDs required")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            begin_evidence_write(
                connection,
                device_id=device_id,
                warehouse_id=warehouse_id,
                epoch=stream_epoch,
            )
            result = stage_eligible_recovery_on_connection_candidate(
                connection,
                original_source_event_id=original_source_event_id,
                historical_rule=historical_rule,
                now=now,
            )
            if result.status == "staged":
                stamp = read_complete_frontier(
                    connection,
                    device_id=device_id,
                    warehouse_id=warehouse_id,
                    epoch=stream_epoch,
                )
                if stamp is None:
                    transaction.rollback()
                    return RecoveryStageResultCandidate(
                        "held_invalid_evidence", reason="frontier_incomplete"
                    )
                if result.source_event_id is None:
                    raise ValueError("staged recovery has no source event ID")
                freeze_recovery_frontier_on_connection_candidate(
                    connection,
                    recovery_source_event_id=result.source_event_id,
                    original_source_event_id=original_source_event_id,
                    stamp=stamp,
                )
                transaction.commit()
            elif result.status == "conflict":
                # Preserve the same-source different-body audit and A hold.
                mark_uncertain(connection, device_id=device_id, epoch=stream_epoch)
                transaction.commit()
            else:
                # A repeat or wait did not change C state. Roll back the
                # otherwise unnecessary A revision so an existing frozen
                # recovery is not invalidated by a no-op poll.
                transaction.rollback()
            return result
        except BaseException:
            if transaction.is_active:
                transaction.rollback()
            raise
