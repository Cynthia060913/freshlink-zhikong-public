"""Explicit continuous local A-to-B event delivery; no migration or notifications."""

import argparse
import logging
import math
import signal
import threading
import time
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, create_engine

from app.core.db import engine as app_engine
from app.modules.alerts.models.event import CandidateReceipt, DeviceEventCreate
from app.modules.alerts.services.events import receive_device_event
from app.modules.iot.event_delivery import EventReceiver
from app.modules.iot.event_dispatch import (
    DispatchResult,
    claim_event,
    deliver_event_claim,
)

logger = logging.getLogger(__name__)


class EventWorkerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Literal[True]
    device_ids: list[UUID] = Field(min_length=1, max_length=14)
    poll_seconds: float = Field(default=1, ge=0.1, le=10)

    @model_validator(mode="after")
    def unique_scope(self) -> EventWorkerSettings:
        if len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("duplicate device scope")
        return self


class EventDeliveryWorker:
    def __init__(
        self, engine: Engine, receiver: EventReceiver, config: EventWorkerSettings
    ) -> None:
        # Required explicit receiver: no placeholder success/default notification.
        self.engine, self.receiver, self.config = engine, receiver, config
        self.stop_requested = threading.Event()
        self.completed = 0
        self.database_failures = 0
        self.last_status = "idle"

    def run_once(self) -> DispatchResult | None:
        claim = claim_event(self.engine, tuple(self.config.device_ids))
        if claim is None:
            self.last_status = "idle"
            return None
        result = deliver_event_claim(self.engine, claim, self.receiver)
        self.last_status = result.status
        self.completed += int(result.status == "delivered")
        return result

    def run(self, *, max_seconds: float | None = None) -> None:
        if max_seconds is not None and (
            not math.isfinite(max_seconds) or max_seconds <= 0
        ):
            raise ValueError("max_seconds must be finite and positive")
        start, delay = time.monotonic(), self.config.poll_seconds
        while not self.stop_requested.is_set():
            if max_seconds is not None and time.monotonic() - start >= max_seconds:
                return
            try:
                self.run_once()
                delay = self.config.poll_seconds
            except SQLAlchemyError:
                self.database_failures += 1
                self.last_status = "storage_unavailable"
                logger.warning("Event delivery database temporarily unavailable")
                delay = min(30, max(1, delay * 2))
            except Exception:
                self.stop_requested.set()
                self.last_status = "worker_failed"
                raise RuntimeError("event_dispatch_worker_failed") from None
            remaining = (
                None
                if max_seconds is None
                else max_seconds - (time.monotonic() - start)
            )
            if remaining is not None and remaining <= 0:
                return
            self.stop_requested.wait(
                delay if remaining is None else min(delay, remaining)
            )


def local_receiver(engine: Engine) -> EventReceiver:
    def receive(payload: DeviceEventCreate) -> CandidateReceipt:
        with Session(engine) as session:
            return receive_device_event(session, payload)

    return receive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float)
    args = parser.parse_args()
    if args.config.stat().st_size > 65536:
        raise ValueError("worker config too large")
    config = EventWorkerSettings.model_validate_json(args.config.read_bytes())
    engine = create_engine(
        app_engine.url,
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
    )
    worker = EventDeliveryWorker(engine, local_receiver(engine), config)
    signal.signal(signal.SIGINT, lambda _s, _f: worker.stop_requested.set())
    signal.signal(signal.SIGTERM, lambda _s, _f: worker.stop_requested.set())
    try:
        worker.run(max_seconds=args.max_seconds)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
