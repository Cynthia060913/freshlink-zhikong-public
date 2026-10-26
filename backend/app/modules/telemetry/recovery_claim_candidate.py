"""Atomically verify and lease one recovery under A's stream lock.

This C-only port does not install a worker or perform network I/O. The caller
owns the transaction and must commit before using the returned claim with a
transport. A stale recovery is never made sendable by a separate read check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import Connection

from app.modules.iot.stream_frontier import FrontierUnavailable, read_complete_frontier
from app.modules.telemetry.outbox_candidate import (
    DeliveryClaimCandidate,
    _aware_now,
    outbox,
)
from app.modules.telemetry.recovery_freshness_candidate import (
    _verify_saved_recovery_on_connection,
)


@dataclass(frozen=True)
class RecoveryClaimResultCandidate:
    status: Literal["claimed", "held"]
    reason: str = ""
    claim: DeliveryClaimCandidate | None = None


def claim_fresh_recovery_on_connection_candidate(
    connection: Connection,
    *,
    recovery_source_event_id: UUID,
    device_id: UUID,
    warehouse_id: UUID,
    stream_epoch: UUID,
    now: datetime,
    lease_seconds: int = 30,
    max_attempts: int = 10,
) -> RecoveryClaimResultCandidate:
    """Lock device, recheck complete evidence, then CAS-lease in one transaction.

    The caller must not send while this transaction is open. A late admission
    blocks on the device lock and invalidates the frozen stamp after commit.
    Only the current lease token can record a later B receipt. A recovery with
    new evidence after a prior attempt stays held; this port never rewrites its
    frozen body, digest or frontier to make a retry look fresh.
    """
    if not connection.in_transaction():
        raise ValueError("caller must own an open transaction")
    if not all(
        isinstance(value, UUID)
        for value in (
            recovery_source_event_id,
            device_id,
            warehouse_id,
            stream_epoch,
        )
    ):
        raise ValueError("trusted recovery and stream UUIDs required")
    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int)
        or not 1 <= lease_seconds <= 300
    ):
        raise ValueError("lease_seconds must be 1-300")
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or not 1 <= max_attempts <= 100
    ):
        raise ValueError("max_attempts must be 1-100")
    current = _aware_now(now)
    try:
        stamp = read_complete_frontier(
            connection,
            device_id=device_id,
            warehouse_id=warehouse_id,
            epoch=stream_epoch,
        )
    except FrontierUnavailable:
        return RecoveryClaimResultCandidate("held", "frontier_unavailable")
    if stamp is None:
        return RecoveryClaimResultCandidate("held", "frontier_incomplete")
    row = (
        connection.execute(
            select(outbox)
            .where(outbox.c.source_event_id == recovery_source_event_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["state"] not in {"pending", "leased"}:
        return RecoveryClaimResultCandidate("held", "recovery_not_due")
    if row["attempt_count"] >= max_attempts:
        return RecoveryClaimResultCandidate("held", "retry_budget_exhausted")
    try:
        verified = _verify_saved_recovery_on_connection(
            connection,
            stamp=stamp,
            recovery_source_event_id=recovery_source_event_id,
            device_id=device_id,
            warehouse_id=warehouse_id,
        )
    except ValueError, TypeError, KeyError:
        return RecoveryClaimResultCandidate("held", "invalid_stored_evidence")
    if verified.status != "ready_at_check":
        return RecoveryClaimResultCandidate("held", verified.reason)
    token = uuid4()
    changed = connection.execute(
        update(outbox)
        .where(
            outbox.c.source_event_id == recovery_source_event_id,
            outbox.c.attempt_count == row["attempt_count"],
            outbox.c.body_sha256 == row["body_sha256"],
            outbox.c.frozen_evidence_revision == stamp.evidence_revision,
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
        return RecoveryClaimResultCandidate("held", "recovery_not_due")
    return RecoveryClaimResultCandidate(
        "claimed",
        claim=DeliveryClaimCandidate(
            source_event_id=recovery_source_event_id,
            lease_token=token,
            body=dict(row["body"]),
            body_sha256=row["body_sha256"],
            attempt_count=row["attempt_count"] + 1,
        ),
    )
