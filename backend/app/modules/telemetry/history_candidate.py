"""Bounded read from C's isolated observation candidate table.

This internal helper has no actor, signed cursor, fixed result snapshot or
public route. It is not the F06 service. It exercises real persisted rows and
the UTC/NULLS LAST order needed before the reviewed migration is available.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.engine import Engine, RowMapping

from app.modules.telemetry.storage_candidate import observations, quality_tags


@dataclass(frozen=True)
class HistoryFiltersCandidate:
    device_id: UUID | None = None
    warehouse_id: UUID | None = None
    sampled_from: datetime | None = None
    sampled_to: datetime | None = None
    received_from: datetime | None = None
    received_to: datetime | None = None
    quality_in: tuple[str, ...] | None = None


@dataclass(frozen=True)
class HistoryPageCandidate:
    items: list[dict[str, Any]]
    next_after_observation_id: UUID | None


def _utc_bound(value: datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be an aware datetime")
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value.astimezone(UTC)


def _display_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    # SQLite drops the timezone after reading a value stored in UTC. The
    # production PostgreSQL dialect returns an aware timestamp.
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _quality_values(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 32:
        raise ValueError("quality_in must contain 1-32 tags")
    if any(
        not isinstance(tag, str) or not 1 <= len(tag) <= 128 or not tag.strip()
        for tag in value
    ):
        raise ValueError("quality_in tags must be nonempty text up to 128 characters")
    return tuple(sorted(set(value)))


def _conditions(filters: HistoryFiltersCandidate) -> list[Any]:
    if not isinstance(filters, HistoryFiltersCandidate):
        raise ValueError("filters must be HistoryFiltersCandidate")
    clauses: list[Any] = []
    for name, column in (
        ("device_id", observations.c.device_id),
        ("warehouse_id", observations.c.warehouse_id),
    ):
        value = getattr(filters, name)
        if value is not None:
            if not isinstance(value, UUID):
                raise ValueError(f"{name} must be a UUID")
            clauses.append(column == value)
    for prefix, column in (
        ("sampled", observations.c.sampled_at_utc),
        ("received", observations.c.first_received_at_utc),
    ):
        start = _utc_bound(getattr(filters, f"{prefix}_from"), f"{prefix}_from")
        end = _utc_bound(getattr(filters, f"{prefix}_to"), f"{prefix}_to")
        if start is not None and end is not None and start >= end:
            raise ValueError(f"{prefix}_from must be before {prefix}_to")
        if start is not None:
            clauses.append(column >= start)
        if end is not None:
            clauses.append(column < end)
    quality_in = _quality_values(filters.quality_in)
    if quality_in is not None:
        clauses.append(
            exists().where(
                quality_tags.c.observation_id == observations.c.id,
                quality_tags.c.tag.in_(quality_in),
            )
        )
    return clauses


def _rule_version_for_history(risk: dict[str, Any]) -> str | None:
    """Keep F06's single column while preserving every multi-batch version.

    A single-batch evaluation retains its original version string. For a
    multi-batch evaluation, the cell contains a stable JSON array of
    batch_id/rule_version pairs; it must not pretend that one version applies
    to every batch. Incomplete saved evidence stays absent.
    """
    version = risk.get("rule_version")
    if isinstance(version, str) and version:
        return version
    batches = risk.get("batches")
    if not isinstance(batches, list) or not batches:
        return None
    pairs: list[dict[str, str]] = []
    seen: set[str] = set()
    for batch in batches:
        if not isinstance(batch, dict):
            return None
        batch_id = batch.get("batch_id")
        batch_version = batch.get("rule_version")
        if (
            not isinstance(batch_id, str)
            or not batch_id
            or batch_id in seen
            or not isinstance(batch_version, str)
            or not batch_version
        ):
            return None
        seen.add(batch_id)
        pairs.append({"batch_id": batch_id, "rule_version": batch_version})
    pairs.sort(key=lambda pair: pair["batch_id"])
    return json.dumps(pairs, ensure_ascii=False, separators=(",", ":"))


def _project_row(row: RowMapping) -> dict[str, Any]:
    metrics = row["normalized"].get("metrics", {})
    return {
        "observation_id": str(row["id"]),
        "device_id": str(row["device_id"]),
        "warehouse_id": str(row["warehouse_id"]),
        "sampled_at": _display_time(row["sampled_at_utc"]),
        "received_at": _display_time(row["first_received_at_utc"]),
        "temperature_c": metrics.get("temperature"),
        "humidity_percent": metrics.get("humidity"),
        "quality": list(row["quality"]),
        "risk_state": row["risk"]["state"],
        "rule_version": _rule_version_for_history(row["risk"]),
    }


def latest_observation_candidate(
    engine: Engine,
    *,
    device_id: UUID | None = None,
    warehouse_id: UUID | None = None,
) -> dict[str, Any] | None:
    """Read one newly saved observation by trusted first-reception order.

    Replays do not replace the latest measurement. This is a live read, not a
    frozen history snapshot or a batch-specific risk conclusion.
    """
    if device_id is None and warehouse_id is None:
        raise ValueError("device_id or warehouse_id is required")
    clauses = []
    for name, value, column in (
        ("device_id", device_id, observations.c.device_id),
        ("warehouse_id", warehouse_id, observations.c.warehouse_id),
    ):
        if value is not None:
            if not isinstance(value, UUID):
                raise ValueError(f"{name} must be a UUID")
            clauses.append(column == value)
    with engine.connect() as connection:
        row = (
            connection.execute(
                select(observations)
                .where(*clauses)
                .order_by(
                    observations.c.first_received_at_utc.desc(),
                    observations.c.id.desc(),
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
    return _project_row(row) if row is not None else None


def list_history_candidate(
    engine: Engine,
    filters: HistoryFiltersCandidate,
    *,
    limit: int = 20,
    after_observation_id: UUID | None = None,
) -> HistoryPageCandidate:
    """Read bounded persisted rows in sampled UTC order, invalid time last.

    A later insert may appear on a later page. The unsigned local ID is only
    a test marker and MUST NOT be exposed as an HTTP cursor or used for CSV.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    if after_observation_id is not None and not isinstance(after_observation_id, UUID):
        raise ValueError("after_observation_id must be a UUID")
    clauses = _conditions(filters)
    with engine.connect() as connection:
        if after_observation_id is not None:
            anchor = (
                connection.execute(
                    select(observations.c.sampled_at_utc, observations.c.id).where(
                        *clauses, observations.c.id == after_observation_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if anchor is None:
                raise ValueError("after_observation_id is not in the selection")
            sampled = anchor["sampled_at_utc"]
            if sampled is None:
                clauses.append(
                    and_(
                        observations.c.sampled_at_utc.is_(None),
                        observations.c.id > after_observation_id,
                    )
                )
            else:
                clauses.append(
                    or_(
                        observations.c.sampled_at_utc > sampled,
                        and_(
                            observations.c.sampled_at_utc == sampled,
                            observations.c.id > after_observation_id,
                        ),
                        observations.c.sampled_at_utc.is_(None),
                    )
                )
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
                .limit(limit + 1)
            )
            .mappings()
            .all()
        )
    has_next = len(selected) > limit
    page = selected[:limit]
    items = [_project_row(row) for row in page]
    return HistoryPageCandidate(
        items=items,
        next_after_observation_id=page[-1]["id"] if has_next else None,
    )
