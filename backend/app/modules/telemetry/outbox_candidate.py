"""C-only durable candidate handoff experiment on isolated test metadata.

This does not call B, install a migration, or expose a public API. A production
worker must resolve ambiguous B timeouts by source ID before re-sending.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    and_,
    case,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement

from app.modules.telemetry.f03_body_candidate import (
    f03_digest_candidate,
    normalize_f03_body_candidate,
)
from app.modules.telemetry.history_candidate import _utc_bound
from app.modules.telemetry.storage_candidate import metadata, observations

outbox = Table(
    "telemetry_candidate_outbox_candidate",
    metadata,
    Column("source_event_id", Uuid(as_uuid=True), primary_key=True),
    Column("related_source_event_id", Uuid(as_uuid=True)),
    Column(
        "observation_id",
        Uuid(as_uuid=True),
        ForeignKey(observations.c.id),
        nullable=False,
    ),
    Column("body_sha256", String(64), nullable=False),
    Column("body", JSON, nullable=False),
    Column("state", String(24), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("next_attempt_at_utc", DateTime(timezone=True), nullable=False),
    Column("lease_token", Uuid(as_uuid=True)),
    Column("lease_until_utc", DateTime(timezone=True)),
    Column("last_error_code", String(80)),
)

outbox_conflicts = Table(
    "telemetry_candidate_outbox_conflict_candidate",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column(
        "source_event_id",
        Uuid(as_uuid=True),
        ForeignKey(outbox.c.source_event_id),
        nullable=False,
    ),
    Column("observation_id", Uuid(as_uuid=True), nullable=False),
    Column("body_sha256", String(64), nullable=False),
    Column("body", JSON, nullable=False),
    Column("first_detected_at_utc", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "source_event_id",
        "observation_id",
        "body_sha256",
        name="uq_c_candidate_outbox_conflict",
    ),
)

_original_outbox = outbox.alias("original_candidate")


def _dependency_ready() -> ColumnElement[bool]:
    """A recovery is due only after its own original is delivered to B."""
    original_delivered = (
        select(_original_outbox.c.source_event_id)
        .where(
            _original_outbox.c.source_event_id == outbox.c.related_source_event_id,
            _original_outbox.c.state == "delivered",
        )
        .correlate(outbox)
        .exists()
    )
    return or_(outbox.c.related_source_event_id.is_(None), original_delivered)


StageStatus = Literal["staged", "duplicate", "conflict"]
FinishDisposition = Literal["delivered", "retry", "rejected"]


@dataclass(frozen=True)
class DeliveryClaimCandidate:
    source_event_id: UUID
    lease_token: UUID
    body: dict[str, Any]
    body_sha256: str
    attempt_count: int


def _aware_now(value: datetime) -> datetime:
    result = _utc_bound(value, "now")
    if result is None:
        raise ValueError("now must be an aware datetime")
    return result


def _frozen_body(
    body: dict[str, Any],
    source_event_id: UUID,
    observation_id: UUID,
    *,
    f03: bool = False,
) -> tuple[dict[str, Any], str]:
    if not isinstance(body, dict):
        raise ValueError("candidate body must be a JSON object")
    if body.get("source_event_id") != str(source_event_id):
        raise ValueError("candidate source_event_id mismatch")
    linked = body.get("observation_ids")
    if not isinstance(linked, list) or str(observation_id) not in linked:
        raise ValueError("candidate must reference its observation")
    try:
        canonical = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate body must contain finite JSON values") from exc
    if len(canonical.encode("utf-8")) > 65536:
        raise ValueError("candidate body exceeds isolated 64 KiB limit")
    frozen = json.loads(canonical)
    digest = (
        f03_digest_candidate(frozen).removeprefix("sha256:")
        if f03
        else hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )
    return frozen, digest


def _unique_violation(error: IntegrityError) -> bool:
    """Only a source/attempt uniqueness race may be resolved as idempotency."""
    original = error.orig
    state = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if state is not None:
        return state == "23505"
    sqlite_code = getattr(original, "sqlite_errorcode", None)
    return sqlite_code in (
        sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
        sqlite3.SQLITE_CONSTRAINT_UNIQUE,
    )


def stage_candidate_on_connection_candidate(
    connection: Connection,
    *,
    source_event_id: UUID,
    observation_id: UUID,
    body: dict[str, Any],
    now: datetime,
    _f03: bool = False,
) -> StageStatus:
    """Stage inside the caller's transaction; never commit or close it.

    Savepoints isolate a duplicate-key attempt without aborting a PostgreSQL
    outer transaction. A caller must roll back its transaction on any other
    database error, and must not send to B before the outer commit.
    """
    if not connection.in_transaction():
        raise ValueError("caller must start the transaction before staging")
    if not isinstance(source_event_id, UUID) or not isinstance(observation_id, UUID):
        raise ValueError("source_event_id and observation_id must be UUIDs")
    current = _aware_now(now)
    frozen, digest = _frozen_body(body, source_event_id, observation_id, f03=_f03)
    related = frozen.get("related_source_event_id")
    if frozen.get("kind") == "temperature_recovered":
        if not isinstance(related, str):
            raise ValueError("recovery must reference its original source_event_id")
        try:
            related_id = UUID(related)
        except ValueError as exc:
            raise ValueError("related_source_event_id must be a UUID") from exc
        if related_id == source_event_id:
            raise ValueError("recovery cannot reference itself")
    elif related is not None:
        raise ValueError("only recovery may reference an original source_event_id")
    else:
        related_id = None
    try:
        with connection.begin_nested():
            connection.execute(
                outbox.insert().values(
                    source_event_id=source_event_id,
                    related_source_event_id=related_id,
                    observation_id=observation_id,
                    body_sha256=digest,
                    body=frozen,
                    state="pending",
                    attempt_count=0,
                    next_attempt_at_utc=current,
                )
            )
        return "staged"
    except IntegrityError as error:
        # The failed savepoint is rolled back; the outer transaction and its
        # device lock remain owned by the caller. Other faults propagate.
        if not _unique_violation(error):
            raise
        previous = (
            connection.execute(
                select(outbox.c.observation_id, outbox.c.body_sha256).where(
                    outbox.c.source_event_id == source_event_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if previous is None:
            raise
        if (
            previous["observation_id"] == observation_id
            and previous["body_sha256"] == digest
        ):
            return "duplicate"
        try:
            with connection.begin_nested():
                connection.execute(
                    outbox_conflicts.insert().values(
                        id=uuid4(),
                        source_event_id=source_event_id,
                        observation_id=observation_id,
                        body_sha256=digest,
                        body=frozen,
                        first_detected_at_utc=current,
                    )
                )
        except IntegrityError as error:
            # Repeating the same rejected attempt is also idempotent.
            if not _unique_violation(error):
                raise
            recorded = connection.execute(
                select(outbox_conflicts.c.id).where(
                    outbox_conflicts.c.source_event_id == source_event_id,
                    outbox_conflicts.c.observation_id == observation_id,
                    outbox_conflicts.c.body_sha256 == digest,
                )
            ).scalar_one_or_none()
            if recorded is None:
                raise
        return "conflict"


def stage_candidate(
    engine: Engine,
    *,
    source_event_id: UUID,
    observation_id: UUID,
    body: dict[str, Any],
    now: datetime,
    _f03: bool = False,
) -> StageStatus:
    """Freeze one event ID and its exact canonical body before any B call."""
    with engine.begin() as connection:
        return stage_candidate_on_connection_candidate(
            connection,
            source_event_id=source_event_id,
            observation_id=observation_id,
            body=body,
            now=now,
            _f03=_f03,
        )


def stage_f03_on_connection_candidate(
    connection: Connection,
    *,
    source_event_id: UUID,
    observation_id: UUID,
    body: dict[str, Any],
    now: datetime,
) -> StageStatus:
    """Stage the complete B-compatible body before an eventual HTTP send.

    The old generic staging primitive remains for isolated outbox mechanics;
    integration callers must use this wrapper so B's schema 1.0 canonical
    field set cannot change the stored digest later.
    """
    if not connection.in_transaction():
        raise ValueError("caller must start the transaction before staging")
    frozen = normalize_f03_body_candidate(body)
    related = frozen["related_source_event_id"]
    if related is not None:
        original = connection.execute(
            select(outbox.c.body).where(outbox.c.source_event_id == UUID(related))
        ).scalar_one_or_none()
        if original is None or original.get("kind") not in (
            "temperature_high",
            "temperature_low",
        ):
            raise ValueError("recovery requires a local original temperature anomaly")
        for key in (
            "warehouse_id",
            "batch_id",
            "metric_code",
            "rule_snapshot_id",
            "rule_version",
            "severity",
        ):
            if frozen[key] != original.get(key):
                raise ValueError(f"recovery {key} must match the original anomaly")
    return stage_candidate_on_connection_candidate(
        connection,
        source_event_id=source_event_id,
        observation_id=observation_id,
        body=frozen,
        now=now,
        _f03=True,
    )


def stage_f03_candidate(
    engine: Engine,
    *,
    source_event_id: UUID,
    observation_id: UUID,
    body: dict[str, Any],
    now: datetime,
) -> StageStatus:
    """Standalone transaction wrapper for the isolated staging candidate."""
    with engine.begin() as connection:
        return stage_f03_on_connection_candidate(
            connection,
            source_event_id=source_event_id,
            observation_id=observation_id,
            body=body,
            now=now,
        )


def claim_candidate(
    engine: Engine,
    *,
    source_event_id: UUID,
    now: datetime,
    lease_seconds: int = 30,
) -> DeliveryClaimCandidate | None:
    """Claim due work with a compare-and-set update; expired claims can retry."""
    if not isinstance(source_event_id, UUID):
        raise ValueError("source_event_id must be a UUID")
    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int)
        or not 1 <= lease_seconds <= 300
    ):
        raise ValueError("lease_seconds must be 1-300")
    current = _aware_now(now)
    token = uuid4()
    with engine.begin() as connection:
        changed = connection.execute(
            update(outbox)
            .where(
                outbox.c.source_event_id == source_event_id,
                _dependency_ready(),
                or_(
                    and_(
                        outbox.c.state == "pending",
                        outbox.c.next_attempt_at_utc <= current,
                    ),
                    and_(
                        outbox.c.state == "leased",
                        outbox.c.lease_until_utc <= current,
                    ),
                ),
            )
            .values(
                state="leased",
                lease_token=token,
                lease_until_utc=current + timedelta(seconds=lease_seconds),
                attempt_count=outbox.c.attempt_count + 1,
            )
        )
        if changed.rowcount != 1:
            return None
        row = (
            connection.execute(
                select(outbox).where(outbox.c.source_event_id == source_event_id)
            )
            .mappings()
            .one()
        )
    return DeliveryClaimCandidate(
        source_event_id=source_event_id,
        lease_token=token,
        body=dict(row["body"]),
        body_sha256=row["body_sha256"],
        attempt_count=row["attempt_count"],
    )


def due_source_ids_candidate(
    engine: Engine, *, now: datetime, limit: int = 100
) -> list[UUID]:
    """Discover bounded, due work; each worker still needs a CAS claim."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be 1-1000")
    current = _aware_now(now)
    due_at = case(
        (outbox.c.state == "leased", outbox.c.lease_until_utc),
        else_=outbox.c.next_attempt_at_utc,
    )
    with engine.connect() as connection:
        return list(
            connection.scalars(
                select(outbox.c.source_event_id)
                .where(
                    _dependency_ready(),
                    or_(
                        and_(
                            outbox.c.state == "pending",
                            outbox.c.next_attempt_at_utc <= current,
                        ),
                        and_(
                            outbox.c.state == "leased",
                            outbox.c.lease_until_utc <= current,
                        ),
                    ),
                )
                .order_by(due_at, outbox.c.source_event_id)
                .limit(limit)
            )
        )


def finish_candidate(
    engine: Engine,
    *,
    claim: DeliveryClaimCandidate,
    disposition: FinishDisposition,
    now: datetime,
    error_code: str = "",
    retry_after_seconds: int = 0,
) -> bool:
    """Only the current lease holder may finish; retry keeps body and ID."""
    if not isinstance(claim, DeliveryClaimCandidate):
        raise ValueError("claim must be DeliveryClaimCandidate")
    if disposition not in ("delivered", "retry", "rejected"):
        raise ValueError("invalid disposition")
    if not isinstance(error_code, str) or len(error_code) > 80:
        raise ValueError("error_code must be at most 80 characters")
    if disposition != "delivered" and not error_code:
        raise ValueError("failed delivery requires error_code")
    if (
        isinstance(retry_after_seconds, bool)
        or not isinstance(retry_after_seconds, int)
        or not 0 <= retry_after_seconds <= 3600
    ):
        raise ValueError("retry_after_seconds must be 0-3600")
    current = _aware_now(now)
    next_state = {"delivered": "delivered", "retry": "pending", "rejected": "rejected"}[
        disposition
    ]
    next_due = current + timedelta(seconds=retry_after_seconds)
    with engine.begin() as connection:
        result = connection.execute(
            update(outbox)
            .where(
                outbox.c.source_event_id == claim.source_event_id,
                outbox.c.state == "leased",
                outbox.c.lease_token == claim.lease_token,
            )
            .values(
                state=next_state,
                next_attempt_at_utc=next_due,
                lease_token=None,
                lease_until_utc=None,
                last_error_code=error_code or None,
            )
        )
    return result.rowcount == 1
