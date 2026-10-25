"""Explicit, dry-run-by-default entry around B's persistent notification worker.

Private config supplies recipients, never a source literal or command argument.
--send is an operator switch, not a substitute for specific human permission.
No import-time worker, migration, provider request or environment modification.
"""

import argparse
import json
import signal
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, create_engine

from app.core.db import engine as application_engine
from app.modules.alerts.notifications.dispatch import build_transports
from app.modules.alerts.notifications.runner import run_dispatch_worker
from app.modules.alerts.notifications.transport import NotificationKind


class NotificationServiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipients: list[EmailStr] = Field(default_factory=list, max_length=20)
    max_events_per_cycle: int = Field(default=1, ge=1, le=50)
    max_attempts_per_channel: int = Field(default=1, ge=1, le=3)
    poll_seconds: float = Field(default=60, ge=1, le=3600, allow_inf_nan=False)


def read_config(path: Path) -> NotificationServiceSettings:
    if path.stat().st_size > 16384:
        raise ValueError("private config too large")
    return NotificationServiceSettings.model_validate(json.loads(path.read_bytes()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--send", action="store_true")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--max-cycles", type=int, default=1)
    modes.add_argument("--continuous", action="store_true")
    args = parser.parse_args(argv)
    if args.max_cycles < 1:
        parser.error("max-cycles must be positive")
    owned_engine = None
    previous_signals = {}
    stop_requested = threading.Event()
    try:
        config = read_config(args.config)
        transports = build_transports()
        if (
            any(t.kind is NotificationKind.SMTP_EMAIL for t in transports)
            and not config.recipients
        ):
            raise ValueError("SMTP recipients required")
        if args.send and not transports:
            raise ValueError("no configured channel")
        if application_engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL required")
        owned_engine = create_engine(
            application_engine.url,
            pool_pre_ping=True,
            pool_timeout=5,
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=10000 -c lock_timeout=5000",
            },
        )

        @contextmanager
        def factory() -> Iterator[Session]:
            with Session(owned_engine) as session:
                yield session

        def stop(_signum: int, _frame: object) -> None:
            stop_requested.set()

        def wait(seconds: float) -> None:
            stop_requested.wait(seconds)

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_signals[signum] = signal.signal(signum, stop)
        reports = run_dispatch_worker(
            factory,
            transports=transports,
            recipients=tuple(str(value) for value in config.recipients),
            limit=config.max_events_per_cycle,
            max_attempts=config.max_attempts_per_channel,
            poll_interval_s=config.poll_seconds,
            dry_run=not args.send,
            max_cycles=None if args.continuous else args.max_cycles,
            stop=stop_requested.is_set,
            sleep=wait,
        )
        interrupted = any(
            report.interrupted
            or any(detail.dedup_error for _, detail in report.reports)
            for report in reports
        )
        # No recipient, provider URL, token, message body or exception text.
        sys.stdout.write(
            json.dumps(
                {
                    "dry_run": not args.send,
                    "cycles": len(reports),
                    "configured_channels": [t.kind.value for t in transports],
                    "pending_count": sum(len(report.pending) for report in reports),
                    "interrupted": interrupted,
                    "terminal_receipt_confirmed": False,
                }
            )
            + "\n"
        )
        return 1 if interrupted else 0
    except ValueError, OSError, SQLAlchemyError:
        sys.stderr.write(
            "Notification service refused or unavailable; check private configuration and database.\n"
        )
        return 2
    finally:
        for signum, handler in previous_signals.items():
            signal.signal(signum, handler)
        if owned_engine is not None:
            owned_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
