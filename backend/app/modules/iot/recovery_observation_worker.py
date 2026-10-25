"""Explicit A caller: saved C samples, A gate, window and frozen local outbox.

No startup, schema creation, epoch repair, B HTTP or notification sending.
Historical B selection finishes before the A transaction starts. C's public
same-Connection ports retain ownership of evidence parsing and window rules.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col

from app.modules.alerts.models.warehouse import Warehouse
from app.modules.iot.models import Device
from app.modules.iot.stream_frontier import (
    FrontierUnavailable,
    begin_evidence_write,
    mark_uncertain,
    read_complete_frontier,
)
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.recovery_observation_candidate import (
    advance_saved_recovery_observation_on_connection_candidate,
)
from app.modules.telemetry.recovery_outbox_candidate import (
    _instant,
    freeze_recovery_frontier_on_connection_candidate,
    stage_eligible_recovery_on_connection_candidate,
)
from app.modules.telemetry.recovery_progress_candidate import recovery_progress
from app.modules.telemetry.recovery_tick_candidate import (
    HistoricalRecoveryRulePort,
    LocalBHistoricalRecoveryRuleCandidate,
)
from app.modules.telemetry.storage_candidate import observations


@dataclass(frozen=True)
class RecoveryObservationResult:
    status: Literal["progress", "staged", "duplicate", "held", "retryable_failure"]
    observation_id: UUID
    source_event_id: UUID | None = None
    decision: str | None = None
    reason: str = ""


def _version(connection: Connection, original_id: UUID) -> int | None:
    return connection.scalar(
        select(recovery_progress.c.version).where(
            recovery_progress.c.original_source_event_id == original_id
        )
    )


class GatedRecoveryObservationWorker:
    """Bounded, explicitly called preparation, never a recovery network sender.

    One original high event and one saved observation are processed atomically.
    All applicable batches are represented by their distinct original event IDs;
    choosing one original does not finish another batch's evaluation job.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        frontier_epochs: Mapping[UUID, UUID],
        historical_rules: HistoricalRecoveryRulePort | None = None,
    ) -> None:
        epochs = dict(frontier_epochs)
        if not 1 <= len(epochs) <= 14 or not all(
            isinstance(device, UUID) and isinstance(epoch, UUID)
            for device, epoch in epochs.items()
        ):
            raise ValueError("1-14 trusted device/epoch UUIDs required")
        self.engine = engine
        self._epochs = epochs
        self._rules = historical_rules or LocalBHistoricalRecoveryRuleCandidate(engine)

    def advance(
        self, original_source_event_id: UUID, observation_id: UUID, *, now: datetime
    ) -> RecoveryObservationResult:
        if (
            not all(
                isinstance(value, UUID)
                for value in (original_source_event_id, observation_id)
            )
            or not isinstance(now, datetime)
            or now.tzinfo is None
        ):
            raise ValueError("trusted UUIDs and aware time required")

        def held(reason: str) -> RecoveryObservationResult:
            return RecoveryObservationResult("held", observation_id, reason=reason)

        try:
            # Discovery and B reads CLOSE before taking A's evidence gate.
            with self.engine.connect() as connection:
                original = connection.execute(
                    select(outbox.c.state, outbox.c.body).where(
                        outbox.c.source_event_id == original_source_event_id
                    )
                ).one_or_none()
                owner = connection.execute(
                    select(observations.c.device_id, observations.c.warehouse_id).where(
                        observations.c.id == observation_id
                    )
                ).one_or_none()
            if original is None or original.state != "delivered":
                return held("original_not_delivered")
            body = original.body
            if not isinstance(body, dict) or body.get("kind") != "temperature_high":
                return held("unsupported_original")
            if owner is None or owner.device_id not in self._epochs:
                return held("unverified_stream_scope")
            batch_id = UUID(body["batch_id"])
            rule = self._rules.for_original(
                original_source_event_id, _instant(body.get("sampled_at"))
            )
            if rule is None:
                return held("historical_rule_missing")
            device_id, warehouse_id = owner
            epoch = self._epochs[device_id]
            with self.engine.connect() as connection:
                transaction = connection.begin()
                try:
                    begin_evidence_write(
                        connection,
                        device_id=device_id,
                        warehouse_id=warehouse_id,
                        epoch=epoch,
                    )
                    device = connection.execute(
                        select(col(Device.kind), col(Device.warehouse_id))
                        .where(col(Device.id) == device_id)
                        .with_for_update(read=True)
                    ).one_or_none()
                    warehouse = connection.scalar(
                        select(col(Warehouse.id))
                        .where(col(Warehouse.id) == warehouse_id)
                        .with_for_update(read=True, key_share=True)
                    )
                    if (
                        device is None
                        or device.kind != "temperature_sensor"
                        or (device.warehouse_id != warehouse_id or warehouse is None)
                    ):
                        raise FrontierUnavailable("current sensor binding unavailable")
                    # C rechecks original digest, owner, raw/quality and history
                    # using THIS connection, not its legacy Engine wrapper.
                    before = _version(connection, original_source_event_id)
                    advanced = (
                        advance_saved_recovery_observation_on_connection_candidate(
                            connection,
                            original_source_event_id=original_source_event_id,
                            observation_id=observation_id,
                            batch_id=batch_id,
                            historical_rule=rule,
                        )
                    )
                    if advanced.status != "advanced":
                        transaction.rollback()
                        return held("saved_evidence_or_rule_unavailable")
                    staged = stage_eligible_recovery_on_connection_candidate(
                        connection,
                        original_source_event_id=original_source_event_id,
                        historical_rule=rule,
                        now=now,
                    )
                    if staged.status == "staged":
                        stamp = read_complete_frontier(
                            connection,
                            device_id=device_id,
                            warehouse_id=warehouse_id,
                            epoch=epoch,
                        )
                        if stamp is None:
                            transaction.rollback()
                            return held("frontier_incomplete")
                        if staged.source_event_id is None:
                            raise ValueError("staged recovery identity unavailable")
                        freeze_recovery_frontier_on_connection_candidate(
                            connection,
                            recovery_source_event_id=staged.source_event_id,
                            original_source_event_id=original_source_event_id,
                            stamp=stamp,
                        )
                        transaction.commit()
                        return RecoveryObservationResult(
                            "staged",
                            observation_id,
                            staged.source_event_id,
                            advanced.decision,
                        )
                    if staged.status == "conflict":
                        mark_uncertain(connection, device_id=device_id, epoch=epoch)
                        transaction.commit()
                        return held("recovery_content_conflict")
                    changed = _version(connection, original_source_event_id) != before
                    if changed:
                        transaction.commit()
                    else:
                        # No-op polls must NOT invalidate an earlier frozen
                        # recovery by committing a useless A revision increment.
                        transaction.rollback()
                    if staged.status == "held_invalid_evidence":
                        return held("recovery_evidence_unavailable")
                    return RecoveryObservationResult(
                        "progress" if changed else "duplicate",
                        observation_id,
                        staged.source_event_id,
                        advanced.decision,
                    )
                except BaseException:
                    if transaction.is_active:
                        try:
                            transaction.rollback()
                        except SQLAlchemyError:
                            connection.invalidate()
                    else:
                        # A failed commit may have deactivated SQLAlchemy's
                        # transaction while the driver still has INTRANS state.
                        # Never return that uncertain connection to the pool.
                        connection.invalidate()
                    raise
        except SQLAlchemyError, FrontierUnavailable, RuntimeError:
            return RecoveryObservationResult(
                "retryable_failure",
                observation_id,
                reason="recovery_storage_unavailable",
            )
        except ValueError, TypeError, KeyError:
            return held("invalid_original_or_evidence")

    def advance_page(
        self, original_source_event_id: UUID, *, now: datetime, limit: int = 50
    ) -> tuple[RecoveryObservationResult, ...]:
        """One original, in durable receive order; rerun after interruptions.

        The durable C receipt watermark selects the next saved samples. The
        original observation is excluded, preventing a limit=1 starvation loop.
        This bounded method starts no thread and performs no network delivery.
        """
        if not isinstance(original_source_event_id, UUID) or (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("original UUID and limit 1-100 required")
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("aware time required")
        with self.engine.connect() as connection:
            original = (
                connection.execute(
                    select(observations)
                    .join(outbox, outbox.c.observation_id == observations.c.id)
                    .where(outbox.c.source_event_id == original_source_event_id)
                )
                .mappings()
                .one_or_none()
            )
            if original is None:
                return ()
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
            query = select(observations.c.id).where(
                observations.c.device_id == original["device_id"],
                observations.c.warehouse_id == original["warehouse_id"],
                observations.c.id != original["id"],
                observations.c.first_received_at_utc
                >= original["first_received_at_utc"],
            )
            if progress is not None and progress["last_received_at_utc"] is not None:
                last_at = _instant(progress["last_received_at_utc"])
                last_id = progress["last_received_observation_id"]
                query = query.where(
                    (observations.c.first_received_at_utc > last_at)
                    | (
                        (observations.c.first_received_at_utc == last_at)
                        & (observations.c.id > last_id)
                    )
                )
            ids = list(
                connection.scalars(
                    query.order_by(
                        observations.c.first_received_at_utc, observations.c.id
                    ).limit(limit)
                )
            )
        results = []
        for observation_id in ids:
            result = self.advance(original_source_event_id, observation_id, now=now)
            results.append(result)
            if result.status in {"staged", "held", "retryable_failure"}:
                break
        return tuple(results)
