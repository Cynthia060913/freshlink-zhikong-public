"""Conservative check for C receipts omitted from the observation stream.

An invalid reception or conflicting replay has no new observation to scan.
Until a reviewed policy can reconcile it, recovery must stay unknown. This
only examines evidence already committed to C; it is not an A commit frontier.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Connection

from app.modules.telemetry.storage_candidate import (
    observations,
    reception_reuse_conflicts,
    receptions,
)


def _utc(value: object, *, stored_observation: bool = False) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("stored reception time is invalid") from exc
    if not isinstance(value, datetime):
        raise ValueError("stored reception time is invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        if not stored_observation:
            raise ValueError("stored reception time has no UTC offset")
        # SQLite tests drop the DateTime(timezone=True) offset; the original
        # observation was normalized to UTC before insertion.
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def has_unresolved_reception_evidence_candidate(
    connection: Connection,
    *,
    device_id: UUID,
    warehouse_id: UUID,
    original_received_at: datetime,
) -> bool:
    """Hold if any saved invalid/conflicting receipt is at/after the anomaly.

    Compare parsed UTC instants, not ISO strings whose fractional-second width
    can differ. This deliberately holds even if the bad receipt was committed
    after an apparently eligible window; a reviewer must resolve that case.
    """
    boundary = _utc(original_received_at, stored_observation=True)
    bad_receipts = connection.execute(
        select(receptions.c.received_at_utc)
        .outerjoin(observations, receptions.c.observation_id == observations.c.id)
        .where(
            receptions.c.outcome.in_(("invalid", "conflict")),
            or_(
                and_(
                    receptions.c.device_id == device_id,
                    receptions.c.warehouse_id == warehouse_id,
                ),
                and_(
                    receptions.c.outcome == "conflict",
                    observations.c.device_id == device_id,
                    observations.c.warehouse_id == warehouse_id,
                ),
            ),
        )
    ).scalars()
    if any(_utc(value) >= boundary for value in bad_receipts):
        return True
    reuse_attempts = connection.execute(
        select(reception_reuse_conflicts.c.received_at_utc)
        .join(
            receptions,
            reception_reuse_conflicts.c.claimed_reception_id == receptions.c.id,
        )
        .where(
            or_(
                and_(
                    reception_reuse_conflicts.c.device_id == device_id,
                    reception_reuse_conflicts.c.warehouse_id == warehouse_id,
                ),
                and_(
                    receptions.c.device_id == device_id,
                    receptions.c.warehouse_id == warehouse_id,
                ),
            )
        )
    ).scalars()
    return any(_utc(value) >= boundary for value in reuse_attempts)
