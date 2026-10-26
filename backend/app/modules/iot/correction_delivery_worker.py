"""Explicit A scheduler for C's guarded linked-correction delivery port.

No startup hook or provider transport. Discovery closes before C takes its
durable lease; only the exact internal correction kind reaches this sender.
"""

import logging
import math
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, case, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.modules.iot.stream_frontier import FrontierUnavailable
from app.modules.iot.telemetry_service import TelemetryServiceSettings, verify_scope
from app.modules.telemetry.correction_delivery_candidate import (
    deliver_correction_once_candidate,
)
from app.modules.telemetry.delivery_candidate import CandidateDeliveryPort
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.storage_candidate import observations

logger = logging.getLogger(__name__)


class CorrectionDeliveryWorker:
    def __init__(
        self,
        engine: Engine,
        settings: TelemetryServiceSettings,
        port: CandidateDeliveryPort,
        *,
        contract_confirmed: bool = False,
    ) -> None:
        if not isinstance(contract_confirmed, bool):
            raise ValueError("contract confirmation must be explicit boolean")
        self.engine, self.settings, self.port = engine, settings, port
        self.contract_confirmed = contract_confirmed
        self.stop_requested = threading.Event()
        self._cursor: UUID | None = None

    def run_once(self, *, now: datetime | None = None) -> dict[str, int]:
        if not self.contract_confirmed:
            return {}
        current = datetime.now(UTC) if now is None else now
        if not isinstance(current, datetime) or current.tzinfo is None:
            raise ValueError("aware correction delivery time required")
        verify_scope(self.engine, self.settings)
        due_at = case(
            (outbox.c.state == "leased", outbox.c.lease_until_utc),
            else_=outbox.c.next_attempt_at_utc,
        )
        query = (
            select(outbox.c.source_event_id)
            .join(observations, observations.c.id == outbox.c.observation_id)
            .where(
                observations.c.device_id.in_(self.settings.device_ids),
                outbox.c.related_source_event_id.is_not(None),
                outbox.c.body["schema_version"].as_string()
                == "c-data-quality-correction/v0",
                outbox.c.body["kind"].as_string() == "data_quality",
                or_(
                    and_(
                        outbox.c.state.in_(("held_contract", "pending")),
                        due_at <= current,
                    ),
                    and_(outbox.c.state == "leased", due_at <= current),
                ),
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
        counts: Counter[str] = Counter()
        # No discovery connection or A device lock crosses this boundary.
        for source in sources:
            if self.stop_requested.is_set():
                break
            self._cursor = source
            try:
                outcome: str = deliver_correction_once_candidate(
                    self.engine,
                    source_event_id=source,
                    now=datetime.now(UTC) if now is None else now,
                    port=self.port,
                    contract_confirmed=True,
                )
            except ValueError, TypeError, KeyError:
                # C rejects corrupted evidence before any POST. Preserve it;
                # one held source must not starve another device or batch.
                outcome = "held"
            counts[outcome] += 1
        return dict(counts)

    def run(self, *, max_seconds: float | None = None) -> None:
        if max_seconds is not None and (
            isinstance(max_seconds, bool)
            or not math.isfinite(max_seconds)
            or max_seconds <= 0
        ):
            raise ValueError("max seconds must be finite and positive")
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        while not self.stop_requested.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                return
            try:
                self.run_once()
            except SQLAlchemyError, FrontierUnavailable, RuntimeError:
                logger.warning("Linked correction delivery temporarily unavailable")
            wait = (
                0.5
                if deadline is None
                else min(0.5, max(0.0, deadline - time.monotonic()))
            )
            self.stop_requested.wait(wait)
