"""Durable A admission, leased delivery and bounded metadata history.

Admission commits the exact trusted envelope before transport acknowledgement.
Delivery calls C outside A transactions; retries preserve the same reception ID.
Only a validated terminal C receipt atomically replaces A's raw queue copy with
an A metadata receipt. C retains the original raw/reception evidence. This is
not a public route and does not create its own schema or authorize any sender.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col

from app.models import User
from app.modules.iot import stream_frontier
from app.modules.iot.ingress import (
    DeviceContext,
    IngressRejected,
    ReceptionEnvelope,
    receive_telemetry,
)
from app.modules.iot.models import Device
from app.modules.iot.telemetry_inbox_models import audit, inbox, queue_guard, receipts

MAX_ATTEMPTS = 10
LEASE_SECONDS = 30
HISTORY_DAYS = 7
_TERMINAL = {"accepted_new", "accepted_duplicate", "invalid", "conflict"}
_REJECTION_REASONS = {
    "device_unknown",
    "device_disabled",
    "device_unbound",
    "identity_mismatch",
    "topic_mismatch",
    "topic_outside_verified_scope",
    "invalid_encoding",
    "invalid_json",
    "invalid_payload",
    "invalid_schema",
    "payload_too_large",
    "retained_telemetry",
    "unexpected_qos",
}


@dataclass(frozen=True)
class QueueLimits:
    max_items: int = 1000
    max_bytes: int = 32 * 1024 * 1024
    history_records: int = 1000

    def __post_init__(self) -> None:
        for value, upper in (
            (self.max_items, 1000),
            (self.max_bytes, 32 * 1024 * 1024),
            (self.history_records, 1000),
        ):
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError("queue limits must be positive bounded integers")


@dataclass(frozen=True)
class InboxResult:
    status: str
    reception_id: UUID
    reason: str = ""
    observation_id: UUID | None = None


@dataclass(frozen=True)
class DeliveryClaim:
    envelope: ReceptionEnvelope
    lease_id: UUID
    attempt: int
    fingerprint: str


class ReceiptLike(Protocol):
    @property
    def status(self) -> str: ...

    @property
    def reception_id(self) -> UUID: ...

    @property
    def observation_id(self) -> UUID | None: ...


StoreSink = Callable[[ReceptionEnvelope], ReceiptLike]


def _time(value: datetime | None) -> datetime:
    value = datetime.now(UTC) if value is None else value
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("server timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _fingerprint(envelope: ReceptionEnvelope) -> str:
    view = [
        str(envelope.reception_id),
        str(envelope.authenticated_device_id),
        str(envelope.warehouse_id),
        _time(envelope.received_at).isoformat(),
        envelope.raw_text,
    ]
    return hashlib.sha256(
        json.dumps(view, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate(envelope: ReceptionEnvelope) -> tuple[bytes, str]:
    if (
        not isinstance(envelope, ReceptionEnvelope)
        or not all(
            isinstance(value, UUID)
            for value in (
                envelope.reception_id,
                envelope.authenticated_device_id,
                envelope.warehouse_id,
            )
        )
        or not isinstance(envelope.raw_text, str)
    ):
        raise ValueError("trusted ReceptionEnvelope with UUID identities required")
    payload = envelope.raw_text.encode("utf-8")
    receive_telemetry(
        topic=f"freshlink/v1/devices/{envelope.authenticated_device_id}/telemetry",
        payload=payload,
        context=DeviceContext(envelope.authenticated_device_id, envelope.warehouse_id),
        received_at=envelope.received_at,
    )
    return payload, _fingerprint(envelope)


def _bounded(connection: Connection) -> None:
    connection.exec_driver_sql("SET LOCAL statement_timeout = '5000ms'")


def _guard(connection: Connection) -> None:
    # Migration/test setup must seed the singleton. Never self-create a table.
    _bounded(connection)
    connection.execute(
        select(queue_guard.c.id).where(queue_guard.c.id == 1).with_for_update()
    ).scalar_one()


def _prune(connection: Connection, table: Any, at: datetime, limit: int) -> None:
    key = table.c.reception_id if table is receipts else table.c.id
    stamp = table.c.completed_at if table is receipts else table.c.recorded_at
    stale = list(
        connection.scalars(
            select(key)
            .where(stamp < at - timedelta(days=HISTORY_DAYS))
            .order_by(stamp, key)
            .limit(1000)
        )
    )
    if stale:
        connection.execute(delete(table).where(key.in_(stale)))
    total = connection.scalar(select(func.count()).select_from(table)) or 0
    if total > limit:
        oldest = list(
            connection.scalars(
                select(key).order_by(stamp, key).limit(min(total - limit, 1000))
            )
        )
        connection.execute(delete(table).where(key.in_(oldest)))


def _audit(
    connection: Connection,
    *,
    at: datetime,
    reason: str,
    device_id: UUID | None,
    reception_id: UUID | None,
    size: int,
    digest: str,
    limits: QueueLimits,
    actor_id: UUID | None = None,
) -> None:
    connection.execute(
        audit.insert().values(
            id=uuid4(),
            recorded_at=at,
            device_id=device_id,
            reception_id=reception_id,
            reason=reason,
            payload_bytes=size,
            payload_sha256=digest,
            actor_id=actor_id,
        )
    )
    _prune(connection, audit, at, limits.history_records)


def _enqueue_locked(
    connection: Connection,
    envelope: ReceptionEnvelope,
    *,
    at: datetime,
    limits: QueueLimits,
    payload: bytes,
    fingerprint: str,
) -> InboxResult:
    identity = envelope.reception_id
    digest = hashlib.sha256(payload).hexdigest()
    for table in (inbox, receipts):
        row = (
            connection.execute(select(table).where(table.c.reception_id == identity))
            .mappings()
            .first()
        )
        if row is not None:
            if row["fingerprint"] == fingerprint:
                return InboxResult(
                    "already_queued" if table is inbox else "already_processed",
                    identity,
                )
            _audit(
                connection,
                at=at,
                reason="reception_conflict",
                device_id=envelope.authenticated_device_id,
                reception_id=identity,
                size=len(payload),
                digest=digest,
                limits=limits,
            )
            return InboxResult("conflict", identity, "reception_id_reuse")
    device = (
        connection.execute(
            select(col(Device.id), col(Device.warehouse_id)).where(
                col(Device.id) == envelope.authenticated_device_id
            )
        )
        .mappings()
        .first()
    )
    if device is None or device["warehouse_id"] != envelope.warehouse_id:
        reason = "device_unknown" if device is None else "device_unbound"
        _audit(
            connection,
            at=at,
            reason=reason,
            device_id=envelope.authenticated_device_id,
            reception_id=identity,
            size=len(payload),
            digest=digest,
            limits=limits,
        )
        return InboxResult("invalid", identity, reason)
    used, size = connection.execute(
        select(
            func.count(), func.coalesce(func.sum(inbox.c.payload_bytes), 0)
        ).select_from(inbox)
    ).one()
    if used >= limits.max_items or size + len(payload) > limits.max_bytes:
        _audit(
            connection,
            at=at,
            reason="queue_full",
            device_id=envelope.authenticated_device_id,
            reception_id=identity,
            size=len(payload),
            digest=digest,
            limits=limits,
        )
        return InboxResult("queue_full", identity, "queue_full")
    connection.execute(
        inbox.insert().values(
            reception_id=identity,
            device_id=envelope.authenticated_device_id,
            warehouse_id=envelope.warehouse_id,
            received_at=_time(envelope.received_at),
            raw_text=envelope.raw_text,
            payload_bytes=len(payload),
            payload_sha256=digest,
            fingerprint=fingerprint,
            state="queued",
            attempt_count=0,
            next_attempt_at=at,
            last_reason="",
            manual_retry_count=0,
        )
    )
    return InboxResult("queued", identity)


def enqueue(
    engine: Engine,
    envelope: ReceptionEnvelope,
    *,
    now: datetime | None = None,
    limits: QueueLimits = QueueLimits(),
    frontier_epoch: UUID | None = None,
) -> InboxResult:
    """Admit a verified frame, bounded across concurrent PostgreSQL writers.

    The gateway must already perform the real B warehouse lookup. A registration
    and binding are also re-read in this admission transaction. No ACK is safe
    on queue_full/storage_unavailable; a broker persistent session must retain it.
    """
    at = _time(now)
    payload, fingerprint = _validate(envelope)
    identity = envelope.reception_id
    try:
        with engine.begin() as connection:
            _guard(connection)
            if frontier_epoch is not None:
                replay = stream_frontier.admission_preflight(
                    connection,
                    device_id=envelope.authenticated_device_id,
                    warehouse_id=envelope.warehouse_id,
                    epoch=frontier_epoch,
                    reception_id=identity,
                    fingerprint=fingerprint,
                )
                if replay is not None:
                    return InboxResult(replay, identity, "frontier_replay_or_hold")
            result = _enqueue_locked(
                connection,
                envelope,
                at=at,
                limits=limits,
                payload=payload,
                fingerprint=fingerprint,
            )
            if frontier_epoch is not None:
                tracked = stream_frontier.record_admission(
                    connection,
                    device_id=envelope.authenticated_device_id,
                    warehouse_id=envelope.warehouse_id,
                    epoch=frontier_epoch,
                    reception_id=identity,
                    fingerprint=fingerprint,
                    inbox_status=result.status,
                )
                if not tracked:
                    return InboxResult(
                        "retryable_failure", identity, "untracked_legacy"
                    )
            return result
    except SQLAlchemyError, stream_frontier.FrontierUnavailable:
        return InboxResult("retryable_failure", identity, "storage_unavailable")


def record_rejection(
    engine: Engine,
    rejection: IngressRejected,
    *,
    device_id: UUID | None,
    now: datetime | None = None,
    limits: QueueLimits = QueueLimits(),
    frontier_epoch: UUID | None = None,
) -> bool:
    """Bounded metadata only, never the rejected raw or credential text."""
    at = _time(now)
    reason = (
        rejection.reason_code
        if rejection.reason_code in _REJECTION_REASONS
        else "invalid_payload"
    )
    try:
        with engine.begin() as connection:
            _guard(connection)
            if frontier_epoch is not None and device_id is not None:
                stream_frontier.mark_uncertain(
                    connection, device_id=device_id, epoch=frontier_epoch
                )
            _audit(
                connection,
                at=at,
                reason=reason,
                device_id=device_id,
                reception_id=None,
                size=rejection.payload_bytes,
                digest=rejection.payload_sha256,
                limits=limits,
            )
        return True
    except SQLAlchemyError, stream_frontier.FrontierUnavailable:
        return False


def _envelope(row: RowMapping) -> ReceptionEnvelope:
    return ReceptionEnvelope(
        raw_text=row["raw_text"],
        authenticated_device_id=row["device_id"],
        warehouse_id=row["warehouse_id"],
        received_at=_time(row["received_at"]),
        reception_id=row["reception_id"],
    )


def claim_next(engine: Engine, *, now: datetime | None = None) -> DeliveryClaim | None:
    """Lease only the earliest unsettled row per device, without holding C locks.

    Backoff, live leases, blocked and exhausted predecessors all hold later
    rows for that device; another device remains eligible. This is not a C
    commit frontier: a lease cannot cancel an already running sink call, and
    later admission of older timestamps still needs explicit reconciliation.
    """
    at = _time(now)
    predecessor = inbox.alias("unsettled_predecessor")
    has_predecessor = (
        select(1)
        .select_from(predecessor)
        .where(
            predecessor.c.device_id == inbox.c.device_id,
            or_(
                predecessor.c.received_at < inbox.c.received_at,
                (predecessor.c.received_at == inbox.c.received_at)
                & (predecessor.c.reception_id < inbox.c.reception_id),
            ),
        )
        .correlate(inbox)
        .exists()
    )
    with engine.begin() as connection:
        _guard(connection)
        row = (
            connection.execute(
                select(inbox)
                .where(
                    ~has_predecessor,
                    or_(
                        (inbox.c.state == "queued") & (inbox.c.next_attempt_at <= at),
                        (inbox.c.state == "leased") & (inbox.c.lease_until <= at),
                    ),
                )
                .order_by(inbox.c.received_at, inbox.c.reception_id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        if row["attempt_count"] >= MAX_ATTEMPTS:
            connection.execute(
                update(inbox)
                .where(inbox.c.reception_id == row["reception_id"])
                .values(
                    state="exhausted",
                    lease_id=None,
                    lease_until=None,
                    last_reason="retry_budget_exhausted",
                )
            )
            return None
        envelope = _envelope(row)
        if _fingerprint(envelope) != row["fingerprint"]:
            connection.execute(
                update(inbox)
                .where(inbox.c.reception_id == row["reception_id"])
                .values(
                    state="blocked",
                    lease_id=None,
                    lease_until=None,
                    last_reason="source_integrity_error",
                )
            )
            return None
        lease = uuid4()
        attempt = row["attempt_count"] + 1
        connection.execute(
            update(inbox)
            .where(inbox.c.reception_id == envelope.reception_id)
            .values(
                state="leased",
                attempt_count=attempt,
                lease_id=lease,
                lease_until=at + timedelta(seconds=LEASE_SECONDS),
            )
        )
        return DeliveryClaim(envelope, lease, attempt, row["fingerprint"])


def _checked_receipt(receipt: ReceiptLike, identity: UUID) -> tuple[str, UUID | None]:
    status = receipt.status
    if (
        status not in _TERMINAL | {"retryable_failure"}
        or receipt.reception_id != identity
    ):
        raise ValueError("invalid sink receipt")
    observation_id = receipt.observation_id
    if observation_id is not None and not isinstance(observation_id, UUID):
        raise ValueError("invalid sink observation identity")
    if status.startswith("accepted_") and observation_id is None:
        raise ValueError("accepted receipt has no durable observation identity")
    return status, observation_id


def _check_claim(
    connection: Connection, claim: DeliveryClaim, at: datetime
) -> InboxResult | None:
    identity = claim.envelope.reception_id
    row = (
        connection.execute(
            select(inbox).where(inbox.c.reception_id == identity).with_for_update()
        )
        .mappings()
        .first()
    )
    if (
        row is None
        or row["state"] != "leased"
        or row["lease_id"] != claim.lease_id
        or row["attempt_count"] != claim.attempt
        or _time(row["lease_until"]) <= at
    ):
        return InboxResult("lease_lost", identity)
    if (
        row["fingerprint"] != claim.fingerprint
        or _fingerprint(_envelope(row)) != claim.fingerprint
        or _fingerprint(claim.envelope) != claim.fingerprint
    ):
        connection.execute(
            update(inbox)
            .where(inbox.c.reception_id == identity)
            .values(
                state="blocked",
                lease_id=None,
                lease_until=None,
                last_reason="source_integrity_error",
            )
        )
        return InboxResult("blocked", identity, "source_integrity_error")
    return None


def deliver_claim(
    engine: Engine,
    claim: DeliveryClaim,
    sink: StoreSink,
    *,
    now: datetime | None = None,
    limits: QueueLimits = QueueLimits(),
    frontier_epoch: UUID | None = None,
) -> InboxResult:
    """Call C without holding A locks, then match the exact live lease.

    Invalid/conflict C receipts are not business success: their C raw receipt is
    retained and A stores a terminal failure metadata result. Failed/corrupt sink
    receipts leave A raw blocked for an administrator, never silently delivered.
    """
    identity = claim.envelope.reception_id
    # Validate the committed lease and original body before calling the sink.
    # Release all A locks before C opens its own transaction.
    try:
        with engine.begin() as connection:
            _guard(connection)
            refused = _check_claim(connection, claim, _time(now))
            if refused is not None:
                return refused
    except SQLAlchemyError:
        return InboxResult("retryable_failure", identity, "storage_unavailable")
    reason = ""
    observation_id = None
    try:
        receipt = sink(claim.envelope)
    except Exception:
        status, reason = "retryable_failure", "sink_error"
    else:
        try:
            status, observation_id = _checked_receipt(receipt, identity)
        except Exception:
            status, reason = "blocked", "invalid_sink_receipt"
    at = _time(now)  # Re-check the real clock after a potentially slow C call.
    try:
        with engine.begin() as connection:
            _guard(connection)
            refused = _check_claim(connection, claim, at)
            if refused is not None:
                return refused
            if status == "blocked":
                if frontier_epoch is not None:
                    stream_frontier.mark_uncertain(
                        connection,
                        device_id=claim.envelope.authenticated_device_id,
                        epoch=frontier_epoch,
                    )
                connection.execute(
                    update(inbox)
                    .where(inbox.c.reception_id == identity)
                    .values(
                        state="blocked",
                        lease_id=None,
                        lease_until=None,
                        last_reason=reason,
                    )
                )
                return InboxResult("blocked", identity, reason)
            if status == "retryable_failure":
                exhausted = claim.attempt >= MAX_ATTEMPTS
                reason = (
                    "retry_budget_exhausted"
                    if exhausted
                    else (reason or "storage_unavailable")
                )
                if exhausted and frontier_epoch is not None:
                    stream_frontier.mark_uncertain(
                        connection,
                        device_id=claim.envelope.authenticated_device_id,
                        epoch=frontier_epoch,
                    )
                delay = min(30, 2 ** (claim.attempt - 1))
                connection.execute(
                    update(inbox)
                    .where(inbox.c.reception_id == identity)
                    .values(
                        state="exhausted" if exhausted else "queued",
                        lease_id=None,
                        lease_until=None,
                        next_attempt_at=at + timedelta(seconds=delay),
                        last_reason=reason,
                    )
                )
                return InboxResult(
                    "exhausted" if exhausted else "retry_scheduled", identity, reason
                )
            # C's transaction has completed. The A copy is removed only together
            # with a terminal metadata receipt, so a failed A commit is replayable.
            if frontier_epoch is not None:
                stream_frontier.record_terminal(
                    connection,
                    reception_id=identity,
                    fingerprint=claim.fingerprint,
                    epoch=frontier_epoch,
                    outcome=status,
                    observation_id=observation_id,
                )
            connection.execute(
                receipts.insert().values(
                    reception_id=identity,
                    fingerprint=claim.fingerprint,
                    outcome=status,
                    observation_id=observation_id,
                    reason=""
                    if status.startswith("accepted_")
                    else (
                        "content_conflict"
                        if status == "conflict"
                        else "invalid_payload"
                    ),
                    completed_at=at,
                )
            )
            connection.execute(delete(inbox).where(inbox.c.reception_id == identity))
            _prune(connection, receipts, at, limits.history_records)
            return InboxResult(
                "delivered" if status.startswith("accepted_") else status,
                identity,
                observation_id=observation_id,
            )
    except SQLAlchemyError, stream_frontier.FrontierUnavailable:
        return InboxResult("retryable_failure", identity, "storage_unavailable")


def retry_pending(
    engine: Engine,
    reception_id: UUID,
    *,
    actor_id: UUID,
    now: datetime | None = None,
    limits: QueueLimits = QueueLimits(),
) -> InboxResult:
    """Explicit active administrator resets an exhausted/blocked attempt cycle."""
    at = _time(now)
    with engine.begin() as connection:
        _bounded(connection)
        actor = (
            connection.execute(
                select(col(User.id), col(User.is_active), col(User.is_superuser)).where(
                    col(User.id) == actor_id
                )
            )
            .mappings()
            .first()
        )
        if actor is None or not actor["is_active"] or not actor["is_superuser"]:
            raise PermissionError("inbox_retry_forbidden")
        _guard(connection)
        row = (
            connection.execute(
                select(inbox)
                .where(inbox.c.reception_id == reception_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if row is None:
            return InboxResult("not_found", reception_id)
        if row["state"] == "queued":
            return InboxResult("already_queued", reception_id)
        if row["state"] not in {"blocked", "exhausted"}:
            return InboxResult("conflict", reception_id, "delivery_in_flight")
        connection.execute(
            update(inbox)
            .where(inbox.c.reception_id == reception_id)
            .values(
                state="queued",
                attempt_count=0,
                lease_id=None,
                lease_until=None,
                next_attempt_at=at,
                last_reason="manual_retry",
                manual_retry_count=row["manual_retry_count"] + 1,
                last_retry_actor=actor_id,
            )
        )
        _audit(
            connection,
            at=at,
            reason="manual_retry",
            device_id=row["device_id"],
            reception_id=reception_id,
            size=row["payload_bytes"],
            digest=row["payload_sha256"],
            actor_id=actor_id,
            limits=limits,
        )
        return InboxResult("retry_requested", reception_id)


def prune_history(
    engine: Engine, *, now: datetime | None = None, limits: QueueLimits = QueueLimits()
) -> None:
    """Prune A transport metadata only; pending raw and C business rows survive."""
    at = _time(now)
    with engine.begin() as connection:
        _guard(connection)
        for table in (receipts, audit):
            _prune(connection, table, at, limits.history_records)
