"""Atomic C evaluation of every active batch for one stored observation.

The B lookup finishes before the A device gate is acquired. This is an
explicit port for A's caller, not a scheduler, migration or notification sender.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.modules.iot.models import Device
from app.modules.iot.stream_frontier import begin_evidence_write
from app.modules.telemetry.candidate_preview import (
    F03_SEVERITIES,
    _required_text,
    _required_utc,
    _required_uuid,
    build_candidate_preview,
)
from app.modules.telemetry.outbox_candidate import stage_f03_on_connection_candidate
from app.modules.telemetry.quality import _number, process_batch
from app.modules.telemetry.rule_evaluation_candidate import (
    _SOURCE_NAMESPACE,
    _utc_text,
    mark_evaluation_on_connection_candidate,
)
from app.modules.telemetry.storage_candidate import evaluation_jobs, observations

MultiStatus = Literal[
    "candidate_staged", "within_rule", "held_unknown", "retryable_failure", "not_due"
]


@dataclass(frozen=True)
class MultiBatchResultCandidate:
    status: MultiStatus
    observation_id: UUID
    source_event_ids: tuple[UUID, ...] = ()
    reason: str = ""


def _stored(connection: Connection, observation_id: UUID) -> dict[str, Any] | None:
    row = (
        connection.execute(
            select(observations, evaluation_jobs.c.state)
            .join(
                evaluation_jobs, evaluation_jobs.c.observation_id == observations.c.id
            )
            .where(observations.c.id == observation_id)
            .with_for_update(of=evaluation_jobs)
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


def _batch_utc(value: datetime) -> datetime:
    # B stores UTC instants; SQLite test reads them back without tzinfo.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _rules_by_batch(
    rules: list[dict[str, Any]], batch_ids: tuple[UUID, ...], warehouse_id: UUID
) -> dict[UUID, dict[str, Any]]:
    if not batch_ids or len(batch_ids) > 100 or len(set(batch_ids)) != len(batch_ids):
        raise ValueError("exact nonempty active batch set required")
    expected = set(batch_ids)
    result: dict[UUID, dict[str, Any]] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("rule must be an object")
        batch = UUID(_required_uuid(rule.get("batch_id"), "batch_id"))
        if batch not in expected or batch in result:
            raise ValueError("rule set has extra or duplicate batch")
        if (
            rule.get("warehouse_id") != str(warehouse_id)
            or rule.get("metric_code") != "temperature"
            or rule.get("kind") not in {"threshold_high", "temperature_high"}
            or rule.get("comparison") != ">"
            or rule.get("unit") != "°C"
            or rule.get("severity") not in F03_SEVERITIES
            or _number(rule.get("threshold")) is None
        ):
            raise ValueError("unsupported batch rule")
        result[batch] = rule
    if set(result) != expected:
        raise ValueError("at least one active batch lacks a historical rule")
    return result


def _decisions(
    row: dict[str, Any],
    *,
    observation_id: UUID,
    warehouse_id: UUID,
    batch_ids: tuple[UUID, ...],
    selected_rules: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[UUID, dict[str, Any]]]]:
    rules = _rules_by_batch(selected_rules, batch_ids, warehouse_id)
    normalized, quality, raw = row["normalized"], row["quality"], row["raw_payload"]
    if (
        not isinstance(normalized, dict)
        or not isinstance(quality, list)
        or not isinstance(raw, dict)
    ):
        raise ValueError("stored observation is malformed")
    sampled_at = _required_utc(normalized.get("sampled_at"), "sampled_at")
    received_at = row["first_received_at_utc"]
    if not isinstance(received_at, datetime):
        raise ValueError("received_at is missing")
    view = deepcopy(raw)
    view["warehouse_id"] = str(warehouse_id)
    view["received_at"] = _utc_text(received_at)
    outcomes: list[dict[str, Any]] = []
    high: list[tuple[UUID, dict[str, Any]]] = []
    for batch_id in sorted(batch_ids):
        rule = rules[batch_id]
        threshold = _number(rule["threshold"])
        if threshold is None:
            raise ValueError("threshold must be finite")
        snapshot_id = _required_uuid(rule.get("snapshot_id"), "snapshot_id")
        version = _required_text(rule.get("rule_version"), "rule_version")
        starts = _required_utc(rule.get("effective_start_at"), "effective_start_at")
        ends = (
            _required_utc(rule["effective_end_at"], "effective_end_at")
            if rule.get("effective_end_at") is not None
            else None
        )
        if sampled_at < starts or (ends is not None and sampled_at >= ends):
            raise ValueError("rule is outside sampled_at interval")
        processed = process_batch([view], threshold)[0]
        if processed["normalized"] != normalized or processed["quality"] != quality:
            raise ValueError("stored quality differs from raw evidence")
        state = processed["risk"]["state"]
        source_id = uuid5(
            _SOURCE_NAMESPACE, f"{observation_id}:{batch_id}:temperature_high"
        )
        body = build_candidate_preview(
            processed,
            observation_id=str(observation_id),
            source_event_id=str(source_id),
            rule={
                **rule,
                "rule_snapshot_id": str(snapshot_id),
                "effective_from": rule["effective_start_at"],
                "effective_to": rule.get("effective_end_at"),
            },
        )
        if state == "candidate" and body is None:
            raise ValueError("high candidate could not be built")
        if state == "normal" and quality != ["ok"]:
            raise ValueError("incomplete observation cannot be normal")
        if state not in {"candidate", "normal", "unknown"}:
            raise ValueError("unrecognized quality verdict")
        outcomes.append(
            {
                "batch_id": str(batch_id),
                "state": state,
                "reason": processed["risk"]["reason"],
                "rule_snapshot_id": str(snapshot_id),
                "rule_version": version,
                "threshold_c": threshold,
                "source_event_id": str(source_id) if body is not None else None,
            }
        )
        if body is not None:
            high.append((source_id, body))
    return outcomes, high


def stage_gated_multi_batch_candidate(
    engine: Engine,
    *,
    device_id: UUID,
    warehouse_id: UUID,
    stream_epoch: UUID,
    observation_id: UUID,
    batch_ids: tuple[UUID, ...],
    selected_rules: list[dict[str, Any]],
    now: datetime,
) -> MultiBatchResultCandidate:
    """Commit all batch decisions, outbox rows, risk and one job in one gate.

    ``batch_ids`` are the complete trusted active set chosen from B outside
    this transaction. Any missing/invalid rule, body conflict or write error
    rolls the whole transaction back; the global job remains pending. A replay
    after commit is ``not_due`` and can never stage only a later batch.
    """
    if (
        not all(
            isinstance(value, UUID)
            for value in (device_id, warehouse_id, stream_epoch, observation_id)
        )
        or not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise ValueError("trusted UUIDs and aware now required")
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            begin_evidence_write(
                connection,
                device_id=device_id,
                warehouse_id=warehouse_id,
                epoch=stream_epoch,
            )
            row = _stored(connection, observation_id)
            if row is None or row["state"] != "pending":
                transaction.rollback()
                return MultiBatchResultCandidate("not_due", observation_id)
            if row["device_id"] != device_id or row["warehouse_id"] != warehouse_id:
                transaction.rollback()
                return MultiBatchResultCandidate(
                    "held_unknown", observation_id, reason="observation_scope_mismatch"
                )
            try:
                outcomes, high = _decisions(
                    row,
                    observation_id=observation_id,
                    warehouse_id=warehouse_id,
                    batch_ids=batch_ids,
                    selected_rules=selected_rules,
                )
            except (KeyError, TypeError, ValueError) as exc:
                transaction.rollback()
                return MultiBatchResultCandidate(
                    "held_unknown", observation_id, reason=type(exc).__name__
                )
            if not high and all(item["state"] == "unknown" for item in outcomes):
                transaction.rollback()
                return MultiBatchResultCandidate(
                    "held_unknown", observation_id, reason="unusable_observation"
                )
            if any(item["state"] == "unknown" for item in outcomes) and not high:
                transaction.rollback()
                return MultiBatchResultCandidate(
                    "held_unknown", observation_id, reason="incomplete_observation"
                )
            for source_id, body in high:
                disposition = stage_f03_on_connection_candidate(
                    connection,
                    source_event_id=source_id,
                    observation_id=observation_id,
                    body=body,
                    now=now,
                )
                if disposition == "conflict":
                    transaction.rollback()
                    return MultiBatchResultCandidate(
                        "held_unknown", observation_id, reason="candidate_body_conflict"
                    )
            state: MultiStatus = "candidate_staged" if high else "within_rule"
            risk = {
                "state": "candidate" if high else "normal",
                "reason": "multi_batch_evaluation",
                "temperature_c": _number(
                    row["normalized"]["metrics"].get("temperature")
                ),
                "batches": outcomes,
            }
            if not mark_evaluation_on_connection_candidate(
                connection, observation_id=observation_id, state=state, risk=risk
            ):
                transaction.rollback()
                return MultiBatchResultCandidate("not_due", observation_id)
            transaction.commit()
            return MultiBatchResultCandidate(
                state, observation_id, tuple(source_id for source_id, _ in high)
            )
        except BaseException:
            if transaction.is_active:
                transaction.rollback()
            raise


def select_and_stage_gated_multi_batch_candidate(
    engine: Engine,
    *,
    device_id: UUID,
    warehouse_id: UUID,
    stream_epoch: UUID,
    observation_id: UUID,
    now: datetime,
) -> MultiBatchResultCandidate:
    """Use actual B active batches/F02, then enter C's atomic gated port."""
    from app.modules.alerts.services.batches import list_batches_for_warehouse
    from app.modules.alerts.services.rules import select_rules_for_observation

    try:
        with engine.connect() as connection:
            row = _stored(connection, observation_id)
        if row is None or row["state"] != "pending":
            return MultiBatchResultCandidate("not_due", observation_id)
        if row["device_id"] != device_id or row["warehouse_id"] != warehouse_id:
            return MultiBatchResultCandidate(
                "held_unknown", observation_id, reason="observation_scope_mismatch"
            )
        sampled_at = _required_utc(row["normalized"].get("sampled_at"), "sampled_at")
        with Session(engine) as session:
            device = session.get(Device, device_id)
            if (
                device is None
                or device.kind != "temperature_sensor"
                or device.warehouse_id != warehouse_id
            ):
                return MultiBatchResultCandidate(
                    "held_unknown",
                    observation_id,
                    reason="current_sensor_binding_mismatch",
                )
            batches, total = list_batches_for_warehouse(
                session, warehouse_id, limit=101
            )
            if total > 100:
                return MultiBatchResultCandidate(
                    "held_unknown", observation_id, reason="batch_scope_capacity"
                )
            active = tuple(
                sorted(
                    batch.id
                    for batch in batches
                    if _batch_utc(batch.started_at) <= sampled_at
                    and (
                        batch.ended_at is None
                        or sampled_at < _batch_utc(batch.ended_at)
                    )
                )
            )
            if not active:
                return MultiBatchResultCandidate(
                    "held_unknown", observation_id, reason="no_active_batch"
                )
            selection = select_rules_for_observation(
                session,
                warehouse_id=warehouse_id,
                batch_ids=active,
                metric_code="temperature",
                sampled_at=sampled_at,
            ).model_dump(mode="json")
        if selection.get("status") != "configured":
            return MultiBatchResultCandidate(
                "held_unknown", observation_id, reason="no_complete_historical_rules"
            )
        return stage_gated_multi_batch_candidate(
            engine,
            device_id=device_id,
            warehouse_id=warehouse_id,
            stream_epoch=stream_epoch,
            observation_id=observation_id,
            batch_ids=active,
            selected_rules=selection["rules"],
            now=now,
        )
    except SQLAlchemyError:
        return MultiBatchResultCandidate(
            "retryable_failure", observation_id, reason="storage_unavailable"
        )
    except (KeyError, TypeError, ValueError):  # fmt: skip
        return MultiBatchResultCandidate(
            "held_unknown", observation_id, reason="invalid_rule_or_evidence"
        )
