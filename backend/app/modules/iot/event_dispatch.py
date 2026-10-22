"""Durable continuous delivery budget for A offline/recovered events to B.

Never sends email/push. Preserve immutable A source/body, wait for the original
offline checkpoint before recovery, and require B's exact durable receipt.
All A bookkeeping uses one short transaction-scoped advisory lock; no A lock
is held during the independently committed B call. No automatic schema setup.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import SQLModel, col

from app.models import User
from app.modules.alerts.models.event import CandidateReceipt, compute_content_digest
from app.modules.iot.event_delivery import EventReceiver, EventSnapshot
from app.modules.iot.event_dispatch_models import checkpoints, retry_audit

MAX_ATTEMPTS = 10
LEASE_SECONDS = 30
MAX_PENDING = 1000
HISTORY_LIMIT = 1000
LOCK_NAMESPACE = 541023881901  # PG bigint namespace, shared by A event dispatch only.
source = SQLModel.metadata.tables["iot_connectivity_event"]


@dataclass(frozen=True)
class EventClaim:
    snapshot: EventSnapshot
    fingerprint: str
    lease_id: UUID
    attempt: int


@dataclass(frozen=True)
class DispatchResult:
    source_event_id: UUID
    status: str
    reason: str = ""
    target_event_id: UUID | None = None


def _time(value: datetime | None) -> datetime:
    value = datetime.now(UTC) if value is None else value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware server clock required")
    return value.astimezone(UTC)


def _scope(identities: tuple[UUID, ...]) -> None:
    if (
        not 1 <= len(identities) <= 14
        or len(set(identities)) != len(identities)
        or any(not isinstance(i, UUID) for i in identities)
    ):
        raise ValueError("explicit scope of 1-14 unique device UUIDs required")


def _guard(connection: Connection) -> None:
    connection.exec_driver_sql("SET LOCAL statement_timeout = '5000ms'")
    connection.exec_driver_sql(f"SELECT pg_advisory_xact_lock({LOCK_NAMESPACE})")


def _snapshot(row: RowMapping) -> EventSnapshot:
    return EventSnapshot(
        row["source_event_id"],
        row["device_id"],
        row["warehouse_id"],
        _time(row["occurred_at"]),
        row["kind"],
        row["reason"],
        row["related_source_event_id"],
    )


def _digest(snapshot: EventSnapshot) -> str:
    parts = [
        str(snapshot.source_event_id),
        str(snapshot.device_id),
        str(snapshot.warehouse_id),
        _time(snapshot.occurred_at).isoformat(),
        snapshot.kind,
        snapshot.reason,
        str(snapshot.related_source_event_id)
        if snapshot.related_source_event_id
        else None,
    ]
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _trim(connection: Connection, at: datetime) -> None:
    # Only A terminal metadata/retry audit, never the original event or B row.
    for table, key, stamp, predicate in (
        (
            checkpoints,
            checkpoints.c.source_event_id,
            checkpoints.c.completed_at,
            checkpoints.c.state == "delivered",
        ),
        (
            retry_audit,
            retry_audit.c.id,
            retry_audit.c.recorded_at,
            retry_audit.c.cycle >= 1,
        ),
    ):
        connection.execute(
            delete(table).where(predicate, stamp < at - timedelta(days=7))
        )
        count = (
            connection.scalar(select(func.count()).select_from(table).where(predicate))
            or 0
        )
        if count > HISTORY_LIMIT:
            old = list(
                connection.scalars(
                    select(key)
                    .where(predicate)
                    .order_by(stamp, key)
                    .limit(count - HISTORY_LIMIT)
                )
            )
            connection.execute(delete(table).where(key.in_(old)))


def _failed(connection: Connection, identity: UUID, state: str, reason: str) -> None:
    connection.execute(
        update(checkpoints)
        .where(checkpoints.c.source_event_id == identity)
        .values(
            state=state,
            last_reason=reason,
            lease_id=None,
            lease_until=None,
        )
    )
    connection.execute(
        update(source)
        .where(
            source.c.source_event_id == identity, source.c.delivery_state == "pending"
        )
        .values(delivery_state="failed")
    )


def claim_event(
    engine: Engine, device_ids: tuple[UUID, ...], *, now: datetime | None = None
) -> EventClaim | None:
    _scope(device_ids)
    at = _time(now)
    with engine.begin() as connection:
        _guard(connection)
        _trim(connection, at)
        used = (
            connection.scalar(
                select(func.count())
                .select_from(checkpoints)
                .where(checkpoints.c.state != "delivered")
            )
            or 0
        )
        if used < MAX_PENDING:
            original = source.alias("admission_original")
            ready = or_(
                source.c.kind != "recovered",
                original.c.source_event_id.is_(None),
                original.c.kind != "offline",
                original.c.device_id != source.c.device_id,
                original.c.warehouse_id.is_distinct_from(source.c.warehouse_id),
                original.c.delivery_state == "delivered",
            )
            # Waiting recoveries stay in A history without consuming capacity;
            # otherwise they could fill the budget and starve their originals.
            new = (
                connection.execute(
                    select(source)
                    .outerjoin(
                        checkpoints,
                        source.c.source_event_id == checkpoints.c.source_event_id,
                    )
                    .outerjoin(
                        original,
                        original.c.source_event_id == source.c.related_source_event_id,
                    )
                    .where(
                        source.c.device_id.in_(device_ids),
                        source.c.delivery_state == "pending",
                        source.c.warehouse_id.is_not(None),
                        checkpoints.c.source_event_id.is_(None),
                        ready,
                    )
                    .order_by(source.c.occurred_at, source.c.source_event_id)
                    .limit(min(50, MAX_PENDING - used))
                )
                .mappings()
                .all()
            )
            for candidate_row in new:
                snapshot = _snapshot(candidate_row)
                connection.execute(
                    checkpoints.insert().values(
                        source_event_id=snapshot.source_event_id,
                        fingerprint=_digest(snapshot),
                        state="queued",
                        attempt_count=0,
                        next_attempt_at=at,
                        last_reason="",
                        manual_retry_count=0,
                    )
                )
        parent = source.alias("original_offline")
        invalid_parent = or_(
            parent.c.source_event_id.is_(None),
            parent.c.kind != "offline",
            parent.c.device_id != source.c.device_id,
            parent.c.warehouse_id.is_distinct_from(source.c.warehouse_id),
        )
        row = (
            connection.execute(
                select(checkpoints)
                .join(source, source.c.source_event_id == checkpoints.c.source_event_id)
                .outerjoin(
                    parent,
                    parent.c.source_event_id == source.c.related_source_event_id,
                )
                .where(
                    source.c.device_id.in_(device_ids),
                    source.c.delivery_state == "pending",
                    or_(
                        and_(
                            checkpoints.c.state == "queued",
                            checkpoints.c.next_attempt_at <= at,
                        ),
                        and_(
                            checkpoints.c.state == "leased",
                            checkpoints.c.lease_until <= at,
                        ),
                    ),
                    or_(
                        source.c.kind != "recovered",
                        invalid_parent,
                        parent.c.delivery_state == "delivered",
                    ),
                )
                .order_by(checkpoints.c.next_attempt_at, checkpoints.c.source_event_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        identity = row["source_event_id"]
        event = (
            connection.execute(
                select(source).where(source.c.source_event_id == identity)
            )
            .mappings()
            .one()
        )
        snapshot = _snapshot(event)
        if event["warehouse_id"] is None or _digest(snapshot) != row["fingerprint"]:
            _failed(connection, identity, "blocked", "source_changed")
            return None
        if row["attempt_count"] >= MAX_ATTEMPTS:
            _failed(connection, identity, "exhausted", "retry_budget_exhausted")
            return None
        if snapshot.kind == "recovered":
            original_event = (
                connection.execute(
                    select(source).where(
                        source.c.source_event_id == snapshot.related_source_event_id
                    )
                )
                .mappings()
                .first()
            )
            if (
                original_event is None
                or original_event["kind"] != "offline"
                or original_event["device_id"] != snapshot.device_id
                or original_event["warehouse_id"] != snapshot.warehouse_id
            ):
                _failed(connection, identity, "blocked", "invalid_recovery_origin")
                return None
        try:
            snapshot.payload()
        except ValueError:
            _failed(connection, identity, "blocked", "source_invalid")
            return None
        lease, attempt = uuid4(), row["attempt_count"] + 1
        connection.execute(
            update(checkpoints)
            .where(checkpoints.c.source_event_id == identity)
            .values(
                state="leased",
                attempt_count=attempt,
                lease_id=lease,
                lease_until=at + timedelta(seconds=LEASE_SECONDS),
            )
        )
        return EventClaim(snapshot, row["fingerprint"], lease, attempt)


def _live(connection: Connection, claim: EventClaim, at: datetime) -> str | None:
    identity = claim.snapshot.source_event_id
    row = (
        connection.execute(
            select(checkpoints).where(checkpoints.c.source_event_id == identity)
        )
        .mappings()
        .first()
    )
    if (
        row is None
        or row["state"] != "leased"
        or row["lease_id"] != claim.lease_id
        or row["attempt_count"] != claim.attempt
        or row["lease_until"] <= at
    ):
        return "lease_lost"
    event = (
        connection.execute(select(source).where(source.c.source_event_id == identity))
        .mappings()
        .first()
    )
    if (
        event is None
        or event["delivery_state"] != "pending"
        or row["fingerprint"] != claim.fingerprint
        or _digest(claim.snapshot) != claim.fingerprint
        or _digest(_snapshot(event)) != claim.fingerprint
    ):
        _failed(connection, identity, "blocked", "source_changed")
        return "source_changed"
    return None


def deliver_event_claim(
    engine: Engine,
    claim: EventClaim,
    receiver: EventReceiver,
    *,
    now: datetime | None = None,
) -> DispatchResult:
    identity = claim.snapshot.source_event_id
    try:
        with engine.begin() as connection:
            _guard(connection)
            refused = _live(connection, claim, _time(now))
            if refused:
                return DispatchResult(
                    identity,
                    "lease_lost" if refused == "lease_lost" else "blocked",
                    refused,
                )
    except SQLAlchemyError:
        return DispatchResult(identity, "retryable_failure", "storage_unavailable")
    payload = claim.snapshot.payload()
    expected = compute_content_digest(payload)
    target = None
    try:
        receipt = receiver(payload)
    except Exception:
        status, reason = "retryable_failure", "receiver_error"
    else:
        if not isinstance(receipt, CandidateReceipt):
            status, reason = "blocked", "receipt_invalid"
        elif receipt.status == "retryable_failure":
            status, reason = "retryable_failure", "target_unavailable"
        elif receipt.status not in {"accepted_new", "accepted_duplicate"}:
            status, reason = "blocked", "target_rejected"
        else:
            target = receipt.event_id or receipt.stored_event_id
            if (
                receipt.source_event_id != str(identity)
                or receipt.content_digest != expected
                or not isinstance(target, UUID)
                or (
                    receipt.event_id is not None
                    and receipt.stored_event_id is not None
                    and receipt.event_id != receipt.stored_event_id
                )
            ):
                status, reason = "blocked", "receipt_mismatch"
            else:
                status, reason = "delivered", ""
    at = _time(now)
    try:
        with engine.begin() as connection:
            _guard(connection)
            refused = _live(connection, claim, at)
            if refused:
                return DispatchResult(
                    identity,
                    "lease_lost" if refused == "lease_lost" else "blocked",
                    refused,
                )
            if status == "blocked":
                _failed(connection, identity, "blocked", reason)
            elif status == "retryable_failure":
                if claim.attempt >= MAX_ATTEMPTS:
                    _failed(connection, identity, "exhausted", "retry_budget_exhausted")
                    return DispatchResult(
                        identity, "exhausted", "retry_budget_exhausted"
                    )
                connection.execute(
                    update(checkpoints)
                    .where(checkpoints.c.source_event_id == identity)
                    .values(
                        state="queued",
                        lease_id=None,
                        lease_until=None,
                        last_reason=reason,
                        next_attempt_at=at
                        + timedelta(seconds=min(30, 2 ** (claim.attempt - 1))),
                    )
                )
                return DispatchResult(identity, "retry_scheduled", reason)
            else:
                # B's committed source/digest/target receipt has been verified.
                # Both A history checkpoint and budget close in this transaction.
                connection.execute(
                    update(source)
                    .where(source.c.source_event_id == identity)
                    .values(delivery_state="delivered")
                )
                connection.execute(
                    update(checkpoints)
                    .where(checkpoints.c.source_event_id == identity)
                    .values(
                        state="delivered",
                        lease_id=None,
                        lease_until=None,
                        last_reason="",
                        target_event_id=target,
                        completed_at=at,
                    )
                )
                _trim(connection, at)
            return DispatchResult(
                identity, status, reason, target if status == "delivered" else None
            )
    except SQLAlchemyError:
        return DispatchResult(identity, "retryable_failure", "storage_unavailable")


def retry_event(
    engine: Engine,
    source_event_id: UUID,
    *,
    actor_id: UUID,
    now: datetime | None = None,
) -> str:
    at = _time(now)
    with engine.begin() as connection:
        _guard(connection)
        actor = (
            connection.execute(
                select(col(User.is_active), col(User.is_superuser)).where(
                    col(User.id) == actor_id
                )
            )
            .mappings()
            .first()
        )
        if actor is None or not actor["is_active"] or not actor["is_superuser"]:
            raise PermissionError("event_retry_forbidden")
        row = (
            connection.execute(
                select(checkpoints).where(
                    checkpoints.c.source_event_id == source_event_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return "not_found"
        if row["state"] == "queued":
            return "already_queued"
        if row["state"] not in {"blocked", "exhausted"}:
            return "conflict"
        event = (
            connection.execute(
                select(source).where(source.c.source_event_id == source_event_id)
            )
            .mappings()
            .first()
        )
        if (
            event is None
            or event["warehouse_id"] is None
            or _digest(_snapshot(event)) != row["fingerprint"]
            or event["delivery_state"] != "failed"
        ):
            return "source_changed"
        cycle = row["manual_retry_count"] + 1
        connection.execute(
            update(checkpoints)
            .where(checkpoints.c.source_event_id == source_event_id)
            .values(
                state="queued",
                attempt_count=0,
                lease_id=None,
                lease_until=None,
                next_attempt_at=at,
                manual_retry_count=cycle,
                last_reason="manual_retry",
            )
        )
        connection.execute(
            update(source)
            .where(source.c.source_event_id == source_event_id)
            .values(delivery_state="pending")
        )
        connection.execute(
            retry_audit.insert().values(
                id=uuid4(),
                source_event_id=source_event_id,
                actor_id=actor_id,
                recorded_at=at,
                cycle=cycle,
            )
        )
        _trim(connection, at)
        return "retry_requested"
