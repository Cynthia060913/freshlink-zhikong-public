"""One bounded C recovery staging tick over durable eligible windows.

The historical-rule lookup is injected; this module does not call B directly or
install a scheduler. A caller must rerun ticks after outages and page from the
beginning on its next cycle so previously waiting windows are reconsidered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.modules.telemetry.recovery_outbox_candidate import (
    RecoveryStageResultCandidate,
    _instant,
    stage_eligible_recovery_candidate,
)
from app.modules.telemetry.recovery_progress_candidate import recovery_progress


class HistoricalRecoveryRulePort(Protocol):
    def for_original(
        self, original_source_event_id: UUID, anomaly_sampled_at: datetime
    ) -> dict[str, Any] | None: ...


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
