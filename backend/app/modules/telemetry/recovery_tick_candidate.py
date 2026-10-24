"""One bounded C recovery staging tick over durable eligible windows.

The historical-rule lookup is injected; this module does not call B directly or
install a scheduler. A caller must rerun ticks after outages and page from the
beginning on its next cycle so previously waiting windows are reconsidered.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.modules.iot.stream_frontier import FrontierUnavailable
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.recovery_outbox_candidate import (
    RecoveryStageResultCandidate,
    _instant,
    stage_eligible_recovery_candidate,
    stage_gated_recovery_candidate,
)
from app.modules.telemetry.recovery_progress_candidate import recovery_progress


class HistoricalRecoveryRulePort(Protocol):
    def for_original(
        self, original_source_event_id: UUID, anomaly_sampled_at: datetime
    ) -> dict[str, Any] | None: ...


class LocalBHistoricalRecoveryRuleCandidate:
    """Read B's original sampled-at F02 rule outside A's evidence lock."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def for_original(
        self, original_source_event_id: UUID, anomaly_sampled_at: datetime
    ) -> dict[str, Any] | None:
        from app.modules.alerts.services.rules import select_rules_for_observation

        with self._engine.connect() as connection:
            body = connection.scalar(
                select(outbox.c.body).where(
                    outbox.c.source_event_id == original_source_event_id
                )
            )
        if (
            not isinstance(body, dict)
            or body.get("kind") != "temperature_high"
            or body.get("source_event_id") != str(original_source_event_id)
            or _instant(body.get("sampled_at")) != anomaly_sampled_at
        ):
            raise ValueError("original anomaly identity or sampled time mismatch")
        try:
            warehouse_id = UUID(body["warehouse_id"])
            batch_id = UUID(body["batch_id"])
        except (KeyError, TypeError, ValueError) as exc:  # fmt: skip
            raise ValueError("original anomaly has invalid batch scope") from exc
        with Session(self._engine) as session:
            selected = select_rules_for_observation(
                session,
                warehouse_id=warehouse_id,
                batch_ids=(batch_id,),
                metric_code="temperature",
                sampled_at=anomaly_sampled_at,
            ).model_dump(mode="json")
        if selected.get("status") == "no_rule":
            return None
        rules = selected.get("rules")
        if (
            selected.get("status") != "configured"
            or not isinstance(rules, list)
            or len(rules) != 1
        ):
            raise ValueError("historical B rule selection is not singular")
        if not isinstance(rules[0], dict):
            raise ValueError("historical B rule is not an object")
        return rules[0]


@dataclass(frozen=True)
class RecoveryTickPageCandidate:
    status: Literal["ok", "retryable_failure"]
    results: tuple[tuple[UUID, RecoveryStageResultCandidate], ...] = ()
    next_after: UUID | None = None


def stage_recovery_page_candidate(
    engine: Engine,
    *,
    historical_rules: HistoricalRecoveryRulePort,
    now: datetime,
    after: UUID | None = None,
    limit: int = 50,
) -> RecoveryTickPageCandidate:
    """Read a stable UUID page; never substitute a current rule for history.

    `next_after` advances even when one item is waiting or held, so one bad
    window cannot starve the rest of this scan. A future full scan starts with
    `after=None` to retry waiting work. No B send or alert resolution happens.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be 1-100")
    if after is not None and not isinstance(after, UUID):
        raise ValueError("after must be a UUID")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    query = select(
        recovery_progress.c.original_source_event_id,
        recovery_progress.c.anomaly_sampled_at_utc,
    ).where(recovery_progress.c.eligible.is_(True))
    if after is not None:
        query = query.where(recovery_progress.c.original_source_event_id > after)
    query = query.order_by(recovery_progress.c.original_source_event_id).limit(
        limit + 1
    )
    try:
        with engine.connect() as connection:
            rows = connection.execute(query).all()
    except SQLAlchemyError:
        return RecoveryTickPageCandidate("retryable_failure", next_after=after)

    has_more = len(rows) > limit
    results: list[tuple[UUID, RecoveryStageResultCandidate]] = []
    for original_id, sampled_at_text in rows[:limit]:
        preliminary = stage_eligible_recovery_candidate(
            engine,
            original_source_event_id=original_id,
            historical_rule=None,
            now=now,
        )
        if preliminary.status != "waiting_rule":
            results.append((original_id, preliminary))
            continue
        try:
            sampled_at = _instant(sampled_at_text)
            selected = historical_rules.for_original(original_id, sampled_at)
        except ValueError as exc:
            result = RecoveryStageResultCandidate(
                "held_invalid_evidence", reason=str(exc)
            )
        except PermissionError:
            result = RecoveryStageResultCandidate(
                "retryable_failure", reason="rule_auth_unavailable"
            )
        except (TimeoutError, ConnectionError):  # fmt: skip
            result = RecoveryStageResultCandidate(
                "retryable_failure", reason="rule_unavailable"
            )
        except SQLAlchemyError:
            # A B-side historical lookup may use the shared database session.
            # A transient read failure must not abort the whole page or turn
            # this window into a permanent missing-rule decision.
            result = RecoveryStageResultCandidate(
                "retryable_failure", reason="rule_storage_unavailable"
            )
        else:
            result = stage_eligible_recovery_candidate(
                engine,
                original_source_event_id=original_id,
                historical_rule=selected,
                now=now,
            )
        results.append((original_id, result))

    return RecoveryTickPageCandidate(
        "ok",
        tuple(results),
        rows[limit - 1][0] if has_more else None,
    )


def stage_gated_recovery_page_candidate(
    engine: Engine,
    *,
    historical_rules: HistoricalRecoveryRulePort,
    frontier_epochs: Mapping[UUID, UUID],
    now: datetime,
    after: UUID | None = None,
    limit: int = 50,
) -> RecoveryTickPageCandidate:
    """Stage one bounded recovery page with C/A writes sharing the device gate.

    The epochs must come from A's trusted runtime, not telemetry payloads. B
    historical selection finishes before the gated write. This explicit port
    does not install a scheduler or authorize recovery network delivery.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be 1-100")
    if after is not None and not isinstance(after, UUID):
        raise ValueError("after must be a UUID")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    epochs = dict(frontier_epochs)
    if not all(
        isinstance(key, UUID) and isinstance(value, UUID)
        for key, value in epochs.items()
    ):
        raise ValueError("frontier_epochs must map trusted UUIDs")
    query = select(
        recovery_progress.c.original_source_event_id,
        recovery_progress.c.anomaly_sampled_at_utc,
        recovery_progress.c.device_id,
        recovery_progress.c.warehouse_id,
    ).where(recovery_progress.c.eligible.is_(True))
    if after is not None:
        query = query.where(recovery_progress.c.original_source_event_id > after)
    query = query.order_by(recovery_progress.c.original_source_event_id).limit(
        limit + 1
    )
    try:
        with engine.connect() as connection:
            rows = connection.execute(query).all()
    except SQLAlchemyError:
        return RecoveryTickPageCandidate("retryable_failure", next_after=after)

    has_more = len(rows) > limit
    results: list[tuple[UUID, RecoveryStageResultCandidate]] = []
    for original_id, sampled_at_text, device_id, warehouse_id in rows[:limit]:
        preliminary = stage_eligible_recovery_candidate(
            engine,
            original_source_event_id=original_id,
            historical_rule=None,
            now=now,
        )
        if preliminary.status != "waiting_rule":
            results.append((original_id, preliminary))
            continue
        epoch = epochs.get(device_id)
        if epoch is None:
            results.append(
                (
                    original_id,
                    RecoveryStageResultCandidate(
                        "held_invalid_evidence", reason="unverified_stream_scope"
                    ),
                )
            )
            continue
        try:
            sampled_at = _instant(sampled_at_text)
            selected = historical_rules.for_original(original_id, sampled_at)
            if selected is None:
                result = RecoveryStageResultCandidate("waiting_rule")
            else:
                result = stage_gated_recovery_candidate(
                    engine,
                    device_id=device_id,
                    warehouse_id=warehouse_id,
                    stream_epoch=epoch,
                    original_source_event_id=original_id,
                    historical_rule=selected,
                    now=now,
                )
        except ValueError:
            result = RecoveryStageResultCandidate(
                "held_invalid_evidence", reason="invalid_rule_or_evidence"
            )
        except PermissionError:
            result = RecoveryStageResultCandidate(
                "retryable_failure", reason="rule_auth_unavailable"
            )
        except (TimeoutError, ConnectionError):  # fmt: skip
            result = RecoveryStageResultCandidate(
                "retryable_failure", reason="rule_unavailable"
            )
        except (SQLAlchemyError, FrontierUnavailable):  # fmt: skip
            result = RecoveryStageResultCandidate(
                "retryable_failure", reason="storage_or_frontier_unavailable"
            )
        results.append((original_id, result))
    return RecoveryTickPageCandidate(
        "ok",
        tuple(results),
        rows[limit - 1][0] if has_more else None,
    )
