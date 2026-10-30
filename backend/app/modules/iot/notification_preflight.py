"""Read-only, point-in-time E1 notification inventory; never a send permit.

No transport.send, worker, lease, migration or provider request is invoked.
An operator must still approve the exact send and prevent inventory changes.
"""

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, create_engine, func, select

from app.modules.alerts.models.event import AlertEvent, NotificationAttempt
from app.modules.alerts.notifications.dispatch import build_transports
from app.modules.alerts.notifications.runner import pending_alert_event_ids
from app.modules.alerts.notifications.transport import NotificationKind
from app.modules.iot.notification_service import read_config

_CHANNELS = tuple(kind.value for kind in NotificationKind)


def inspect_inventory(
    engine: Engine, *, source_event_id: str, channels: Sequence[str]
) -> dict[str, object]:
    """Inspect one fresh-database event using a real PG read-only snapshot."""
    if engine.dialect.name != "postgresql":
        raise ValueError("PostgreSQL required")
    if not 1 <= len(source_event_id) <= 128 or any(
        character.isspace() or ord(character) < 32 for character in source_event_id
    ):
        raise ValueError("Invalid source identity")
    if sorted(channels) != sorted(_CHANNELS):
        raise ValueError("Exactly two distinct configured channels required")
    readonly = engine.execution_options(
        isolation_level="REPEATABLE READ", postgresql_readonly=True
    )
    with (
        readonly.connect() as connection,
        connection.begin(),
        Session(connection) as session,
    ):
        if connection.scalar(text("SHOW transaction_read_only")) != "on":
            raise ValueError("Read-only transaction required")

        @contextmanager
        def factory() -> Iterator[Session]:
            yield session

        total = session.exec(select(func.count()).select_from(AlertEvent)).one()
        attempts = session.exec(
            select(func.count()).select_from(NotificationAttempt)
        ).one()
        event = session.exec(
            select(AlertEvent).where(col(AlertEvent.source_event_id) == source_event_id)
        ).first()
        # The same bounded scanner as the worker, without acquiring a send lease.
        pending = pending_alert_event_ids(factory, channels=channels, limit=2)
        reasons = []
        if total != 1:
            reasons.append("requires_fresh_single_event_database")
        if attempts:
            reasons.append("existing_attempts_require_review")
        if event is None:
            reasons.append("expected_source_not_found")
        elif pending != [event.id]:
            reasons.append("pending_source_mismatch")
        return {
            "status": "readiness_only" if not reasons else "blocked",
            "reasons": reasons,
            "configured_channels": sorted(channels),
            "event_count": total,
            "attempt_count": attempts,
            "pending_count_capped_at_two": len(pending),
            "expected_source_present": event is not None,
            "read_only_transaction": True,
            "send_authorized": False,
            "terminal_receipt_confirmed": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--source-event-id", required=True)
    args = parser.parse_args(argv)
    owned = None
    try:
        config = read_config(args.config)
        if (
            len(config.recipients) != 1
            or config.max_events_per_cycle != 1
            or config.max_attempts_per_channel != 1
        ):
            raise ValueError("Single recipient/event/attempt E1 profile required")
        transports = build_transports()
        channels = [transport.kind.value for transport in transports]
        if sorted(channels) != sorted(_CHANNELS):
            raise ValueError("Both configured channels required")
        from app.core.db import engine

        if engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL required")
        owned = create_engine(
            engine.url,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=10000 -c lock_timeout=5000",
            },
        )
        report = inspect_inventory(
            owned, source_event_id=args.source_event_id, channels=channels
        )
        sys.stdout.write(json.dumps(report) + "\n")
        return 0 if report["status"] == "readiness_only" else 1
    except ValueError, OSError, SQLAlchemyError:
        sys.stderr.write("Notification preflight refused or unavailable.\n")
        return 2
    finally:
        if owned is not None:
            owned.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
