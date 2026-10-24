"""Schedule frozen C high alarms through the existing B delivery port.

C's durable claim/digest/retry logic is unchanged. Recovery rows are excluded
before claiming; no current-stamp or real notification permission is implied.
No import-time start, migration or new queue.
"""

import logging
import math
import threading
import time
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, case, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.modules.telemetry.delivery_candidate import (
    CandidateDeliveryPort,
    DeliveryOutcome,
    deliver_once_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.storage_candidate import observations

logger = logging.getLogger(__name__)


class HighDeliveryWorker:
    def __init__(
        self, engine: Engine, port: CandidateDeliveryPort, *, device_ids: list[UUID]
    ) -> None:
        if (
            not 1 <= len(device_ids) <= 14
            or len(set(device_ids)) != len(device_ids)
            or not all(isinstance(item, UUID) for item in device_ids)
        ):
            raise ValueError("1-14 unique explicit device UUIDs required")
        self.engine, self.port = engine, port
        self.device_ids = tuple(device_ids)
        self.stop_requested = threading.Event()

    def run_once(self, *, now: datetime) -> list[tuple[UUID, DeliveryOutcome]]:
        if now.tzinfo is None:
            raise ValueError("aware delivery time required")
        due_at = case(
            (outbox.c.state == "leased", outbox.c.lease_until_utc),
            else_=outbox.c.next_attempt_at_utc,
        )
        with self.engine.connect() as connection:
            sources = list(
                connection.scalars(
                    select(outbox.c.source_event_id)
                    .join(observations, observations.c.id == outbox.c.observation_id)
                    .where(
                        observations.c.device_id.in_(self.device_ids),
                        outbox.c.related_source_event_id.is_(None),
                        outbox.c.body["kind"].as_string() == "temperature_high",
                        or_(
                            and_(outbox.c.state == "pending", due_at <= now),
                            and_(outbox.c.state == "leased", due_at <= now),
                        ),
                    )
                    .order_by(due_at, outbox.c.source_event_id)
                    .limit(14)
                )
            )
        # Discovery transaction is CLOSED before C's CAS claim or any B HTTP.
        # Racing workers still arbitrate through C's original lease protocol.
        return [
            (
                source,
                deliver_once_candidate(
                    self.engine, source_event_id=source, now=now, port=self.port
                ),
            )
            for source in sources
            if not self.stop_requested.is_set()
        ]

    def run(self, *, max_seconds: float | None = None) -> None:
        if max_seconds is not None and (
            not math.isfinite(max_seconds) or max_seconds <= 0
        ):
            raise ValueError("max seconds must be finite and positive")
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        while not self.stop_requested.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                return
            try:
                self.run_once(now=datetime.now(UTC))
            except SQLAlchemyError:
                logger.warning("High alarm delivery storage temporarily unavailable")
            wait = (
                0.5
                if deadline is None
                else min(0.5, max(0.0, deadline - time.monotonic()))
            )
            self.stop_requested.wait(wait)
