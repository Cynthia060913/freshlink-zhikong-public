"""Bounded recovery frontier renewal after additional admitted observations.

The caller owns the PostgreSQL transaction. A's stream row is locked first;
this port never sends B HTTP, claims a lease, or changes frozen F03 content.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from math import isfinite
from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from app.modules.iot.stream_frontier import (
    FrontierUnavailable,
    read_complete_frontier,
)
from app.modules.iot.stream_frontier_models import entries
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.recovery_freshness_candidate import (
    _verify_saved_recovery_on_connection,
)
from app.modules.telemetry.recovery_observation_candidate import _sample_from_saved
from app.modules.telemetry.recovery_outbox_candidate import _instant
from app.modules.telemetry.recovery_progress_candidate import recovery_progress
from app.modules.telemetry.storage_candidate import observations, receptions


@dataclass(frozen=True)
class RecoveryRefreezeCandidate:
    status: Literal["refrozen", "already_current", "held"]
    reason: str = ""


def _receipt_matches_admission(
    entry, receipt, observation, *, device_id, warehouse_id
) -> bool:
    """Recheck both durable hashes before treating a replay as known evidence."""
    raw_text = receipt["raw_text"]
    received_text = receipt["received_at_utc"]
    if (
        not isinstance(raw_text, str)
        or not isinstance(received_text, str)
        or raw_text != observation["raw_text"]
        or receipt["device_id"] != device_id
        or receipt["warehouse_id"] != warehouse_id
    ):
        return False
    receipt_digest = hashlib.sha256(
        "\x00".join(
            (raw_text, str(device_id), str(warehouse_id), received_text)
        ).encode("utf-8")
    ).hexdigest()
    admission_digest = hashlib.sha256(
        json.dumps(
            [
                str(entry["reception_id"]),
                str(device_id),
                str(warehouse_id),
                _instant(received_text).isoformat(),
                raw_text,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (
        receipt["receipt_sha256"] == receipt_digest
        and entry["fingerprint"] == admission_digest
    )


def refresh_pending_recovery_frontier_on_connection_candidate(
    connection: Connection,
    *,
    recovery_source_event_id: UUID,
    device_id: UUID,
    warehouse_id: UUID,
    stream_epoch: UUID,
) -> RecoveryRefreezeCandidate:
    """Renew only the frontier stamp after verifying every new A admission.

    A pending recovery with attempt_count=0 may be renewed in the SAME epoch
    after every new admission is terminal and all new sensor evidence still
    matches the frozen historical recovery rule. A verified replay of an old,
    already admitted high adds no new sensor evidence. Any new high, invalid,
    missing, late, gap, epoch change or tampered evidence stays held. Leased
    or delivered work cannot be re-frozen. No network I/O under this lock.
    """
    if not connection.in_transaction():
        raise ValueError("caller must own an open C transaction")
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
    try:
        stamp = read_complete_frontier(
            connection,
            device_id=device_id,
            warehouse_id=warehouse_id,
            epoch=stream_epoch,
        )
    except FrontierUnavailable:
        return RecoveryRefreezeCandidate("held", "frontier_unavailable")
    if stamp is None:
        return RecoveryRefreezeCandidate("held", "frontier_incomplete")
    recovery = (
        connection.execute(
            select(outbox)
            .where(outbox.c.source_event_id == recovery_source_event_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if (
        recovery is None
        or recovery["state"] != "pending"
        or recovery["attempt_count"] != 0
        or recovery["lease_token"] is not None
        or recovery["lease_until_utc"] is not None
    ):
        return RecoveryRefreezeCandidate("held", "recovery_not_unclaimed")
    frozen = (
        recovery["frozen_device_id"],
        recovery["frozen_warehouse_id"],
        recovery["frozen_stream_epoch"],
        recovery["frozen_admitted_seq"],
        recovery["frozen_settled_through"],
        recovery["frozen_evidence_revision"],
    )
    current = (
        stamp.device_id,
        stamp.warehouse_id,
        stamp.stream_epoch,
        stamp.admitted_seq,
        stamp.settled_through,
        stamp.evidence_revision,
    )
    if (
        frozen[0:3] != current[0:3]
        or not isinstance(frozen[3], int)
        or isinstance(frozen[3], bool)
        or frozen[3] <= 0
        or frozen[3] != frozen[4]
        or not isinstance(frozen[5], int)
        or isinstance(frozen[5], bool)
        or frozen[5] > stamp.evidence_revision
    ):
        return RecoveryRefreezeCandidate("held", "frozen_frontier_mismatch")
    try:
        verified = _verify_saved_recovery_on_connection(
            connection,
            stamp=stamp,
            recovery_source_event_id=recovery_source_event_id,
            device_id=device_id,
            warehouse_id=warehouse_id,
            allow_stale_frontier=True,
        )
    except ValueError, TypeError, KeyError:
        return RecoveryRefreezeCandidate("held", "invalid_stored_evidence")
    if verified.status != "ready_at_check":
        return RecoveryRefreezeCandidate("held", verified.reason)
    if frozen == current:
        return RecoveryRefreezeCandidate("already_current")
    # A different recovery window can advance the shared evidence revision
    # without adding an admission. The full saved-evidence check above still
    # gates this renewal; only a regressed admission sequence is unsafe.
    if frozen[3] > stamp.admitted_seq:
        return RecoveryRefreezeCandidate("held", "no_new_admission")
    original_id = recovery["related_source_event_id"]
    progress = (
        connection.execute(
            select(recovery_progress).where(
                recovery_progress.c.original_source_event_id == original_id
            )
        )
        .mappings()
        .one()
    )
    admission_rows = (
        connection.execute(
            select(entries)
            .where(
                entries.c.device_id == device_id,
                entries.c.stream_epoch == stream_epoch,
                entries.c.admission_seq > frozen[3],
                entries.c.admission_seq <= stamp.admitted_seq,
            )
            .order_by(entries.c.admission_seq)
        )
        .mappings()
        .all()
    )
    if len(admission_rows) != stamp.admitted_seq - frozen[3]:
        return RecoveryRefreezeCandidate("held", "admission_gap")
    last_sampled = _instant(progress["last_sampled_at_utc"])
    threshold = progress["threshold_c"]
    comparison = progress["comparison"]
    max_gap = progress["max_gap_seconds"]
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (float, int))
        or not isfinite(threshold)
        or comparison not in {"<", "<="}
        or not isinstance(max_gap, int)
        or not 1 <= max_gap <= 90
    ):
        return RecoveryRefreezeCandidate("held", "invalid_historical_rule")
    for expected_seq, entry in enumerate(admission_rows, frozen[3] + 1):
        if (
            entry["admission_seq"] != expected_seq
            or entry["outcome"] not in {"accepted_new", "accepted_duplicate"}
            or entry["observation_id"] is None
        ):
            return RecoveryRefreezeCandidate("held", "unsafe_new_admission")
        observation = (
            connection.execute(
                select(observations).where(observations.c.id == entry["observation_id"])
            )
            .mappings()
            .one_or_none()
        )
        receipt = (
            connection.execute(
                select(receptions).where(receptions.c.id == entry["reception_id"])
            )
            .mappings()
            .one_or_none()
        )
        if (
            observation is None
            or receipt is None
            or receipt["observation_id"] != entry["observation_id"]
            or receipt["device_id"] != device_id
            or receipt["warehouse_id"] != warehouse_id
            or receipt["outcome"] != entry["outcome"]
        ):
            return RecoveryRefreezeCandidate("held", "admission_receipt_mismatch")
        try:
            sample = _sample_from_saved(observation, batch_id=progress["batch_id"])
            receipt_at = _instant(receipt["received_at_utc"])
        except ValueError, TypeError, KeyError:
            return RecoveryRefreezeCandidate("held", "invalid_new_observation")
        try:
            receipt_matches = _receipt_matches_admission(
                entry,
                receipt,
                observation,
                device_id=device_id,
                warehouse_id=warehouse_id,
            )
        except ValueError, TypeError, KeyError:
            receipt_matches = False
        if not receipt_matches:
            return RecoveryRefreezeCandidate("held", "admission_receipt_mismatch")
        if (
            sample.device_id != device_id
            or sample.warehouse_id != warehouse_id
            or sample.quality != ("ok",)
            or sample.sampled_at is None
            or sample.temperature_c is None
        ):
            return RecoveryRefreezeCandidate("held", "new_evidence_not_recovered")
        if entry["outcome"] == "accepted_new":
            if (
                sample.temperature_c >= threshold
                if comparison == "<"
                else sample.temperature_c > threshold
            ):
                return RecoveryRefreezeCandidate("held", "new_evidence_not_recovered")
            if (
                receipt_at != sample.received_at
                or sample.sampled_at <= last_sampled
                or sample.sampled_at - last_sampled > timedelta(seconds=max_gap)
            ):
                return RecoveryRefreezeCandidate("held", "new_window_gap_or_reorder")
            last_sampled = sample.sampled_at
        else:
            # A duplicate can name an old hot observation. It adds no new
            # sensor evidence only when its first accepted_new admission in
            # this same epoch is still present and matches the saved raw.
            witnesses = (
                connection.execute(
                    select(entries).where(
                        entries.c.device_id == device_id,
                        entries.c.stream_epoch == stream_epoch,
                        entries.c.observation_id == entry["observation_id"],
                        entries.c.outcome == "accepted_new",
                        entries.c.admission_seq < entry["admission_seq"],
                    )
                )
                .mappings()
                .all()
            )
            if len(witnesses) != 1:
                return RecoveryRefreezeCandidate("held", "unverified_duplicate_origin")
            witness = witnesses[0]
            first_receipt = (
                connection.execute(
                    select(receptions).where(receptions.c.id == witness["reception_id"])
                )
                .mappings()
                .one_or_none()
            )
            try:
                witness_valid = (
                    first_receipt is not None
                    and first_receipt["outcome"] == "accepted_new"
                    and first_receipt["observation_id"] == observation["id"]
                    and _instant(first_receipt["received_at_utc"]) == sample.received_at
                    and _receipt_matches_admission(
                        witness,
                        first_receipt,
                        observation,
                        device_id=device_id,
                        warehouse_id=warehouse_id,
                    )
                )
            except ValueError, TypeError, KeyError:
                witness_valid = False
            if not witness_valid:
                return RecoveryRefreezeCandidate("held", "unverified_duplicate_origin")
            if (
                sample.temperature_c >= threshold
                if comparison == "<"
                else sample.temperature_c > threshold
            ) and witness["admission_seq"] > frozen[3]:
                return RecoveryRefreezeCandidate("held", "new_evidence_not_recovered")
    changed = connection.execute(
        update(outbox)
        .where(
            outbox.c.source_event_id == recovery_source_event_id,
            outbox.c.state == "pending",
            outbox.c.attempt_count == 0,
            outbox.c.lease_token.is_(None),
            outbox.c.lease_until_utc.is_(None),
            outbox.c.frozen_device_id == frozen[0],
            outbox.c.frozen_warehouse_id == frozen[1],
            outbox.c.frozen_stream_epoch == frozen[2],
            outbox.c.frozen_admitted_seq == frozen[3],
            outbox.c.frozen_settled_through == frozen[4],
            outbox.c.frozen_evidence_revision == frozen[5],
        )
        .values(
            frozen_admitted_seq=stamp.admitted_seq,
            frozen_settled_through=stamp.settled_through,
            frozen_evidence_revision=stamp.evidence_revision,
        )
    )
    if changed.rowcount != 1:
        raise RuntimeError("recovery frontier renewal lost its locked row")
    return RecoveryRefreezeCandidate("refrozen")
