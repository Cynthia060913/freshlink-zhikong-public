"""Explicit disposable-PG probe; never part of application startup.

Pauses after a REAL status read; no state, result, SQL or commit is mocked.
Both old unlocked and future locked services can finish: release the first
actor after the second commits OR PostgreSQL proves its lock wait.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlmodel import Session, SQLModel

from app.core.config import settings
from app.modules.alerts.models import (
    AlertEvent,
    AlertStatus,
    AlertStatusTransition,
    Batch,
    CandidateCreate,
    Warehouse,
)
from app.modules.alerts.services import events


def run_case(base_engine, first_action: str, second_action: str) -> dict:
    schema = "a_lifecycle_probe_" + uuid4().hex
    tables = [
        table
        for table in SQLModel.metadata.sorted_tables
        if table.name.startswith("alerts_")
    ]
    if len(tables) != 6:
        raise AssertionError("expected exactly six B candidate tables")
    engine = base_engine.execution_options(schema_translate_map={None: schema})
    created = False
    first_read, release_first, second_done = Event(), Event(), Event()
    second_pid: list[int] = []
    pool = ThreadPoolExecutor(max_workers=2)
    actual_status = events.get_alert_status
    try:
        with base_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        SQLModel.metadata.create_all(engine, tables=tables)
        with Session(engine) as session:
            warehouse = Warehouse(code="probe", name="isolated probe")
            session.add(warehouse)
            session.commit()
            batch = Batch(warehouse_id=warehouse.id, code="probe", product="apple")
            session.add(batch)
            session.commit()
            body = {
                "schema_version": "1.0",
                "source_event_id": str(uuid4()),
                "warehouse_id": warehouse.id,
                "batch_id": batch.id,
                "observation_ids": [str(uuid4())],
                "metric_code": "temperature",
                "sampled_at": datetime(2026, 10, 21, tzinfo=UTC),
                "value": 11.0,
                "unit": "°C",
                "kind": "temperature_high",
                "severity": "warning",
                "rule_snapshot_id": uuid4(),
                "rule_version": "probe-v1",
                "algorithm_version": "threshold-rule/probe",
                "evidence": {"comparison": ">", "threshold": 8},
            }
            receipt = events.receive_candidate(session, CandidateCreate(**body))
            if receipt.status != "accepted_new" or receipt.event_id is None:
                raise AssertionError("failed to persist real anomaly")
            alert_id = receipt.event_id
        recovery = CandidateCreate(
            **{
                **body,
                "source_event_id": str(uuid4()),
                "kind": "temperature_recovered",
                "value": 7.0,
                "related_source_event_id": body["source_event_id"],
            }
        )

        def paused_real_read(session, event_id):
            result = actual_status(session, event_id)
            if session.info.get("probe_actor") == "first":
                first_read.set()
                if not release_first.wait(10):
                    raise TimeoutError("first actor was not released")
            return result

        def actor(role: str, action: str) -> dict:
            try:
                with Session(engine) as session:
                    session.info["probe_actor"] = role
                    session.execute(text("SET LOCAL lock_timeout = '7s'"))
                    session.execute(text("SET LOCAL statement_timeout = '8s'"))
                    pid = session.execute(text("SELECT pg_backend_pid()")).scalar_one()
                    if role == "second":
                        second_pid.append(pid)
                    if action == "ack":
                        try:
                            status, was_created = events.acknowledge_alert(
                                session, alert_id
                            )
                            return {"status": str(status), "created": was_created}
                        except events.AlertAlreadyResolved:
                            session.rollback()
                            return {"status": "already_resolved", "created": False}
                    result = events.receive_candidate(session, recovery)
                    return {"status": str(result.status)}
            finally:
                if role == "second":
                    second_done.set()

        second_waited_on_pg_lock = False
        with patch.object(events, "get_alert_status", side_effect=paused_real_read):
            first = pool.submit(actor, "first", first_action)
            if not first_read.wait(8):
                first.result(timeout=1)
                raise AssertionError("first actor never reached real status read")
            second = pool.submit(actor, "second", second_action)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if second_done.is_set():
                    break
                if second_pid:
                    with base_engine.connect() as monitor:
                        wait = monitor.execute(
                            text(
                                "SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"
                            ),
                            {"pid": second_pid[0]},
                        ).scalar_one_or_none()
                    if wait == "Lock":
                        second_waited_on_pg_lock = True
                        break
                time.sleep(0.02)
            else:
                raise TimeoutError("second neither finished nor reached a PG lock wait")
            release_first.set()
            first_result = first.result(timeout=10)
            second_result = second.result(timeout=10)
        pool.shutdown(wait=True)
        with Session(engine) as session:
            final_status = actual_status(session, alert_id)
            history = events.list_status_transitions(session, alert_id)
            states = [(str(row.from_status), str(row.to_status)) for row in history]
            event_count = session.execute(
                select(func.count()).select_from(AlertEvent)
            ).scalar_one()
            count = session.execute(
                select(func.count()).select_from(AlertStatusTransition)
            ).scalar_one()
        if first_action == second_action == "ack":
            safe = states == [("open", "acknowledged")] and sorted(
                [first_result["created"], second_result["created"]]
            ) == [False, True]
        elif first_action == "ack":
            safe = (
                states == [("open", "acknowledged"), ("acknowledged", "resolved")]
                and final_status == AlertStatus.RESOLVED
            )
        else:
            safe = (
                states == [("open", "resolved")]
                and second_result["status"] == "already_resolved"
                and final_status == AlertStatus.RESOLVED
            )
        return {
            "first": first_action,
            "second": second_action,
            "first_result": first_result,
            "second_result": second_result,
            "second_waited_on_pg_lock": second_waited_on_pg_lock,
            "final_status": str(final_status),
            "history": states,
            "event_count": event_count,
            "transition_count": count,
            "safe": safe,
        }
    finally:
        release_first.set()
        pool.shutdown(wait=True, cancel_futures=True)
        if created:
            SQLModel.metadata.drop_all(engine, tables=tables)
            with base_engine.begin() as connection:
                connection.execute(DropSchema(schema))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    path = Path(args.report)
    if path.parent != Path("/results") or path.exists():
        raise ValueError("report must be a fresh file directly under /results")
    url = make_url(str(settings.DATABASE_URL))
    if (
        settings.PROJECT_NAME != "FreshLink-test"
        or url.get_backend_name() != "postgresql"
        or url.host != "test-db"
        or url.database != "freshlink_test"
    ):
        raise ValueError("requires exact disposable test-db/freshlink_test project")
    engine = create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version()")).scalar_one()
            isolation = connection.get_isolation_level()
        if isolation != "READ COMMITTED":
            raise AssertionError("probe requires explicit READ COMMITTED behavior")
        results = [
            run_case(engine, first, second)
            for first, second in (
                ("ack", "ack"),
                ("ack", "recovery"),
                ("recovery", "ack"),
            )
        ]
        with engine.connect() as connection:
            leftovers = (
                connection.execute(
                    text(
                        "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'a_lifecycle_probe_%'"
                    )
                )
                .scalars()
                .all()
            )
        report = {
            "postgres": version,
            "isolation": isolation,
            "cases": results,
            "leftover_probe_schemas": leftovers,
            "safe": all(case["safe"] for case in results) and not leftovers,
            "limits": [
                "synthetic anomaly/recovery; actual B service and PG writes",
                "candidate test DDL only, no original app database or notifications",
                "only pauses after actual status reads; no mocked outcomes",
            ],
        }
        with path.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2)
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
        return 0 if report["safe"] else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
