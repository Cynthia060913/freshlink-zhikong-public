"""Internal fixed-membership history snapshot over C's isolated test tables.

No public route or trusted actor is supplied here. Production F06 still needs
the reviewed migration, actual auth, signed cursors, capacity control and CSV.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Uuid,
    select,
)
from sqlalchemy.engine import Engine

from app.modules.telemetry.history_candidate import (
    HistoryFiltersCandidate,
    _conditions,
    _display_time,
    _project_row,
    _utc_bound,
)
from app.modules.telemetry.storage_candidate import metadata, observations

_TTL = timedelta(seconds=900)

snapshots = Table(
    "telemetry_snapshot_candidate",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("actor_id", Uuid(as_uuid=True), nullable=False),
    Column("filters_sha256", String(64), nullable=False),
    Column("policy_version", String(64), nullable=False),
    Column("created_at_utc", DateTime(timezone=True), nullable=False),
    Column("expires_at_utc", DateTime(timezone=True), nullable=False),
    Column("member_count", Integer, nullable=False),
)

snapshot_members = Table(
    "telemetry_snapshot_member_candidate",
    metadata,
    Column(
        "snapshot_id",
        Uuid(as_uuid=True),
        ForeignKey(snapshots.c.id),
        primary_key=True,
    ),
    Column("ordinal", Integer, primary_key=True),
    Column("observation_id", Uuid(as_uuid=True), ForeignKey(observations.c.id)),
    Column("projection", JSON, nullable=False),
)


@dataclass(frozen=True)
class SnapshotPageCandidate:
    snapshot_id: UUID
    items: list[dict[str, Any]]
    total: int
    next_after_ordinal: int | None


def _filters_digest(filters: HistoryFiltersCandidate) -> str:
    # Normalize equivalent timezone offsets before binding filter identity.
    fields: dict[str, str | None] = {}
    for name in (
        "device_id",
        "warehouse_id",
        "sampled_from",
        "sampled_to",
        "received_from",
        "received_to",
    ):
        value = getattr(filters, name)
        if name.endswith(("_from", "_to")):
            fields[name] = _display_time(_utc_bound(value, name))
        else:
            fields[name] = str(value) if value is not None else None
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _policy(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 64:
        raise ValueError("policy_version must be 1-64 characters")
    return value.strip()


def _now(value: datetime) -> datetime:
    normalized = _utc_bound(value, "now")
    if normalized is None:
        raise ValueError("now must be an aware datetime")
    return normalized


def _from_db(value: datetime) -> datetime:
    return (
        value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    )


def create_snapshot_candidate(
    engine: Engine,
    *,
    actor_id: UUID,
    filters: HistoryFiltersCandidate,
    policy_version: str,
    now: datetime,
    max_members: int = 10000,
) -> UUID:
    """Freeze ordered IDs and their public projection in one DB transaction.

    One SELECT statement sees one committed result set; members and metadata
    then commit together. A later old-received-time insert cannot join it.
    """
    if not isinstance(actor_id, UUID):
        raise ValueError("actor_id must be a UUID")
    if isinstance(max_members, bool) or not isinstance(max_members, int):
        raise ValueError("max_members must be an integer from 1 to 10000")
    if not 1 <= max_members <= 10000:
        raise ValueError("max_members must be an integer from 1 to 10000")
    clauses = _conditions(filters)
    fingerprint = _filters_digest(filters)
    policy = _policy(policy_version)
    created = _now(now)
    snapshot_id = uuid4()
    with engine.begin() as connection:
        selected = (
            connection.execute(
                select(
                    observations.c.id,
                    observations.c.device_id,
                    observations.c.warehouse_id,
                    observations.c.sampled_at_utc,
                    observations.c.first_received_at_utc,
                    observations.c.normalized,
                    observations.c.quality,
                    observations.c.risk,
                )
                .where(*clauses)
                .order_by(
                    observations.c.sampled_at_utc.asc().nulls_last(),
                    observations.c.id.asc(),
                )
                .limit(max_members + 1)
            )
            .mappings()
            .all()
        )
        if len(selected) > max_members:
            raise ValueError("snapshot_row_limit")
        connection.execute(
            snapshots.insert().values(
                id=snapshot_id,
                actor_id=actor_id,
                filters_sha256=fingerprint,
                policy_version=policy,
                created_at_utc=created,
                expires_at_utc=created + _TTL,
                member_count=len(selected),
            )
        )
        if selected:
            connection.execute(
                snapshot_members.insert(),
                [
                    {
                        "snapshot_id": snapshot_id,
                        "ordinal": ordinal,
                        "observation_id": row["id"],
                        "projection": _project_row(row),
                    }
                    for ordinal, row in enumerate(selected)
                ],
            )
    return snapshot_id


def read_snapshot_page_candidate(
    engine: Engine,
    *,
    snapshot_id: UUID,
    actor_id: UUID,
    filters: HistoryFiltersCandidate,
    policy_version: str,
    now: datetime,
    limit: int = 20,
    after_ordinal: int = -1,
) -> SnapshotPageCandidate:
    """Read an immutable page; caller-supplied actor is not authentication."""
    if not isinstance(snapshot_id, UUID) or not isinstance(actor_id, UUID):
        raise ValueError("invalid_snapshot")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    if isinstance(after_ordinal, bool) or not isinstance(after_ordinal, int):
        raise ValueError("after_ordinal must be an integer")
    if after_ordinal < -1:
        raise ValueError("after_ordinal must be at least -1")
    _conditions(filters)
    fingerprint = _filters_digest(filters)
    policy = _policy(policy_version)
    current = _now(now)
    with engine.connect() as connection:
        snapshot = (
            connection.execute(select(snapshots).where(snapshots.c.id == snapshot_id))
            .mappings()
            .one_or_none()
        )
        if (
            snapshot is None
            or snapshot["actor_id"] != actor_id
            or snapshot["filters_sha256"] != fingerprint
            or snapshot["policy_version"] != policy
            or _from_db(snapshot["expires_at_utc"]) <= current
        ):
            raise ValueError("invalid_snapshot")
        total = snapshot["member_count"]
        if after_ordinal >= total and after_ordinal != -1:
            raise ValueError("invalid_snapshot_cursor")
        rows = (
            connection.execute(
                select(snapshot_members.c.ordinal, snapshot_members.c.projection)
                .where(
                    snapshot_members.c.snapshot_id == snapshot_id,
                    snapshot_members.c.ordinal > after_ordinal,
                )
                .order_by(snapshot_members.c.ordinal.asc())
                .limit(limit + 1)
            )
            .mappings()
            .all()
        )
    has_next = len(rows) > limit
    page = rows[:limit]
    return SnapshotPageCandidate(
        snapshot_id=snapshot_id,
        items=[dict(row["projection"]) for row in page],
        total=total,
        next_after_ordinal=page[-1]["ordinal"] if has_next else None,
    )
