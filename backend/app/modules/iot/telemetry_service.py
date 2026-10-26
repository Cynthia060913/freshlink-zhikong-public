"""Explicit telemetry and high-alarm process entries, reusing reviewed ports.

No import-time start, DDL, epoch seeding, recovery or notification delivery.
Run each role separately so a broker callback never performs C business work.
The application operator owns the reviewed migration, broker ACL and scope.
"""

import argparse
import logging
import math
import os
import signal
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col, create_engine

from app.core.db import engine as application_engine
from app.modules.iot.heartbeat_worker import HeartbeatWorkerSettings
from app.modules.iot.high_delivery_worker import HighDeliveryWorker
from app.modules.iot.high_evaluation_worker import GatedHighWorker
from app.modules.iot.models import Device
from app.modules.iot.stream_frontier import FrontierUnavailable
from app.modules.iot.stream_frontier_models import streams
from app.modules.iot.telemetry_storage_bridge import GatedTelemetrySink
from app.modules.iot.telemetry_worker import TelemetryDeliveryWorker, TelemetryReceiver
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session
from app.modules.telemetry.b_http_candidate import (
    BHttpCandidatePort,
    BHttpLinkedCorrectionPort,
    UrllibCandidateTransport,
)

logger = logging.getLogger(__name__)
Role = Literal["receive", "store", "evaluate", "deliver-high"]


class TelemetryServiceSettings(HeartbeatWorkerSettings):
    frontier_epochs: dict[UUID, UUID] = Field(min_length=1, max_length=14)

    @model_validator(mode="after")
    def exact_epoch_scope(self) -> TelemetryServiceSettings:
        if set(self.frontier_epochs) != set(self.device_ids):
            raise ValueError("device scope and explicit epoch scope must match")
        return self


def verify_scope(engine: Engine, settings: TelemetryServiceSettings) -> None:
    """Read only: never invent a verified origin or overwrite an old epoch."""
    if engine.dialect.name != "postgresql":
        raise ValueError("telemetry service requires PostgreSQL")
    with engine.connect() as connection:
        rows = (
            connection.execute(
                select(
                    streams.c.device_id,
                    streams.c.stream_epoch,
                    streams.c.origin_verified,
                    streams.c.warehouse_id,
                    col(Device.warehouse_id).label("current_warehouse"),
                    col(Device.kind),
                )
                .join(Device, col(Device.id) == streams.c.device_id)
                .where(streams.c.device_id.in_(settings.device_ids))
            )
            .mappings()
            .all()
        )
    if len(rows) != len(settings.device_ids) or any(
        row["stream_epoch"] != settings.frontier_epochs[row["device_id"]]
        or row["origin_verified"] is not True
        or row["warehouse_id"] != row["current_warehouse"]
        or row["kind"] != "temperature_sensor"
        for row in rows
    ):
        raise FrontierUnavailable("explicit initialized sensor scope required")


def run_evaluation_loop(
    worker: GatedHighWorker,
    stop_requested: threading.Event,
    *,
    max_seconds: float | None = None,
    poll_seconds: float = 0.5,
) -> None:
    """Bounded page per tick; pending evidence stays durable across failures."""
    if not math.isfinite(poll_seconds) or not 0.1 <= poll_seconds <= 10:
        raise ValueError("poll must be finite and between 0.1 and 10 seconds")
    if max_seconds is not None and (not math.isfinite(max_seconds) or max_seconds <= 0):
        raise ValueError("max seconds must be finite and positive")
    deadline = None if max_seconds is None else time.monotonic() + max_seconds
    while not stop_requested.is_set():
        if deadline is not None and time.monotonic() >= deadline:
            return
        try:
            worker.run_once(now=datetime.now(UTC), limit=14)
        except SQLAlchemyError, FrontierUnavailable:
            # Do not print SQL, credentials, payload or exception text.
            logger.warning("Telemetry evaluation temporarily unavailable")
        wait = poll_seconds
        if deadline is not None:
            wait = min(wait, max(0.0, deadline - time.monotonic()))
        stop_requested.wait(wait)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role",
        required=True,
        choices=("receive", "store", "evaluate", "deliver-high", "deliver-correction"),
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--confirm-correction-contract", action="store_true")
    args = parser.parse_args()
    if args.confirm_correction_contract != (args.role == "deliver-correction"):
        parser.error(
            "linked correction requires its own explicit contract confirmation"
        )
    if args.max_seconds is not None and (
        not math.isfinite(args.max_seconds) or args.max_seconds <= 0
    ):
        parser.error("max-seconds must be finite and positive")
    owned_engine: Engine | None = None
    previous_signals = {}
    try:
        if args.config.stat().st_size > 65536:
            raise ValueError("config too large")
        settings = TelemetryServiceSettings.model_validate_json(
            args.config.read_bytes()
        )
        password = os.environ.get("FRESHLINK_TELEMETRY_MQTT_PASSWORD", "")
        if args.role == "receive" and not password:
            raise ValueError("MQTT account owner password required")
        if args.role in {"deliver-high", "deliver-correction"} and not os.environ.get(
            "FRESHLINK_B_SERVICE_BEARER"
        ):
            raise ValueError("B authenticated ingestion credential required")
        owned_engine = create_engine(
            application_engine.url,
            pool_pre_ping=True,
            pool_timeout=5,
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=10000 -c lock_timeout=5000",
            },
        )
        verify_scope(owned_engine, settings)
        if args.role == "receive":
            worker = TelemetryReceiver(
                settings,
                password=password,
                warehouse_lookup_factory=warehouse_lookup_for_session,
                frontier_epochs=settings.frontier_epochs,
            )
            stop_requested = worker.stop_requested
            run = worker.run
        elif args.role == "store":
            delivery_worker = TelemetryDeliveryWorker(
                owned_engine,
                GatedTelemetrySink(
                    owned_engine, frontier_epochs=settings.frontier_epochs
                ),
                frontier_epochs=settings.frontier_epochs,
            )
            stop_requested = delivery_worker.stop_requested
            run = delivery_worker.run
        elif args.role == "deliver-correction":
            from app.modules.iot.correction_delivery_worker import (
                CorrectionDeliveryWorker,
            )

            correction_sender = CorrectionDeliveryWorker(
                owned_engine,
                settings,
                BHttpLinkedCorrectionPort(
                    UrllibCandidateTransport(
                        base_url=os.environ.get(
                            "FRESHLINK_B_INTERNAL_ORIGIN", "http://127.0.0.1:8000"
                        ),
                        bearer=lambda: os.environ.get("FRESHLINK_B_SERVICE_BEARER", ""),
                    )
                ),
                contract_confirmed=True,
            )
            stop_requested = correction_sender.stop_requested
            run = correction_sender.run
        elif args.role == "deliver-high":
            sender = HighDeliveryWorker(
                owned_engine,
                BHttpCandidatePort(
                    UrllibCandidateTransport(
                        base_url=os.environ.get(
                            "FRESHLINK_B_INTERNAL_ORIGIN", "http://127.0.0.1:8000"
                        ),
                        bearer=lambda: os.environ.get("FRESHLINK_B_SERVICE_BEARER", ""),
                    )
                ),
                device_ids=settings.device_ids,
            )
            stop_requested = sender.stop_requested
            run = sender.run
        else:
            high_worker = GatedHighWorker(
                owned_engine, frontier_epochs=settings.frontier_epochs
            )
            stop_requested = threading.Event()

            def run(*, max_seconds: float | None = None) -> None:
                run_evaluation_loop(
                    high_worker, stop_requested, max_seconds=max_seconds
                )

        def stop(_signum: int, _frame: object) -> None:
            stop_requested.set()

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_signals[signum] = signal.signal(signum, stop)
        run(max_seconds=args.max_seconds)
    except Exception:
        # Validation errors may embed config values: do not echo them.
        raise SystemExit(
            "telemetry role failed; check private configuration and scope"
        ) from None
    finally:
        for signum, handler in previous_signals.items():
            signal.signal(signum, handler)
        if owned_engine is not None:
            owned_engine.dispose()


if __name__ == "__main__":
    main()
