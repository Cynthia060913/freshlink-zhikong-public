"""One-batch C evaluation over persisted observations and an injected B F02 port.

This is an isolated candidate path for the first E1 temperature-high scenario.
It neither imports B's unmerged module nor installs a scheduler or migration.
The integration caller must supply the batch from B's trusted warehouse/batch
association; no device-supplied batch ID is trusted here.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID, uuid5

from sqlalchemy import select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.modules.iot.stream_frontier import begin_evidence_write, mark_uncertain
from app.modules.telemetry.candidate_preview import (
    F03_SEVERITIES,
    _required_text,
    _required_utc,
    _required_uuid,
    build_candidate_preview,
)
from app.modules.telemetry.f03_body_candidate import normalize_f03_body_candidate
from app.modules.telemetry.outbox_candidate import (
    outbox,
    stage_f03_candidate,
    stage_f03_on_connection_candidate,
)
from app.modules.telemetry.quality import _number, process_batch
from app.modules.telemetry.storage_candidate import evaluation_jobs, observations

_SOURCE_NAMESPACE = UUID("5b94ee30-b302-45b7-94b7-9447e650ec5c")
EvaluationStatus = Literal[
    "candidate_staged",
    "within_rule",
    "no_rule",
    "held_unknown",
    "retryable_failure",
    "not_due",
]


class F02SelectionPort(Protocol):
    def select_rules_for_observation(
        self,
        *,
        warehouse_id: UUID,
        batch_ids: tuple[UUID, ...],
        metric_code: str,
        sampled_at: datetime,
    ) -> dict[str, Any]: ...


class LocalBRuleSelectionCandidate:
    """Read B's actual F02 service outside the A/C evidence-write lock.

    The caller supplies a batch ID from a trusted warehouse association.
    This adapter does not choose that association, start a worker, or send an
    alert. A storage error propagates so pending C evaluation can retry.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def select_rules_for_observation(
        self,
        *,
        warehouse_id: UUID,
        batch_ids: tuple[UUID, ...],
        metric_code: str,
        sampled_at: datetime,
    ) -> dict[str, Any]:
        from app.modules.alerts.services.rules import select_rules_for_observation

        with Session(self._engine) as session:
            return select_rules_for_observation(
                session,
                warehouse_id=warehouse_id,
                batch_ids=batch_ids,
                metric_code=metric_code,
                sampled_at=sampled_at,
            ).model_dump(mode="json")


@dataclass(frozen=True)
class EvaluationResultCandidate:
    status: EvaluationStatus
    observation_id: UUID
    source_event_id: UUID | None = None
    reason: str = ""


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        # The isolated candidate table writes UTC; SQLite drops tzinfo on read.
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def mark_evaluation_on_connection_candidate(
    connection: Connection,
    *,
    observation_id: UUID,
    state: str,
    risk: dict[str, Any] | None = None,
) -> bool:
    """Stage an evaluated job and risk in the caller's gated transaction.

    The caller must verify the historical rule and persisted observation
    before taking A's device write gate. This function never contacts B,
    commits, or retries a failed outer transaction.
    """
    if not connection.in_transaction():
        raise ValueError("an open caller transaction is required")
    expected_risk = {"candidate_staged": "candidate", "within_rule": "normal"}
    if state == "no_rule":
        if risk is not None:
            raise ValueError("no rule cannot assign normal or candidate risk")
    elif (
        state not in expected_risk
        or not isinstance(risk, dict)
        or risk.get("state") != expected_risk[state]
    ):
        raise ValueError("evaluation state and risk must agree")
    updated = connection.execute(
        update(evaluation_jobs)
        .where(
            evaluation_jobs.c.observation_id == observation_id,
            evaluation_jobs.c.state == "pending",
        )
        .values(state=state)
    )
    if updated.rowcount != 1:
        return False
    if risk is not None:
        connection.execute(
            update(observations)
            .where(observations.c.id == observation_id)
            .values(risk=risk)
        )
    return True


def stage_evaluated_high_on_connection_candidate(
    connection: Connection,
    *,
    observation_id: UUID,
    batch_id: UUID,
    body: dict[str, Any],
    now: datetime,
) -> EvaluationResultCandidate:
    """Atomically freeze one preselected high candidate and its C risk.

    The caller selects B's historical rule before taking A's evidence gate,
    then keeps that gate and this connection until the outer transaction ends.
    This verifies the saved observation against the proposed body but cannot
    independently certify B's selection or an ungated caller. No B I/O or
    external send occurs inside the transaction.
    """
    if not connection.in_transaction():
        raise ValueError("an open caller transaction is required")
    if not isinstance(observation_id, UUID) or not isinstance(batch_id, UUID):
        raise ValueError("observation_id and batch_id must be UUIDs")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
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
    if row is None or row["state"] != "pending":
        return EvaluationResultCandidate("not_due", observation_id)
    frozen = normalize_f03_body_candidate(body)
    normalized, quality = row["normalized"], row["quality"]
    metrics = normalized.get("metrics") if isinstance(normalized, dict) else None
    temperature = metrics.get("temperature") if isinstance(metrics, dict) else None
    normalized_temperature = _number(temperature)
    threshold = _number(frozen["evidence"].get("threshold"))
    expected_source = uuid5(
        _SOURCE_NAMESPACE, f"{observation_id}:{batch_id}:temperature_high"
    )
    if (
        frozen["kind"] != "temperature_high"
        or frozen["related_source_event_id"] is not None
        or frozen["source_event_id"] != str(expected_source)
        or frozen["observation_ids"] != [str(observation_id)]
        or frozen["batch_id"] != str(batch_id)
        or frozen["warehouse_id"] != str(row["warehouse_id"])
        or not isinstance(normalized, dict)
        or frozen["sampled_at"] != normalized.get("sampled_at")
        or normalized_temperature is None
        or frozen["value"] != temperature
        or not isinstance(quality, list)
        or frozen["evidence"].get("quality") != quality
        or threshold is None
        or normalized_temperature <= threshold
    ):
        raise ValueError("frozen high candidate differs from stored observation")
    view = deepcopy(row["raw_payload"])
    view["warehouse_id"] = str(row["warehouse_id"])
    view["received_at"] = _utc_text(row["first_received_at_utc"])
    processed = process_batch([view], threshold)[0]
    if processed["normalized"] != normalized or processed["quality"] != quality:
        raise ValueError("stored quality differs from raw evidence")
    rebuilt = build_candidate_preview(
        processed,
        observation_id=str(observation_id),
        source_event_id=str(expected_source),
        rule={
            "warehouse_id": frozen["warehouse_id"],
            "batch_id": frozen["batch_id"],
            "rule_snapshot_id": frozen["rule_snapshot_id"],
            "rule_version": frozen["rule_version"],
            "metric_code": "temperature",
            "unit": "°C",
            "comparison": ">",
            "severity": frozen["severity"],
            "threshold": threshold,
            "effective_from": frozen["sampled_at"],
            "effective_to": None,
        },
    )
    if rebuilt != frozen:
        raise ValueError("frozen high candidate differs from raw evidence")
    staged = stage_f03_on_connection_candidate(
        connection,
        source_event_id=expected_source,
        observation_id=observation_id,
        body=frozen,
        now=now,
    )
    if staged == "conflict":
        return EvaluationResultCandidate(
            "held_unknown", observation_id, expected_source, "candidate_body_conflict"
        )
    risk = {
        "state": "candidate",
        "reason": "threshold_exceeded",
        "temperature_c": normalized_temperature,
        "threshold_c": threshold,
        "rule_snapshot_id": frozen["rule_snapshot_id"],
        "rule_version": frozen["rule_version"],
    }
    changed = mark_evaluation_on_connection_candidate(
        connection, observation_id=observation_id, state="candidate_staged", risk=risk
    )
    return EvaluationResultCandidate(
        "candidate_staged" if changed else "not_due", observation_id, expected_source
    )


def stage_gated_high_candidate(
    engine: Engine,
    *,
    device_id: UUID,
    warehouse_id: UUID,
    stream_epoch: UUID,
    observation_id: UUID,
    batch_id: UUID,
    body: dict[str, Any],
    now: datetime,
) -> EvaluationResultCandidate:
    """Commit a preselected high candidate under A's device evidence gate.

    The caller obtains B's historical rule and builds ``body`` before calling
    this port. A's gate, observation ownership check, outbox, risk and job state
    share one PostgreSQL transaction. A replay that has nothing to write rolls
    back the gate revision. A conflicting source ID keeps the stream held.
    This port performs no B I/O and does not install a worker or sender.
    """
    if not all(
        isinstance(value, UUID)
        for value in (device_id, warehouse_id, stream_epoch, observation_id, batch_id)
    ):
        raise ValueError(
            "trusted device, warehouse, epoch, observation and batch UUIDs required"
        )
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            begin_evidence_write(
                connection,
                device_id=device_id,
                warehouse_id=warehouse_id,
                epoch=stream_epoch,
            )
            owner = connection.execute(
                select(observations.c.device_id, observations.c.warehouse_id).where(
                    observations.c.id == observation_id
                )
            ).one_or_none()
            if owner is None or tuple(owner) != (device_id, warehouse_id):
                raise ValueError("observation does not belong to gated device")
            result = stage_evaluated_high_on_connection_candidate(
                connection,
                observation_id=observation_id,
                batch_id=batch_id,
                body=body,
                now=now,
            )
            if result.status == "held_unknown":
                mark_uncertain(connection, device_id=device_id, epoch=stream_epoch)
            if result.status in {"candidate_staged", "held_unknown"}:
                transaction.commit()
            else:
                transaction.rollback()
            return result
        except BaseException:
            if transaction.is_active:
                transaction.rollback()
            raise


def stage_gated_within_rule_candidate(
    engine: Engine,
    *,
    device_id: UUID,
    warehouse_id: UUID,
    stream_epoch: UUID,
    observation_id: UUID,
    batch_id: UUID,
    selected_rule: dict[str, Any],
) -> EvaluationResultCandidate:
    """Mark one clean, within-rule observation under A's evidence gate.

    The caller selects B's historical F02 rule before entry. This port
    rechecks its identity, interval and threshold against stored raw evidence.
    It never marks missing, unruled or high evidence normal. One observation
    still has one evaluation job, so this is not a multi-batch finalizer.
    """
    if not all(
        isinstance(value, UUID)
        for value in (device_id, warehouse_id, stream_epoch, observation_id, batch_id)
    ):
        raise ValueError(
            "trusted device, warehouse, epoch, observation and batch required"
        )
    if not isinstance(selected_rule, dict):
        raise ValueError("selected_rule must be a B F02 rule object")
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            begin_evidence_write(
                connection,
                device_id=device_id,
                warehouse_id=warehouse_id,
                epoch=stream_epoch,
            )
            row = (
                connection.execute(
                    select(observations, evaluation_jobs.c.state)
                    .join(
                        evaluation_jobs,
                        evaluation_jobs.c.observation_id == observations.c.id,
                    )
                    .where(observations.c.id == observation_id)
                    .with_for_update(of=evaluation_jobs)
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["state"] != "pending":
                transaction.rollback()
                return EvaluationResultCandidate("not_due", observation_id)
            if row["device_id"] != device_id or row["warehouse_id"] != warehouse_id:
                transaction.rollback()
                return EvaluationResultCandidate(
                    "held_unknown", observation_id, reason="observation_scope_mismatch"
                )
            try:
                normalized, quality = row["normalized"], row["quality"]
                if not isinstance(normalized, dict) or quality != ["ok"]:
                    raise ValueError("observation quality is not clean")
                sampled_at = _required_utc(normalized.get("sampled_at"), "sampled_at")
                metrics = normalized.get("metrics")
                value = _number(
                    metrics.get("temperature") if isinstance(metrics, dict) else None
                )
                threshold = _number(selected_rule.get("threshold"))
                if value is None or threshold is None or value > threshold:
                    raise ValueError("temperature is missing or above threshold")
                if (
                    any(
                        selected_rule.get(key) != expected
                        for key, expected in {
                            "warehouse_id": str(warehouse_id),
                            "batch_id": str(batch_id),
                            "metric_code": "temperature",
                            "kind": "threshold_high",
                            "comparison": ">",
                            "unit": "°C",
                        }.items()
                    )
                    or selected_rule.get("severity") not in F03_SEVERITIES
                ):
                    raise ValueError("unsupported historical rule")
                snapshot_id = _required_uuid(
                    selected_rule.get("snapshot_id"), "snapshot_id"
                )
                version = _required_text(
                    selected_rule.get("rule_version"), "rule_version"
                )
                starts = _required_utc(
                    selected_rule.get("effective_start_at"), "effective_start_at"
                )
                ends = (
                    _required_utc(selected_rule["effective_end_at"], "effective_end_at")
                    if selected_rule.get("effective_end_at") is not None
                    else None
                )
                if sampled_at < starts or (ends is not None and sampled_at >= ends):
                    raise ValueError("rule is outside sample interval")
                view = deepcopy(row["raw_payload"])
                view["warehouse_id"] = str(warehouse_id)
                view["received_at"] = _utc_text(row["first_received_at_utc"])
                processed = process_batch([view], threshold)[0]
                if (
                    processed["normalized"] != normalized
                    or processed["quality"] != quality
                ):
                    raise ValueError("stored quality differs from raw evidence")
            except (KeyError, TypeError, ValueError):  # fmt: skip
                transaction.rollback()
                return EvaluationResultCandidate(
                    "held_unknown", observation_id, reason="invalid_rule_or_evidence"
                )
            risk = {
                "state": "normal",
                "temperature_c": value,
                "threshold_c": threshold,
                "rule_snapshot_id": str(snapshot_id),
                "rule_version": version,
            }
            changed = mark_evaluation_on_connection_candidate(
                connection,
                observation_id=observation_id,
                state="within_rule",
                risk=risk,
            )
            if changed:
                transaction.commit()
                return EvaluationResultCandidate("within_rule", observation_id)
            transaction.rollback()
            return EvaluationResultCandidate("not_due", observation_id)
        except BaseException:
            if transaction.is_active:
                transaction.rollback()
            raise


def select_and_stage_gated_high_candidate(
    engine: Engine,
    *,
    device_id: UUID,
    warehouse_id: UUID,
    stream_epoch: UUID,
    observation_id: UUID,
    batch_id: UUID,
    selector: F02SelectionPort,
    now: datetime,
) -> EvaluationResultCandidate:
    """Read one historical B rule before entering A's gated C write transaction.

    The batch ID must come from a trusted device/warehouse association. This
    high-only opt-in path leaves no-rule, incomplete, and within-rule samples
    pending/unknown; it never promotes them to normal. It is not a scheduler.
    """
    if not all(
        isinstance(value, UUID)
        for value in (device_id, warehouse_id, stream_epoch, observation_id, batch_id)
    ):
        raise ValueError(
            "trusted device, warehouse, epoch, observation and batch required"
        )
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    try:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    select(observations, evaluation_jobs.c.state)
                    .join(
                        evaluation_jobs,
                        evaluation_jobs.c.observation_id == observations.c.id,
                    )
                    .where(observations.c.id == observation_id)
                )
                .mappings()
                .one_or_none()
            )
    except SQLAlchemyError:
        return EvaluationResultCandidate(
            "retryable_failure", observation_id, reason="storage_unavailable"
        )
    if row is None or row["state"] != "pending":
        return EvaluationResultCandidate("not_due", observation_id)
    if row["device_id"] != device_id or row["warehouse_id"] != warehouse_id:
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="observation_scope_mismatch"
        )
    normalized, quality = row["normalized"], row["quality"]
    if not isinstance(normalized, dict) or not isinstance(quality, list):
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="invalid_stored_quality"
        )
    try:
        sampled_at = _required_utc(normalized.get("sampled_at"), "sampled_at")
    except ValueError:
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="unusable_sampled_at"
        )
    try:
        selection = selector.select_rules_for_observation(
            warehouse_id=warehouse_id,
            batch_ids=(batch_id,),
            metric_code="temperature",
            sampled_at=sampled_at,
        )
    except SQLAlchemyError:
        return EvaluationResultCandidate(
            "retryable_failure", observation_id, reason="rule_store_unavailable"
        )
    except (TimeoutError, ConnectionError, PermissionError):  # fmt: skip
        return EvaluationResultCandidate(
            "retryable_failure", observation_id, reason="rule_unavailable"
        )
    except ValueError:
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="invalid_rule_selection"
        )
    if not isinstance(selection, dict) or selection.get("status") != "configured":
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="no_usable_rule"
        )
    rules = selection.get("rules")
    if not isinstance(rules, list) or len(rules) != 1 or not isinstance(rules[0], dict):
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="invalid_rule_selection"
        )
    rule = rules[0]
    if (
        rule.get("warehouse_id") != str(warehouse_id)
        or rule.get("batch_id") != str(batch_id)
        or rule.get("metric_code") != "temperature"
        or rule.get("kind") != "threshold_high"
        or rule.get("comparison") != ">"
        or rule.get("unit") != "°C"
        or rule.get("severity") not in F03_SEVERITIES
    ):
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="unsupported_rule"
        )
    try:
        threshold = _number(rule.get("threshold"))
        if threshold is None:
            raise ValueError("invalid threshold")
        snapshot_id = _required_uuid(rule.get("snapshot_id"), "snapshot_id")
        _required_text(rule.get("rule_version"), "rule_version")
        starts = _required_utc(rule.get("effective_start_at"), "effective_start_at")
        ends = (
            _required_utc(rule["effective_end_at"], "effective_end_at")
            if rule.get("effective_end_at") is not None
            else None
        )
        if sampled_at < starts or (ends is not None and sampled_at >= ends):
            raise ValueError("rule outside historical sample interval")
        view = deepcopy(row["raw_payload"])
        view["warehouse_id"] = str(warehouse_id)
        view["received_at"] = _utc_text(row["first_received_at_utc"])
        processed = process_batch([view], threshold)[0]
        if processed["normalized"] != normalized or processed["quality"] != quality:
            raise ValueError("stored quality differs from raw evidence")
        source_id = uuid5(
            _SOURCE_NAMESPACE, f"{observation_id}:{batch_id}:temperature_high"
        )
        body = build_candidate_preview(
            processed,
            observation_id=str(observation_id),
            source_event_id=str(source_id),
            rule={
                **rule,
                "rule_snapshot_id": snapshot_id,
                "effective_from": rule["effective_start_at"],
                "effective_to": rule.get("effective_end_at"),
            },
        )
    except (KeyError, TypeError, ValueError):  # fmt: skip
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="invalid_rule_or_evidence"
        )
    if body is None:
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="not_a_high_candidate"
        )
    return stage_gated_high_candidate(
        engine,
        device_id=device_id,
        warehouse_id=warehouse_id,
        stream_epoch=stream_epoch,
        observation_id=observation_id,
        batch_id=batch_id,
        body=body,
        now=now,
    )


def _mark(
    engine: Engine,
    *,
    observation_id: UUID,
    state: str,
    risk: dict[str, Any] | None = None,
) -> bool:
    with engine.begin() as connection:
        return mark_evaluation_on_connection_candidate(
            connection, observation_id=observation_id, state=state, risk=risk
        )


def evaluate_one_batch_candidate(
    engine: Engine,
    *,
    observation_id: UUID,
    batch_id: UUID,
    selector: F02SelectionPort,
    now: datetime,
) -> EvaluationResultCandidate:
    """Stage a durable high-temperature candidate using one historical B rule.

    The same deterministic source ID/body is retried after a crash between
    outbox staging and job completion. Until both persist, risk remains
    conservatively unknown. This first-scenario path does not resolve
    multi-batch evaluation or recovery windows.
    """
    if not isinstance(observation_id, UUID) or not isinstance(batch_id, UUID):
        raise ValueError("observation_id and batch_id must be UUIDs")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    try:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    select(observations, evaluation_jobs.c.state)
                    .join(
                        evaluation_jobs,
                        evaluation_jobs.c.observation_id == observations.c.id,
                    )
                    .where(observations.c.id == observation_id)
                )
                .mappings()
                .one_or_none()
            )
    except SQLAlchemyError:
        return EvaluationResultCandidate(
            "retryable_failure", observation_id, reason="storage_unavailable"
        )
    if row is None or row["state"] != "pending":
        return EvaluationResultCandidate("not_due", observation_id)
    normalized = row["normalized"]
    quality = row["quality"]
    if not isinstance(normalized, dict) or not isinstance(quality, list):
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="invalid_stored_quality"
        )
    sampled_text = normalized.get("sampled_at")
    metrics = normalized.get("metrics")
    temperature = metrics.get("temperature") if isinstance(metrics, dict) else None
    if not isinstance(sampled_text, str) or not isinstance(temperature, (int, float)):
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="unusable_temperature"
        )
    try:
        sampled_at = datetime.fromisoformat(sampled_text.replace("Z", "+00:00"))
    except ValueError:
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="unusable_sampled_at"
        )
    # Staging and job completion use separate transactions. A crash between
    # them must finish the first frozen event, even if a later F02 read would
    # choose another historical snapshot for the same observation.
    try:
        with engine.connect() as connection:
            staged_rows = (
                connection.execute(
                    select(outbox.c.source_event_id, outbox.c.body).where(
                        outbox.c.observation_id == observation_id
                    )
                )
                .mappings()
                .all()
            )
    except SQLAlchemyError:
        return EvaluationResultCandidate(
            "retryable_failure", observation_id, reason="storage_unavailable"
        )
    if staged_rows:
        if len(staged_rows) != 1:
            return EvaluationResultCandidate(
                "held_unknown", observation_id, reason="multiple_staged_candidates"
            )
        frozen = staged_rows[0]["body"]
        evidence = frozen.get("evidence") if isinstance(frozen, dict) else None
        if (
            not isinstance(evidence, dict)
            or frozen.get("kind") != "temperature_high"
            or frozen.get("batch_id") != str(batch_id)
            or frozen.get("warehouse_id") != str(row["warehouse_id"])
            or frozen.get("observation_ids") != [str(observation_id)]
            or frozen.get("source_event_id") != str(staged_rows[0]["source_event_id"])
            or frozen.get("sampled_at") != sampled_text
            or frozen.get("value") != temperature
            or evidence.get("quality") != quality
            or _number(evidence.get("threshold")) is None
        ):
            return EvaluationResultCandidate(
                "held_unknown", observation_id, reason="staged_candidate_mismatch"
            )
        risk = {
            "state": "candidate",
            "reason": "threshold_exceeded",
            "temperature_c": temperature,
            "threshold_c": evidence.get("threshold"),
            "rule_snapshot_id": frozen.get("rule_snapshot_id"),
            "rule_version": frozen.get("rule_version"),
        }
        try:
            changed = _mark(
                engine,
                observation_id=observation_id,
                state="candidate_staged",
                risk=risk,
            )
        except SQLAlchemyError:
            return EvaluationResultCandidate(
                "retryable_failure", observation_id, reason="storage_unavailable"
            )
        return EvaluationResultCandidate(
            "candidate_staged" if changed else "not_due",
            observation_id,
            staged_rows[0]["source_event_id"],
        )
    warehouse_id = row["warehouse_id"]
    try:
        selection = selector.select_rules_for_observation(
            warehouse_id=warehouse_id,
            batch_ids=(batch_id,),
            metric_code="temperature",
            sampled_at=sampled_at,
        )
    except ValueError:
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="invalid_rule_selection"
        )
    except SQLAlchemyError:
        return EvaluationResultCandidate(
            "retryable_failure", observation_id, reason="rule_store_unavailable"
        )
    except PermissionError:
        return EvaluationResultCandidate(
            "retryable_failure", observation_id, reason="rule_auth_unavailable"
        )
    except (TimeoutError, ConnectionError):  # fmt: skip
        return EvaluationResultCandidate(
            "retryable_failure", observation_id, reason="rule_unavailable"
        )
    if not isinstance(selection, dict):
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="invalid_rule_selection"
        )
    status = selection.get("status")
    if status == "no_rule" and selection.get("rules") == []:
        try:
            changed = _mark(engine, observation_id=observation_id, state="no_rule")
        except SQLAlchemyError:
            return EvaluationResultCandidate(
                "retryable_failure", observation_id, reason="storage_unavailable"
            )
        return EvaluationResultCandidate(
            "no_rule" if changed else "not_due", observation_id
        )
    rules = selection.get("rules")
    if status != "configured" or not isinstance(rules, list) or len(rules) != 1:
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="invalid_rule_selection"
        )
    selected = rules[0]
    if not isinstance(selected, dict):
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="invalid_rule_selection"
        )
    if (
        selected.get("batch_id") != str(batch_id)
        or selected.get("warehouse_id") != str(warehouse_id)
        or selected.get("metric_code") != "temperature"
        # B F02 names the rule shape `threshold_high`; C F03 emits the
        # temperature-specific event kind `temperature_high` below.
        or selected.get("kind") != "threshold_high"
        or selected.get("comparison") != ">"
        or selected.get("unit") != "°C"
    ):
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="unsupported_rule"
        )
    try:
        snapshot_id = _required_uuid(selected.get("snapshot_id"), "snapshot_id")
        rule_version = _required_text(selected.get("rule_version"), "rule_version")
        if selected.get("severity") not in F03_SEVERITIES:
            raise ValueError("invalid rule severity")
        threshold = _number(selected.get("threshold"))
        if threshold is None:
            raise ValueError("invalid rule threshold")
        starts = _required_utc(selected.get("effective_start_at"), "effective_start_at")
        ends = (
            _required_utc(selected["effective_end_at"], "effective_end_at")
            if selected.get("effective_end_at") is not None
            else None
        )
        if sampled_at < starts or (ends is not None and sampled_at >= ends):
            raise ValueError("rule is outside sampled time")
        view = deepcopy(row["raw_payload"])
        view["warehouse_id"] = str(warehouse_id)
        view["received_at"] = _utc_text(row["first_received_at_utc"])
        processed = process_batch([view], threshold)[0]
        if processed["normalized"] != normalized or processed["quality"] != quality:
            raise ValueError("stored quality differs from raw evidence")
        source_id = uuid5(
            _SOURCE_NAMESPACE,
            f"{observation_id}:{batch_id}:temperature_high",
        )
        candidate = build_candidate_preview(
            processed,
            observation_id=str(observation_id),
            source_event_id=str(source_id),
            rule={
                **selected,
                "rule_snapshot_id": snapshot_id,
                "effective_from": selected["effective_start_at"],
                "effective_to": selected.get("effective_end_at"),
            },
        )
    except (KeyError, TypeError, ValueError):  # fmt: skip
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="invalid_rule_or_evidence"
        )
    if candidate is None:
        if quality != ["ok"]:
            return EvaluationResultCandidate(
                "held_unknown", observation_id, reason="incomplete_observation"
            )
        risk = {
            "state": "normal",
            "reason": "within_threshold",
            "temperature_c": temperature,
            "threshold_c": threshold,
            "rule_snapshot_id": snapshot_id,
            "rule_version": rule_version,
        }
        try:
            changed = _mark(
                engine, observation_id=observation_id, state="within_rule", risk=risk
            )
        except SQLAlchemyError:
            return EvaluationResultCandidate(
                "retryable_failure", observation_id, reason="storage_unavailable"
            )
        return EvaluationResultCandidate(
            "within_rule" if changed else "not_due", observation_id
        )
    try:
        staged = stage_f03_candidate(
            engine,
            source_event_id=source_id,
            observation_id=observation_id,
            body=candidate,
            now=now,
        )
        if staged == "conflict":
            return EvaluationResultCandidate(
                "held_unknown", observation_id, source_id, "candidate_body_conflict"
            )
        risk = {
            "state": "candidate",
            "reason": "threshold_exceeded",
            "temperature_c": temperature,
            "threshold_c": threshold,
            "rule_snapshot_id": snapshot_id,
            "rule_version": rule_version,
        }
        changed = _mark(
            engine, observation_id=observation_id, state="candidate_staged", risk=risk
        )
    except ValueError:
        return EvaluationResultCandidate(
            "held_unknown", observation_id, reason="invalid_staged_candidate"
        )
    except SQLAlchemyError:
        return EvaluationResultCandidate(
            "retryable_failure", observation_id, reason="storage_unavailable"
        )
    return EvaluationResultCandidate(
        "candidate_staged" if changed else "not_due", observation_id, source_id
    )
