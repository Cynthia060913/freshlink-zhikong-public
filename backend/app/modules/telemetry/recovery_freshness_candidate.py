"""Read-only recovery freshness check under A's device gate.

This is a necessary pre-send check, not a sender or permission to send. The
device lock is released before any network operation, so a later admission can
still require a separate B correction event after an accepted recovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.modules.iot.stream_frontier import (
    FrontierUnavailable,
    read_complete_frontier,
)
from app.modules.telemetry.f03_body_candidate import (
    f03_digest_candidate,
    normalize_f03_body_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.recovery_outbox_candidate import (
    _instant,
    _stored_observation_ids,
    _verified_evidence,
)
from app.modules.telemetry.recovery_progress_candidate import recovery_progress
from app.modules.telemetry.recovery_reception_evidence_candidate import (
    has_unresolved_reception_evidence_candidate,
)
from app.modules.telemetry.recovery_window_candidate import RecoveryRuleCandidate
from app.modules.telemetry.storage_candidate import observations


@dataclass(frozen=True)
class RecoveryFreshnessCandidate:
    status: Literal["ready_at_check", "held", "retryable_failure"]
    reason: str = ""


def check_recovery_freshness_candidate(
    engine: Engine,
    *,
    recovery_source_event_id: UUID,
    device_id: UUID,
    warehouse_id: UUID,
    stream_epoch: UUID,
) -> RecoveryFreshnessCandidate:
    """Check frozen identity, current A revision and C evidence in one PG read.

    A's stream row is locked through the read transaction. No B call, claim,
    attempt increment or network send occurs here. The caller must treat any
    non-ready result as a hold; even ready is only true at this instant.
    """
    if not all(
        isinstance(value, UUID)
        for value in (
            recovery_source_event_id,
            device_id,
            warehouse_id,
            stream_epoch,
        )
    ):
        raise ValueError("trusted recovery and frontier UUIDs required")
    try:
        with engine.begin() as connection:
            stamp = read_complete_frontier(
                connection,
                device_id=device_id,
                warehouse_id=warehouse_id,
                epoch=stream_epoch,
            )
            if stamp is None:
                return RecoveryFreshnessCandidate("held", "frontier_incomplete")
            recovery = (
                connection.execute(
                    select(outbox).where(
                        outbox.c.source_event_id == recovery_source_event_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if recovery is None or recovery["state"] not in {"pending", "leased"}:
                return RecoveryFreshnessCandidate("held", "recovery_not_due")
            frozen = (
                recovery["frozen_device_id"],
                recovery["frozen_warehouse_id"],
                recovery["frozen_stream_epoch"],
                recovery["frozen_admitted_seq"],
                recovery["frozen_settled_through"],
                recovery["frozen_evidence_revision"],
            )
            if frozen != (
                stamp.device_id,
                stamp.warehouse_id,
                stamp.stream_epoch,
                stamp.admitted_seq,
                stamp.settled_through,
                stamp.evidence_revision,
            ):
                return RecoveryFreshnessCandidate("held", "stale_frozen_frontier")
            body = normalize_f03_body_candidate(recovery["body"])
            if (
                f03_digest_candidate(body).removeprefix("sha256:")
                != recovery["body_sha256"]
            ):
                return RecoveryFreshnessCandidate("held", "invalid_stored_evidence")
            original_id = recovery["related_source_event_id"]
            if (
                body["kind"] != "temperature_recovered"
                or original_id is None
                or body["related_source_event_id"] != str(original_id)
                or body["source_event_id"] != str(recovery_source_event_id)
                or body["warehouse_id"] != str(warehouse_id)
            ):
                return RecoveryFreshnessCandidate("held", "invalid_recovery_link")
            original = (
                connection.execute(
                    select(outbox).where(outbox.c.source_event_id == original_id)
                )
                .mappings()
                .one_or_none()
            )
            progress = (
                connection.execute(
                    select(recovery_progress).where(
                        recovery_progress.c.original_source_event_id == original_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                original is None
                or original["state"] != "delivered"
                or progress is None
                or not progress["eligible"]
                or progress["device_id"] != device_id
                or progress["warehouse_id"] != warehouse_id
                or body["batch_id"] != str(progress["batch_id"])
            ):
                return RecoveryFreshnessCandidate("held", "original_or_window_unready")
            original_body = normalize_f03_body_candidate(original["body"])
            if (
                f03_digest_candidate(original_body).removeprefix("sha256:")
                != original["body_sha256"]
            ):
                return RecoveryFreshnessCandidate("held", "invalid_stored_evidence")
            if (
                original_body["kind"] != "temperature_high"
                or original_body["related_source_event_id"] is not None
                or original_body["source_event_id"] != str(original_id)
                or original_body["observation_ids"] != [str(original["observation_id"])]
                or original_body["warehouse_id"] != str(warehouse_id)
                or original_body["batch_id"] != body["batch_id"]
                or _instant(original_body["sampled_at"])
                != _instant(progress["anomaly_sampled_at_utc"])
            ):
                return RecoveryFreshnessCandidate("held", "invalid_original_link")
            original_observation = (
                connection.execute(
                    select(observations).where(
                        observations.c.id == original["observation_id"]
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                original_observation is None
                or original_observation["device_id"] != device_id
                or original_observation["warehouse_id"] != warehouse_id
            ):
                return RecoveryFreshnessCandidate("held", "original_owner_mismatch")
            if has_unresolved_reception_evidence_candidate(
                connection,
                device_id=device_id,
                warehouse_id=warehouse_id,
                original_received_at=original_observation["first_received_at_utc"],
            ):
                return RecoveryFreshnessCandidate("held", "unresolved_bad_reception")
            ids = _stored_observation_ids(progress["observation_ids"])
            if (
                body["observation_ids"] != [str(value) for value in ids]
                or recovery["observation_id"] != ids[-1]
                or _instant(body["sampled_at"])
                != _instant(progress["last_sampled_at_utc"])
            ):
                return RecoveryFreshnessCandidate("held", "window_changed")
            rows = {
                row["id"]: row
                for row in connection.execute(
                    select(observations).where(observations.c.id.in_(ids))
                )
                .mappings()
                .all()
            }
            rule = RecoveryRuleCandidate(
                progress["comparison"],
                progress["threshold_c"],
                progress["min_samples"],
                progress["max_gap_seconds"],
            )
            verified_ids, sampled_at, value = _verified_evidence(progress, rows, rule)
            if (
                verified_ids != ids
                or sampled_at != _instant(body["sampled_at"])
                or value != body["value"]
            ):
                return RecoveryFreshnessCandidate("held", "window_changed")
            return RecoveryFreshnessCandidate("ready_at_check")
    except (SQLAlchemyError, FrontierUnavailable):  # fmt: skip
        return RecoveryFreshnessCandidate(
            "retryable_failure", "storage_or_frontier_unavailable"
        )
    except (ValueError, TypeError, KeyError):  # fmt: skip
        return RecoveryFreshnessCandidate("held", "invalid_stored_evidence")
