"""Bounded C-only scan from saved observations into one recovery window.

The caller supplies a trusted batch and B's rule at the original anomaly time.
This explicit tick has no scheduler, B lookup, public route, or migration. One
worker per original source must own the tick until production locking exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.modules.telemetry.f03_body_candidate import (
    f03_digest_candidate,
    normalize_f03_body_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.recovery_observation_candidate import (
    RecoveryAdvanceResultCandidate,
    advance_saved_recovery_observation_candidate,
)
from app.modules.telemetry.recovery_progress_candidate import load_recovery_progress
from app.modules.telemetry.recovery_reception_evidence_candidate import (
    has_unresolved_reception_evidence_candidate,
)
from app.modules.telemetry.recovery_rule_candidate import bind_original_recovery_rule
from app.modules.telemetry.storage_candidate import observations


@dataclass(frozen=True)
class RecoveryObservationPageCandidate:
    status: Literal[
        "ok", "eligible", "waiting_rule", "held_invalid_evidence", "retryable_failure"
    ]
    results: tuple[tuple[UUID, RecoveryAdvanceResultCandidate], ...] = ()
    has_more: bool = False
    reason: str = ""


def _received_at(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("stored received_at is invalid")
    if value.tzinfo is None:  # SQLite test driver drops the UTC offset.
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def scan_saved_recovery_observations_candidate(
    engine: Engine,
    *,
    original_source_event_id: UUID,
    batch_id: UUID,
    historical_rule: dict[str, Any] | None,
    limit: int = 50,
) -> RecoveryObservationPageCandidate:
    """Advance one original's receipt-ordered page; progress is the cursor.

    A restart repeats this call from the persisted receipt watermark. A bad
    stored row is consumed once by the window as unknown/reset; malformed
    evidence that cannot be trusted stops the page without skipping the row.
    An eligible window is frozen and staged by the separate recovery tick.
    """
    if not isinstance(original_source_event_id, UUID) or not isinstance(batch_id, UUID):
        raise ValueError("recovery IDs must be UUIDs")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be 1-100")
    try:
        with engine.connect() as connection:
            original = (
                connection.execute(
                    select(outbox).where(
                        outbox.c.source_event_id == original_source_event_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            source = (
                connection.execute(
                    select(observations).where(
                        observations.c.id == original["observation_id"]
                    )
                )
                .mappings()
                .one_or_none()
                if original is not None
                else None
            )
            unresolved_reception = (
                has_unresolved_reception_evidence_candidate(
                    connection,
                    device_id=source["device_id"],
                    warehouse_id=source["warehouse_id"],
                    original_received_at=source["first_received_at_utc"],
                )
                if source is not None
                else False
            )
        progress = load_recovery_progress(engine, original_source_event_id)
    except SQLAlchemyError:
        return RecoveryObservationPageCandidate(
            "retryable_failure", reason="storage_unavailable"
        )
    except ValueError:
        return RecoveryObservationPageCandidate(
            "held_invalid_evidence", reason="invalid_stored_progress"
        )
    if unresolved_reception:
        return RecoveryObservationPageCandidate(
            "held_invalid_evidence", reason="unresolved_reception_evidence"
        )
    try:
        if original is None or source is None:
            raise ValueError("original anomaly or observation is unavailable")
        body = normalize_f03_body_candidate(original["body"])
        if (
            f03_digest_candidate(body).removeprefix("sha256:")
            != original["body_sha256"]
            or body["kind"] != "temperature_high"
            or body["source_event_id"] != str(original_source_event_id)
            or body["observation_ids"] != [str(source["id"])]
            or body["batch_id"] != str(batch_id)
            or body["warehouse_id"] != str(source["warehouse_id"])
        ):
            raise ValueError("original anomaly identity differs from stored evidence")
        if bind_original_recovery_rule(body, historical_rule) is None:
            return RecoveryObservationPageCandidate("waiting_rule")
        cursor_at = _received_at(source["first_received_at_utc"])
        cursor_id = source["id"]
        if progress is not None:
            if (
                progress.device_id != source["device_id"]
                or progress.warehouse_id != source["warehouse_id"]
                or progress.batch_id != batch_id
            ):
                raise ValueError("stored recovery identity changed")
            if progress.eligible:
                return RecoveryObservationPageCandidate("eligible")
            if progress.last_received_at is not None:
                cursor_at = _received_at(progress.last_received_at)
                cursor_id = progress.last_received_observation_id
                if cursor_id is None:
                    raise ValueError("stored receipt watermark is incomplete")
    except (KeyError, TypeError, ValueError):  # fmt: skip
        return RecoveryObservationPageCandidate(
            "held_invalid_evidence", reason="invalid_original_or_rule"
        )
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                select(observations.c.id)
                .where(
                    observations.c.device_id == source["device_id"],
                    observations.c.warehouse_id == source["warehouse_id"],
                    or_(
                        observations.c.first_received_at_utc > cursor_at,
                        and_(
                            observations.c.first_received_at_utc == cursor_at,
                            observations.c.id > cursor_id,
                        ),
                    ),
                )
                .order_by(observations.c.first_received_at_utc, observations.c.id)
                .limit(limit + 1)
            ).all()
    except SQLAlchemyError:
        return RecoveryObservationPageCandidate(
            "retryable_failure", reason="storage_unavailable"
        )
    results: list[tuple[UUID, RecoveryAdvanceResultCandidate]] = []
    for (observation_id,) in rows[:limit]:
        result = advance_saved_recovery_observation_candidate(
            engine,
            original_source_event_id=original_source_event_id,
            observation_id=observation_id,
            batch_id=batch_id,
            historical_rule=historical_rule,
        )
        results.append((observation_id, result))
        if result.status != "advanced":
            return RecoveryObservationPageCandidate(
                result.status, tuple(results), reason=result.reason
            )
        if result.decision == "eligible":
            return RecoveryObservationPageCandidate("eligible", tuple(results))
    return RecoveryObservationPageCandidate(
        "ok", tuple(results), has_more=len(rows) > limit
    )
