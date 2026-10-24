"""Opt-in one-batch high-temperature caller using real B F02 and C's gate.

No scheduling/startup, DDL, B delivery, recovery or external notification here.
Multiple active batches deliberately remain pending until C's per-batch job
contract exists; never silently evaluate only the first of several batches.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.modules.alerts.services.batches import (
    BatchNotFound,
    list_batches_for_warehouse,
)
from app.modules.alerts.services.rules import select_rules_for_observation
from app.modules.alerts.services.warehouses import WarehouseNotFound
from app.modules.iot.models import Device
from app.modules.iot.stream_frontier import FrontierUnavailable
from app.modules.telemetry.candidate_preview import (
    _required_utc,
    build_candidate_preview,
)
from app.modules.telemetry.quality import _number, process_batch
from app.modules.telemetry.rule_evaluation_candidate import (
    _SOURCE_NAMESPACE,
    EvaluationResultCandidate,
    stage_gated_high_candidate,
)
from app.modules.telemetry.storage_candidate import evaluation_jobs, observations


class GatedHighWorker:
    """Explicit bounded caller; only high candidates change C's pending job.

    Rules are read from B before C opens its gated transaction. The C port
    rechecks stored ownership/raw evidence and commits outbox/risk/job with
    A's revision. No network call occurs while the evidence lock is held.
    Binding mutation linearization and multi-batch/normal/recovery writers
    remain separate acceptance items; do not enable an unattended E1 sender.
    """

    def __init__(self, engine: Engine, *, frontier_epochs: Mapping[UUID, UUID]):
        epochs = dict(frontier_epochs)
        if not 1 <= len(epochs) <= 14 or not all(
            isinstance(device, UUID) and isinstance(epoch, UUID)
            for device, epoch in epochs.items()
        ):
            raise ValueError("1-14 explicit trusted device/epoch UUIDs required")
        self.engine = engine
        self._epochs = epochs

    def evaluate(
        self, observation_id: UUID, *, now: datetime
    ) -> EvaluationResultCandidate:
        if not isinstance(observation_id, UUID) or now.tzinfo is None:
            raise ValueError("observation UUID and aware time required")

        def held(reason: str) -> EvaluationResultCandidate:
            return EvaluationResultCandidate(
                "held_unknown", observation_id, reason=reason
            )

        try:
            with self.engine.connect() as connection:
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
            if row is None or row["state"] != "pending":
                return EvaluationResultCandidate("not_due", observation_id)
            if not isinstance(row["normalized"], dict) or not isinstance(
                row["raw_payload"], dict
            ):
                return held("invalid_stored_evidence")
            identity, warehouse = row["device_id"], row["warehouse_id"]
            epoch = self._epochs.get(identity)
            if epoch is None:
                return held("unverified_stream_scope")
            sampled_at = _required_utc(
                row["normalized"].get("sampled_at"), "sampled_at"
            )
            with Session(self.engine) as session:
                device = session.get(Device, identity)
                if (
                    device is None
                    or device.kind != "temperature_sensor"
                    or device.warehouse_id != warehouse
                ):
                    return held("current_sensor_binding_mismatch")
                batches, total = list_batches_for_warehouse(
                    session, warehouse, limit=101
                )
                if total > 100:
                    return held("batch_scope_capacity")
                active = [
                    batch
                    for batch in batches
                    if batch.started_at <= sampled_at
                    and (batch.ended_at is None or sampled_at < batch.ended_at)
                ]
                if len(active) != 1:
                    return held("one_active_batch_required")
                batch_id = active[0].id
                selection = select_rules_for_observation(
                    session,
                    warehouse_id=warehouse,
                    batch_ids=(batch_id,),
                    metric_code="temperature",
                    sampled_at=sampled_at,
                ).model_dump(mode="json")
            if selection["status"] == "no_rule":
                return held("no_rule")
            rules = selection["rules"]
            if selection["status"] != "configured" or len(rules) != 1:
                return held("unsupported_rule_selection")
            rule = rules[0]
            if any(
                rule.get(key) != value
                for key, value in {
                    "warehouse_id": str(warehouse),
                    "batch_id": str(batch_id),
                    "kind": "temperature_high",
                    "metric_code": "temperature",
                    "unit": "°C",
                    "comparison": ">",
                }.items()
            ):
                return held("unsupported_rule")
            threshold = _number(rule.get("threshold"))
            if threshold is None:
                return held("invalid_threshold")
            view = dict(row["raw_payload"])
            view["warehouse_id"] = str(warehouse)
            received = row["first_received_at_utc"]
            if not isinstance(received, datetime) or received.tzinfo is None:
                return held("invalid_received_at")
            view["received_at"] = received.astimezone(UTC).isoformat()
            processed = process_batch([view], threshold)[0]
            if (
                processed["normalized"] != row["normalized"]
                or processed["quality"] != row["quality"]
            ):
                return held("stored_evidence_mismatch")
            source = uuid5(
                _SOURCE_NAMESPACE, f"{observation_id}:{batch_id}:temperature_high"
            )
            body = build_candidate_preview(
                processed,
                observation_id=str(observation_id),
                source_event_id=str(source),
                rule={
                    **rule,
                    "rule_snapshot_id": rule["snapshot_id"],
                    "effective_from": rule["effective_start_at"],
                    "effective_to": rule.get("effective_end_at"),
                },
            )
            if body is None:
                return held("not_high_candidate")
            return stage_gated_high_candidate(
                self.engine,
                device_id=identity,
                warehouse_id=warehouse,
                stream_epoch=epoch,
                observation_id=observation_id,
                batch_id=batch_id,
                body=body,
                now=now,
            )
        except SQLAlchemyError, FrontierUnavailable:
            return EvaluationResultCandidate(
                "retryable_failure", observation_id, reason="high_storage_unavailable"
            )
        except ValueError, TypeError, KeyError, BatchNotFound, WarehouseNotFound:
            return held("invalid_rule_or_evidence")

    def run_once(
        self, *, now: datetime, limit: int = 100
    ) -> list[EvaluationResultCandidate]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("limit must be 1-100")
        with self.engine.connect() as connection:
            ids = list(
                connection.scalars(
                    select(observations.c.id)
                    .join(
                        evaluation_jobs,
                        evaluation_jobs.c.observation_id == observations.c.id,
                    )
                    .where(evaluation_jobs.c.state == "pending")
                    .order_by(observations.c.first_received_at_utc, observations.c.id)
                    .limit(limit)
                )
            )
        return [self.evaluate(identity, now=now) for identity in ids]
