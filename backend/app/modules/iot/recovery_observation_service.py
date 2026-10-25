"""Explicit, bounded preparation of saved recovery evidence; NEVER delivery.

Use the reviewed telemetry scope and explicit original high-event UUIDs. Each
invocation advances at most one page per original, on an already migrated and
initialized database. A staged/frozen local row is not a B recovery acceptance,
an exception resolution, a notification or a terminal receipt.
"""

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.engine import Engine
from sqlmodel import create_engine

from app.core.db import engine as application_engine
from app.modules.iot.recovery_observation_worker import GatedRecoveryObservationWorker
from app.modules.iot.telemetry_service import TelemetryServiceSettings, verify_scope


def prepare_once(
    engine: Engine,
    settings: TelemetryServiceSettings,
    originals: tuple[UUID, ...],
    *,
    now: datetime,
    limit: int = 50,
) -> dict[str, int]:
    """One bounded call; saved C progress permits later invocations to resume."""
    if (
        not 1 <= len(originals) <= 14
        or len(set(originals)) != len(originals)
        or any(not isinstance(identity, UUID) for identity in originals)
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 100
        or not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise ValueError(
            "explicit unique originals, bounded page and aware time required"
        )
    # Read-only scope check; never create tables, repair epochs or alter bindings.
    verify_scope(engine, settings)
    worker = GatedRecoveryObservationWorker(
        engine, frontier_epochs=settings.frontier_epochs
    )
    counts: Counter[str] = Counter()
    for original in originals:
        for result in worker.advance_page(original, now=now, limit=limit):
            counts[result.status] += 1
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--original-source-event-id", action="append", required=True)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    owned: Engine | None = None
    try:
        if args.config.stat().st_size > 65536:
            raise ValueError("config too large")
        settings = TelemetryServiceSettings.model_validate_json(
            args.config.read_bytes()
        )
        originals = tuple(UUID(value) for value in args.original_source_event_id)
        owned = create_engine(
            application_engine.url,
            pool_pre_ping=True,
            pool_timeout=5,
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=10000 -c lock_timeout=5000",
            },
        )
        counts = prepare_once(
            owned, settings, originals, now=datetime.now(UTC), limit=args.limit
        )
        print(  # noqa: T201 - explicit operator CLI, no secrets or raw evidence
            json.dumps(
                {
                    "scope": "local_recovery_evidence_only_not_B_delivery",
                    "originals_requested": len(originals),
                    "results": counts,
                },
                sort_keys=True,
            )
        )
        if counts.get("held", 0) or counts.get("retryable_failure", 0):
            raise SystemExit(2)
    except Exception:
        # Pydantic/SQL/config errors can contain private paths or credentials.
        raise SystemExit("recovery preparation failed; check private scope") from None
    finally:
        if owned is not None:
            owned.dispose()


if __name__ == "__main__":
    main()
