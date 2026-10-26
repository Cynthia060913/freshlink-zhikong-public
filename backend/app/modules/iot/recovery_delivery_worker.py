"""Explicit recovery scheduler; C verifies and leases before lock-free B I/O."""

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
from app.modules.telemetry.delivery_candidate import (
    CandidateDeliveryPort,
    deliver_claimed_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.recovery_claim_candidate import (
    claim_fresh_recovery_on_connection_candidate,
)

logger = logging.getLogger(__name__)


class RecoveryDeliveryWorker:
    """No implicit activation, schema creation, refreeze or notification send."""

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
        if (
            not isinstance(current, datetime)
            or current.tzinfo is None
            or current.utcoffset() is None
        ):
            raise ValueError("aware recovery time required")
        verify_scope(self.engine, self.settings)
        due = case(
            (outbox.c.state == "leased", outbox.c.lease_until_utc),
            else_=outbox.c.next_attempt_at_utc,
        )
        query = (
            select(
                outbox.c.source_event_id,
                outbox.c.frozen_device_id,
                outbox.c.frozen_warehouse_id,
                outbox.c.frozen_stream_epoch,
            )
            .where(
                outbox.c.frozen_device_id.in_(self.settings.device_ids),
                outbox.c.related_source_event_id.is_not(None),
                outbox.c.body["kind"].as_string() == "temperature_recovered",
                or_(
                    and_(outbox.c.state == "pending", due <= current),
                    and_(outbox.c.state == "leased", due <= current),
                ),
            )
            .order_by(outbox.c.source_event_id)
        )
        with self.engine.connect() as connection:
            tail = (
                query
                if self._cursor is None
                else query.where(outbox.c.source_event_id > self._cursor)
            )
            rows = list(connection.execute(tail.limit(14)).mappings())
            if self._cursor is not None and len(rows) < 14:
                rows.extend(
                    connection.execute(
                        query.where(outbox.c.source_event_id <= self._cursor).limit(
                            14 - len(rows)
                        )
                    ).mappings()
                )
        counts: Counter[str] = Counter()
        for row in rows:
            if self.stop_requested.is_set():
                break
            self._cursor = row["source_event_id"]
            device = row["frozen_device_id"]
            if self.settings.frontier_epochs.get(device) != row[
                "frozen_stream_epoch"
            ] or not isinstance(row["frozen_warehouse_id"], UUID):
                counts["held"] += 1
                continue
            try:
                with self.engine.begin() as connection:
                    result = claim_fresh_recovery_on_connection_candidate(
                        connection,
                        recovery_source_event_id=row["source_event_id"],
                        device_id=device,
                        warehouse_id=row["frozen_warehouse_id"],
                        stream_epoch=row["frozen_stream_epoch"],
                        now=datetime.now(UTC) if now is None else now,
                    )
                # Commit must succeed before the returned claim crosses this boundary.
                if result.claim is None:
                    counts["held"] += 1
                    continue
                outcome = deliver_claimed_candidate(
                    self.engine,
                    claim=result.claim,
                    now=datetime.now(UTC) if now is None else now,
                    port=self.port,
                )
                counts[outcome] += 1
            except ValueError, TypeError, KeyError:
                counts["held"] += 1
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
                logger.warning("Recovery delivery temporarily unavailable")
            self.stop_requested.wait(
                0.5
                if deadline is None
                else min(0.5, max(0.0, deadline - time.monotonic()))
            )
