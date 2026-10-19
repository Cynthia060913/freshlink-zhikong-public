"""Isolated durable recovery-window port; not a production migration or worker.

The caller supplies a verified historical B rule and a C-persisted observation.
This port freezes that rule with the original anomaly and saves each transition
atomically. An eligible window still needs a separate durable outbox decision and
B acceptance before any alert may be shown as resolved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.modules.telemetry.recovery_window_candidate import (
    RecoveryDecision,
    RecoveryRuleCandidate,
    RecoverySampleCandidate,
    RecoveryWindowCandidate,
    advance_recovery_window,
)

metadata = MetaData()

recovery_progress = Table(
    "telemetry_recovery_progress_candidate",
    metadata,
    Column("original_source_event_id", Uuid(as_uuid=True), primary_key=True),
    Column("device_id", Uuid(as_uuid=True), nullable=False),
    Column("warehouse_id", Uuid(as_uuid=True), nullable=False),
    Column("batch_id", Uuid(as_uuid=True), nullable=False),
    Column("anomaly_sampled_at_utc", String(35), nullable=False),
    Column("comparison", String(2), nullable=False),
    Column("threshold_c", Float, nullable=False),
    Column("min_samples", Integer, nullable=False),
    Column("max_gap_seconds", Integer, nullable=False),
    Column("last_sampled_at_utc", String(35)),
    Column("last_matching_at_utc", String(35)),
    Column("observation_ids", JSON, nullable=False),
    Column("eligible", Boolean, nullable=False),
    Column("version", Integer, nullable=False),
)


def _utc_text(value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("recovery timestamp must be timezone aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _stored_window(row: Any) -> RecoveryWindowCandidate:
    return RecoveryWindowCandidate(
        original_source_event_id=row["original_source_event_id"],
        device_id=row["device_id"],
        warehouse_id=row["warehouse_id"],
        batch_id=row["batch_id"],
        anomaly_sampled_at=datetime.fromisoformat(
            row["anomaly_sampled_at_utc"].replace("Z", "+00:00")
        ),
        last_sampled_at=(
            datetime.fromisoformat(row["last_sampled_at_utc"].replace("Z", "+00:00"))
            if row["last_sampled_at_utc"] is not None
            else None
        ),
        observation_ids=tuple(UUID(value) for value in row["observation_ids"]),
        last_matching_at=(
            datetime.fromisoformat(row["last_matching_at_utc"].replace("Z", "+00:00"))
            if row["last_matching_at_utc"] is not None
            else None
        ),
        eligible=row["eligible"],
    )


def _check_frozen(
    row: Any, initial: RecoveryWindowCandidate, rule: RecoveryRuleCandidate
) -> None:
    expected = (
        initial.device_id,
        initial.warehouse_id,
        initial.batch_id,
        _utc_text(initial.anomaly_sampled_at),
        rule.comparison,
        rule.threshold_c,
        rule.min_samples,
        rule.max_gap_seconds,
    )
    actual = tuple(
        row[key]
        for key in (
            "device_id",
            "warehouse_id",
            "batch_id",
            "anomaly_sampled_at_utc",
            "comparison",
            "threshold_c",
            "min_samples",
            "max_gap_seconds",
        )
    )
    if actual != expected:
        raise ValueError("original recovery identity or frozen rule changed")


def load_recovery_progress(
    engine: Engine, original_source_event_id: UUID
) -> RecoveryWindowCandidate | None:
    """Read the stored window after restart, without claiming alert resolution."""
    with engine.connect() as connection:
        row = (
            connection.execute(
                select(recovery_progress).where(
                    recovery_progress.c.original_source_event_id
                    == original_source_event_id
                )
            )
            .mappings()
            .first()
        )
    return _stored_window(row) if row is not None else None


def advance_persisted_recovery_window(
    engine: Engine,
    initial: RecoveryWindowCandidate,
    rule: RecoveryRuleCandidate,
    sample: RecoverySampleCandidate,
) -> tuple[RecoveryWindowCandidate, RecoveryDecision]:
    """Save one decision with compare-and-swap; concurrent workers cannot lose a step.

    The schema is a C-owned isolated candidate. A reviewed C Alembic revision
    must later follow the merged B01 head; this function never creates tables.
    """
    if (
        initial.last_sampled_at is not None
        or initial.observation_ids
        or initial.eligible
    ):
        raise ValueError("initial recovery window must be empty")
    anomaly_time = _utc_text(initial.anomaly_sampled_at)
    for _ in range(8):
        try:
            with engine.begin() as connection:
                row = (
                    connection.execute(
                        select(recovery_progress).where(
                            recovery_progress.c.original_source_event_id
                            == initial.original_source_event_id
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    connection.execute(
                        recovery_progress.insert().values(
                            original_source_event_id=initial.original_source_event_id,
                            device_id=initial.device_id,
                            warehouse_id=initial.warehouse_id,
                            batch_id=initial.batch_id,
                            anomaly_sampled_at_utc=anomaly_time,
                            comparison=rule.comparison,
                            threshold_c=rule.threshold_c,
                            min_samples=rule.min_samples,
                            max_gap_seconds=rule.max_gap_seconds,
                            last_sampled_at_utc=None,
                            last_matching_at_utc=None,
                            observation_ids=[],
                            eligible=False,
                            version=0,
                        )
                    )
                    row = (
                        connection.execute(
                            select(recovery_progress).where(
                                recovery_progress.c.original_source_event_id
                                == initial.original_source_event_id
                            )
                        )
                        .mappings()
                        .one()
                    )
                _check_frozen(row, initial, rule)
                before = _stored_window(row)
                after, decision = advance_recovery_window(before, sample, rule)
                if after == before:
                    return before, decision
                changed = connection.execute(
                    update(recovery_progress)
                    .where(
                        recovery_progress.c.original_source_event_id
                        == initial.original_source_event_id,
                        recovery_progress.c.version == row["version"],
                    )
                    .values(
                        last_sampled_at_utc=(
                            _utc_text(after.last_sampled_at)
                            if after.last_sampled_at is not None
                            else None
                        ),
                        last_matching_at_utc=(
                            _utc_text(after.last_matching_at)
                            if after.last_matching_at is not None
                            else None
                        ),
                        observation_ids=[str(value) for value in after.observation_ids],
                        eligible=after.eligible,
                        version=row["version"] + 1,
                    )
                ).rowcount
                if changed == 1:
                    return after, decision
        except IntegrityError:
            # Another worker may have inserted the same original anomaly.
            # Retry only if that row now exists; otherwise surface the error.
            if load_recovery_progress(engine, initial.original_source_event_id) is None:
                raise
    raise RuntimeError("recovery progress changed concurrently too many times")
