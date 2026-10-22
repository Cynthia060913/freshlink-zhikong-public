"""Caller-transaction A24 candidate, disabled unless explicitly selected.

No schema creation, epoch rotation, gap clearing, C/B call or commit. Counter
and ledger writes must accompany the actual inbox/terminal receipt transaction.
The completeness stamp is NOT permission to send automatic recovery: shared C
write gates, window invalidation and send linearization still need integration.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection, RowMapping
from sqlmodel import col

from app.modules.iot.models import Device
from app.modules.iot.stream_frontier_models import entries, streams

MAX_ENTRIES = 10000
_ACCEPTED = {"accepted_new", "accepted_duplicate"}
_TERMINAL = _ACCEPTED | {"invalid", "conflict"}


class FrontierUnavailable(Exception):
    """Missing/legacy state or mismatched epoch must fail closed."""


@dataclass(frozen=True)
class FrontierStamp:
    device_id: UUID
    warehouse_id: UUID
    stream_epoch: UUID
    admitted_seq: int
    settled_through: int
    evidence_revision: int


def _transaction(connection: Connection) -> None:
    if not connection.in_transaction() or connection.dialect.name != "postgresql":
        raise ValueError("frontier requires caller-owned PostgreSQL transaction")
    connection.exec_driver_sql("SET LOCAL lock_timeout = '5000ms'")
    connection.exec_driver_sql("SET LOCAL statement_timeout = '5000ms'")
    if connection.get_isolation_level() != "READ COMMITTED":
        raise FrontierUnavailable("fresh READ COMMITTED statements required")


def _locked(connection: Connection, identities: set[UUID]) -> dict[UUID, RowMapping]:
    if not identities or not all(isinstance(item, UUID) for item in identities):
        raise ValueError("trusted device UUIDs required")
    _transaction(connection)
    rows = (
        connection.execute(
            select(streams)
            .where(streams.c.device_id.in_(identities))
            .order_by(streams.c.device_id)
            .with_for_update()
        )
        .mappings()
        .all()
    )
    result = {row["device_id"]: row for row in rows}
    if set(result) != identities:
        raise FrontierUnavailable("stream has not been explicitly initialized")
    return result


def _hold(connection: Connection, device_id: UUID, reason: str) -> None:
    connection.execute(
        update(streams)
        .where(streams.c.device_id == device_id)
        .values(
            evidence_revision=streams.c.evidence_revision + 1,
            gap_reason=reason,
        )
    )


def _context(row: RowMapping, epoch: UUID, warehouse_id: UUID) -> None:
    if row["stream_epoch"] != epoch or row["warehouse_id"] != warehouse_id:
        raise FrontierUnavailable("epoch or binding mismatch")


def begin_evidence_write(
    connection: Connection, *, device_id: UUID, warehouse_id: UUID, epoch: UUID
) -> int:
    """Candidate C write gate on the caller's transaction, never a commit.

    Call BEFORE reading evidence or writing any C observation/receipt/conflict,
    risk, window or frozen outbox. Keep this connection through the whole local
    unit. The stream row stays locked until the OUTER commit/rollback; advancing
    evidence_revision invalidates earlier frozen stamps even without a new A
    admission. A rolled-back C unit also rolls back this revision increment.

    This is not a complete-frontier check: pending samples must still be able
    to enter C. Freeze only after all writes via read_complete_frontier, saving
    its resulting stamp. Bad evidence must additionally call mark_uncertain in
    this unit. Never acquire the inbox singleton after this gate, open another
    C Engine transaction, or perform network I/O while holding the lock.

    Does not initialize streams, enable production callers, solve concurrent
    device rebinding, or authorize automatic recovery/external notification.
    """
    row = _locked(connection, {device_id})[device_id]
    _context(row, epoch, warehouse_id)
    binding = connection.execute(
        select(col(Device.warehouse_id)).where(col(Device.id) == device_id)
    ).scalar_one_or_none()
    if binding != warehouse_id:
        raise FrontierUnavailable("current device binding mismatch")
    previous_revision = row["evidence_revision"]
    if not isinstance(previous_revision, int) or isinstance(previous_revision, bool):
        raise FrontierUnavailable("invalid stored evidence revision")
    revision = previous_revision + 1
    connection.execute(
        update(streams)
        .where(streams.c.device_id == device_id)
        .values(evidence_revision=revision)
    )
    return revision


def admission_preflight(
    connection: Connection,
    *,
    device_id: UUID,
    warehouse_id: UUID,
    epoch: UUID,
    reception_id: UUID,
    fingerprint: str,
) -> str | None:
    """Return a replay/refusal, or hold the stream lock until inbox commit.

    Caller must already hold the inbox singleton; lock order is singleton then
    sorted stream identities. Frozen checks lock one stream and never request
    the singleton. A reception's original device association is never changed.
    """
    if not isinstance(epoch, UUID) or not isinstance(reception_id, UUID):
        raise ValueError("epoch and reception UUIDs required")
    _transaction(connection)
    old = (
        connection.execute(
            select(entries).where(entries.c.reception_id == reception_id)
        )
        .mappings()
        .one_or_none()
    )
    identities = {device_id} if old is None else {device_id, old["device_id"]}
    locked = _locked(connection, identities)
    current = locked[device_id]
    _context(current, epoch, warehouse_id)
    old = (
        connection.execute(
            select(entries).where(entries.c.reception_id == reception_id)
        )
        .mappings()
        .one_or_none()
    )
    if old is not None:
        if (
            old["device_id"] != device_id
            or old["stream_epoch"] != epoch
            or old["fingerprint"] != fingerprint
        ):
            for identity in sorted(identities):
                _hold(connection, identity, "reception_id_reuse")
            return "conflict"
        return "already_processed" if old["outcome"] in _TERMINAL else None
    if current["admitted_seq"] >= MAX_ENTRIES:
        _hold(connection, device_id, "frontier_capacity")
        return "queue_full"
    return None


def record_admission(
    connection: Connection,
    *,
    device_id: UUID,
    warehouse_id: UUID,
    epoch: UUID,
    reception_id: UUID,
    fingerprint: str,
    inbox_status: str,
) -> bool:
    """Append one sequence only when the same transaction queued its raw."""
    row = _locked(connection, {device_id})[device_id]
    _context(row, epoch, warehouse_id)
    old = (
        connection.execute(
            select(entries).where(entries.c.reception_id == reception_id)
        )
        .mappings()
        .one_or_none()
    )
    if old is not None:
        if old["device_id"] != device_id or old["fingerprint"] != fingerprint:
            raise FrontierUnavailable("preflight required before conflicting reception")
        return True
    if inbox_status != "queued":
        _hold(connection, device_id, "untracked_legacy_or_refusal")
        if inbox_status.startswith("already_"):
            return False
        return True
    if row["admitted_seq"] >= MAX_ENTRIES:
        raise FrontierUnavailable("frontier capacity preflight required")
    sequence = row["admitted_seq"] + 1
    connection.execute(
        entries.insert().values(
            reception_id=reception_id,
            device_id=device_id,
            stream_epoch=epoch,
            admission_seq=sequence,
            fingerprint=fingerprint,
            outcome="pending",
        )
    )
    connection.execute(
        update(streams)
        .where(streams.c.device_id == device_id)
        .values(admitted_seq=sequence)
    )
    return True


def mark_uncertain(connection: Connection, *, device_id: UUID, epoch: UUID) -> None:
    row = _locked(connection, {device_id})[device_id]
    if row["stream_epoch"] != epoch:
        raise FrontierUnavailable("epoch mismatch")
    _hold(connection, device_id, "unsettled_bad_evidence")


def record_terminal(
    connection: Connection,
    *,
    reception_id: UUID,
    fingerprint: str,
    epoch: UUID,
    outcome: str,
    observation_id: UUID | None,
) -> None:
    """After caller's live-lease validation, advance only a contiguous prefix."""
    if outcome not in _TERMINAL or (
        outcome in _ACCEPTED and not isinstance(observation_id, UUID)
    ):
        raise ValueError("validated durable C receipt required")
    _transaction(connection)
    entry = (
        connection.execute(
            select(entries).where(entries.c.reception_id == reception_id)
        )
        .mappings()
        .one_or_none()
    )
    if entry is None:
        raise FrontierUnavailable("terminal receipt has no stable admission")
    device_id = entry["device_id"]
    row = _locked(connection, {device_id})[device_id]
    entry = (
        connection.execute(
            select(entries).where(entries.c.reception_id == reception_id)
        )
        .mappings()
        .one()
    )
    if epoch != row["stream_epoch"] or epoch != entry["stream_epoch"]:
        raise FrontierUnavailable("terminal epoch mismatch")
    if fingerprint != entry["fingerprint"]:
        raise FrontierUnavailable("terminal fingerprint mismatch")
    if entry["outcome"] != "pending":
        if entry["outcome"] != outcome or entry["observation_id"] != observation_id:
            raise FrontierUnavailable("terminal receipt cannot be rewritten")
        return
    connection.execute(
        update(entries)
        .where(entries.c.reception_id == reception_id)
        .values(outcome=outcome, observation_id=observation_id)
    )
    if outcome not in _ACCEPTED:
        _hold(connection, device_id, "terminal_bad_evidence")
    through = row["settled_through"]
    following = connection.execute(
        select(entries.c.admission_seq, entries.c.outcome)
        .where(
            entries.c.device_id == device_id,
            entries.c.stream_epoch == epoch,
            entries.c.admission_seq > through,
        )
        .order_by(entries.c.admission_seq)
        .limit(MAX_ENTRIES)
    )
    for sequence, status in following:
        if sequence != through + 1 or status == "pending":
            break
        through = sequence
    connection.execute(
        update(streams)
        .where(streams.c.device_id == device_id)
        .values(settled_through=through)
    )


def read_complete_frontier(
    connection: Connection, *, device_id: UUID, warehouse_id: UUID, epoch: UUID
) -> FrontierStamp | None:
    """Hold the same admission lock while caller checks C and stages locally.

    Does not commit or call B. It cannot certify external/direct C writes,
    historical initialization or the validity of a business recovery window.
    """
    row = _locked(connection, {device_id})[device_id]
    _context(row, epoch, warehouse_id)
    binding = connection.execute(
        select(col(Device.warehouse_id)).where(col(Device.id) == device_id)
    ).scalar_one_or_none()
    count = connection.scalar(
        select(func.count())
        .select_from(entries)
        .where(entries.c.device_id == device_id, entries.c.stream_epoch == epoch)
    )
    if (
        not row["origin_verified"]
        or row["gap_reason"]
        or binding != warehouse_id
        or row["admitted_seq"] == 0
        or row["admitted_seq"] != row["settled_through"]
        or count != row["admitted_seq"]
    ):
        return None
    return FrontierStamp(
        device_id,
        warehouse_id,
        epoch,
        row["admitted_seq"],
        row["settled_through"],
        row["evidence_revision"],
    )


def stamp_is_current(connection: Connection, stamp: FrontierStamp) -> bool:
    return (
        read_complete_frontier(
            connection,
            device_id=stamp.device_id,
            warehouse_id=stamp.warehouse_id,
            epoch=stamp.stream_epoch,
        )
        == stamp
    )
