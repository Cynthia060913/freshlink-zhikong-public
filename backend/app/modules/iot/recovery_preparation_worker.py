"""Opt-in bounded local recovery preparation; never claim or contact B.

Reuse the reviewed A/C same-Connection caller, durable receipt watermark and
frontier renewal. Discovery is not a claim. A process-local cursor only ensures
fair scheduling: losing it cannot lose business progress, which lives in PG.
"""

import logging
import math
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.modules.iot.recovery_observation_service import refresh_once
from app.modules.iot.recovery_observation_worker import GatedRecoveryObservationWorker
from app.modules.iot.stream_frontier import FrontierUnavailable
from app.modules.iot.telemetry_service import TelemetryServiceSettings, verify_scope
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.storage_candidate import observations

logger = logging.getLogger(__name__)


class RecoveryPreparationWorker:
    """Prepare up to 14 originals per tick, including every applicable batch.

    Explicitly starting this worker does not enable any recovery sender. A
    later sender still needs its own reviewed freshness/claim/wire protocol.
    """

    def __init__(self, engine: Engine, settings: TelemetryServiceSettings) -> None:
        self.engine, self.settings = engine, settings
        self.stop_requested = threading.Event()
        self._cursor: UUID | None = None
        self._observer = GatedRecoveryObservationWorker(
            engine, frontier_epochs=settings.frontier_epochs
        )

    def _originals(self) -> list[UUID]:
        recovered = outbox.alias("completed_recovery")
        completed = (
            select(recovered.c.source_event_id)
            .where(
                recovered.c.related_source_event_id == outbox.c.source_event_id,
                recovered.c.body["kind"].as_string() == "temperature_recovered",
                recovered.c.state == "delivered",
            )
            .correlate(outbox)
            .exists()
        )
        query = (
            select(outbox.c.source_event_id)
            .join(observations, observations.c.id == outbox.c.observation_id)
            .where(
                observations.c.device_id.in_(self.settings.device_ids),
                outbox.c.state == "delivered",
                outbox.c.related_source_event_id.is_(None),
                outbox.c.body["kind"].as_string() == "temperature_high",
                ~completed,
            )
            .order_by(outbox.c.source_event_id)
        )
        with self.engine.connect() as connection:
            tail = query
            if self._cursor is not None:
                tail = tail.where(outbox.c.source_event_id > self._cursor)
            sources = list(connection.scalars(tail.limit(14)))
            if self._cursor is not None and len(sources) < 14:
                sources.extend(
                    connection.scalars(
                        query.where(outbox.c.source_event_id <= self._cursor).limit(
                            14 - len(sources)
                        )
                    )
                )
        return sources

    def run_once(self, *, now: datetime | None = None) -> dict[str, int]:
        current = datetime.now(UTC) if now is None else now
        if not isinstance(current, datetime) or current.tzinfo is None:
            raise ValueError("aware preparation time required")
        verify_scope(self.engine, self.settings)
        sources = self._originals()
        counts: Counter[str] = Counter()
        for original in sources:
            if self.stop_requested.is_set():
                break
            # Advance the cursor even for held evidence: one poisoned original
            # must not starve another device/batch. C still owns each watermark.
            self._cursor = original
            results = self._observer.advance_page(
                original, now=datetime.now(UTC) if now is None else now, limit=50
            )
            counts["originals_checked"] += 1
            for result in results:
                counts[f"observation_{result.status}"] += 1
            # Read discovery closes before refresh_once takes its A/C locks.
            # Never touch leased/retried work or mark anything delivered here.
            with self.engine.connect() as connection:
                recoveries = list(
                    connection.scalars(
                        select(outbox.c.source_event_id)
                        .where(
                            outbox.c.related_source_event_id == original,
                            outbox.c.body["kind"].as_string()
                            == "temperature_recovered",
                            and_(
                                outbox.c.state == "pending",
                                outbox.c.attempt_count == 0,
                                outbox.c.lease_token.is_(None),
                            ),
                        )
                        .order_by(outbox.c.source_event_id)
                        .limit(14)
                    )
                )
            if recoveries and not self.stop_requested.is_set():
                for status, count in refresh_once(
                    self.engine, self.settings, tuple(recoveries)
                ).items():
                    counts[f"frontier_{status}"] += count
        return dict(counts)

    def run(
        self, *, max_seconds: float | None = None, poll_seconds: float = 0.5
    ) -> dict[str, int]:
        if (
            isinstance(poll_seconds, bool)
            or not math.isfinite(poll_seconds)
            or not 0.1 <= poll_seconds <= 10
        ):
            raise ValueError("poll must be finite and between 0.1 and 10 seconds")
        if max_seconds is not None and (
            isinstance(max_seconds, bool)
            or not math.isfinite(max_seconds)
            or max_seconds <= 0
        ):
            raise ValueError("max seconds must be finite and positive")
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        counts: Counter[str] = Counter()
        while not self.stop_requested.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                break
            try:
                counts.update(self.run_once())
            except SQLAlchemyError, FrontierUnavailable, RuntimeError:
                # No SQL text/configuration/identifiers in background logging.
                counts["retryable_cycle"] += 1
                logger.warning("Local recovery preparation temporarily unavailable")
            wait = poll_seconds
            if deadline is not None:
                wait = min(wait, max(0.0, deadline - time.monotonic()))
            self.stop_requested.wait(wait)
        return dict(counts)
