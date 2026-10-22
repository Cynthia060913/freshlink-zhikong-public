"""A's explicitly handed-off B lifecycle regression package.

Real PG in owned random schemas; no original DB, notifications or host tools.
Clock/storage faults are injected around real SQL, never fake successful writes.
"""

import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel

from app.api.deps import get_db
from app.api.main import api_router
from app.core.config import settings
from app.core.security import create_access_token
from app.models import User
from app.modules.alerts.models import (
    AlertEvent,
    AlertStatus,
    AlertStatusTransition,
    Warehouse,
)
from app.modules.alerts.services import events
from tests.api.routes import test_telemetry_inbox as inbox_checks
from tests.modules.alerts.test_pr10_boundary_regressions import _rc2

spec = importlib.util.spec_from_file_location(
    "a_lifecycle_pg_probe",
    Path(__file__).resolve().parents[2] / "scripts/probe-alert-lifecycle-races.py",
)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.fixture
def world():
    if os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") != "1":
        pytest.skip("requires controlled disposable PG entry")
    url = make_url(str(settings.DATABASE_URL))
    local = (
        url.host == "test-db"
        and url.database == "freshlink_test"
        and settings.PROJECT_NAME == "FreshLink-test"
    )
    hosted = (
        os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("CI") == "true"
        and url.host in {"localhost", "127.0.0.1", "db"}
        and url.database == "app"
        and settings.FASTAPI_ENV == "development"
    )
    if not (local or hosted):
        raise AssertionError("requires owned disposable database or hosted CI")
    with contextmanager(inbox_checks.store.__wrapped__)() as store:
        tables = [
            table
            for table in SQLModel.metadata.sorted_tables
            if table.name.startswith("alerts_")
        ]
        SQLModel.metadata.create_all(store.engine, tables=tables)
        try:
            with Session(store.engine) as session:
                warehouse = Warehouse(code="race", name="race")
                session.add(warehouse)
                session.commit()
                from app.modules.alerts.models import Batch

                batch = Batch(warehouse_id=warehouse.id, code="race", product="apple")
                session.add(batch)
                session.commit()
                original = _rc2(
                    source_event_id=str(uuid4()),
                    warehouse_id=warehouse.id,
                    batch_id=batch.id,
                )
                receipt = events.receive_candidate(session, original)
                assert receipt.status == "accepted_new"
            recovery = original.model_copy(
                update={
                    "source_event_id": str(uuid4()),
                    "kind": original.kind.__class__.TEMPERATURE_RECOVERED,
                    "value": 7.0,
                    "related_source_event_id": original.source_event_id,
                }
            )
            yield store, receipt.event_id, recovery
        finally:
            SQLModel.metadata.drop_all(store.engine, tables=tables)


def histories(store, alert_id):
    with Session(store.engine) as session:
        return [
            (str(row.from_status), str(row.to_status))
            for row in events.list_status_transitions(session, alert_id)
        ]


def counts(store):
    with store.engine.connect() as connection:
        return (
            len(connection.execute(select(AlertEvent)).all()),
            len(connection.execute(select(AlertStatusTransition)).all()),
        )


@pytest.mark.parametrize(
    "first,second", [("ack", "ack"), ("ack", "recovery"), ("recovery", "ack")]
)
def test_pg_interleavings_actually_wait_on_same_original_row(world, first, second):
    store, _, _ = world
    result = probe.run_case(store.engine, first, second)
    assert result["safe"] and result["second_waited_on_pg_lock"]


@pytest.mark.parametrize("mode", ["identical", "changed", "different_id"])
def test_concurrent_recovery_replay_and_conflict_are_atomic(world, mode):
    store, alert_id, recovery = world
    other = recovery
    if mode == "changed":
        other = recovery.model_copy(update={"evidence": {"threshold": 9}})
    if mode == "different_id":
        other = recovery.model_copy(update={"source_event_id": str(uuid4())})
    gate = Barrier(2)

    def receive(payload):
        with Session(store.engine) as session:
            gate.wait(timeout=5)
            return events.receive_candidate(session, payload).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(receive, payload) for payload in (recovery, other)]
        results = sorted(future.result(timeout=8) for future in futures)
    expected = {
        "identical": ["accepted_duplicate", "accepted_new"],
        "changed": ["accepted_new", "conflict"],
        "different_id": ["accepted_new", "accepted_new"],
    }
    assert results == expected[mode]
    assert histories(store, alert_id) == [("open", "resolved")]
    assert counts(store) == (3 if mode == "different_id" else 2, 1)


@pytest.mark.parametrize("action", ["ack", "recovery"])
def test_lock_timeout_is_retryable_rolls_back_and_same_operation_can_retry(
    world, action
):
    store, alert_id, recovery = world

    def contender():
        with Session(store.engine) as session:
            if action == "ack":
                with pytest.raises(events.AlertLifecycleUnavailable):
                    events.acknowledge_alert(session, alert_id)
                assert not session.in_transaction()
            else:
                assert (
                    events.receive_candidate(session, recovery).status
                    == "retryable_failure"
                )
                assert not session.in_transaction()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(store.engine) as blocker:
            assert events._lock_alert_event(blocker, alert_id) is not None
            future = pool.submit(contender)
            future.result(timeout=7)
            blocker.rollback()
    assert counts(store) == (1, 0)
    with Session(store.engine) as session:
        if action == "ack":
            assert events.acknowledge_alert(session, alert_id) == (
                AlertStatus.ACKNOWLEDGED,
                True,
            )
        else:
            assert events.receive_candidate(session, recovery).status == "accepted_new"
    assert counts(store) == (1 if action == "ack" else 2, 1)


def test_recovery_insert_failure_rolls_back_event_and_transition_and_releases_lock(
    world,
):
    store, alert_id, recovery = world

    def fail(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            statement.lstrip().startswith("INSERT INTO")
            and "alerts_status_transition" in statement
        ):
            raise OperationalError(
                "synthetic transition outage", {}, RuntimeError("private fault")
            )

    event.listen(store.engine, "before_cursor_execute", fail)
    try:
        with Session(store.engine) as session:
            assert (
                events.receive_candidate(session, recovery).status
                == "retryable_failure"
            )
            assert not session.in_transaction()
    finally:
        event.remove(store.engine, "before_cursor_execute", fail)
    assert counts(store) == (1, 0)
    with Session(store.engine) as session:
        assert events.receive_candidate(session, recovery).status == "accepted_new"
    assert histories(store, alert_id) == [("open", "resolved")]


def test_lost_ack_commit_response_retries_without_second_transition(world):
    store, alert_id, _ = world

    class LostResponseSession(Session):
        def commit(self):
            super().commit()  # Actual PG commit, then lost response to caller.
            raise OperationalError("lost commit response", {}, RuntimeError("offline"))

    with LostResponseSession(store.engine) as session:
        with pytest.raises(events.AlertLifecycleUnavailable):
            events.acknowledge_alert(session, alert_id)
    with Session(store.engine) as session:
        assert events.acknowledge_alert(session, alert_id) == (
            AlertStatus.ACKNOWLEDGED,
            False,
        )
    assert counts(store) == (1, 1)


def test_future_legacy_timestamp_or_clock_rollback_cannot_reorder_new_resolution(world):
    store, alert_id, recovery = world
    future_time = datetime(2099, 1, 1, tzinfo=UTC)
    with Session(store.engine) as session:
        session.add(
            AlertStatusTransition(
                alert_event_id=alert_id,
                from_status=AlertStatus.OPEN,
                to_status=AlertStatus.ACKNOWLEDGED,
                created_at=future_time,
            )
        )
        session.commit()
        assert events.receive_candidate(session, recovery).status == "accepted_new"
        assert events.get_alert_status(session, alert_id) == AlertStatus.RESOLVED
        rows = events.list_status_transitions(session, alert_id)
        assert rows[-1].created_at == future_time + timedelta(microseconds=1)
        assert (
            events.statuses_for_events(session, [alert_id])[alert_id]
            == AlertStatus.RESOLVED
        )


def test_pending_writes_and_explicit_caller_transaction_are_not_committed_or_rolled_back(
    world,
):
    store, alert_id, recovery = world
    with Session(store.engine) as session:
        pending = Warehouse(code="unrelated", name="pending")
        session.add(pending)
        with pytest.raises(events.AlertLifecycleUnavailable):
            events.acknowledge_alert(session, alert_id)
        assert pending in session.new
        assert events.receive_candidate(session, recovery).status == "retryable_failure"
        assert pending in session.new
        session.rollback()
    with Session(store.engine) as session:
        with session.begin():
            with pytest.raises(events.AlertLifecycleUnavailable):
                events.acknowledge_alert(session, alert_id)
            assert session.in_transaction()
    with store.engine.begin() as connection:
        with Session(connection) as session:
            with pytest.raises(events.AlertLifecycleUnavailable):
                events.acknowledge_alert(session, alert_id)
            assert connection.in_transaction()
    assert counts(store) == (1, 0)


def test_real_root_jwt_permissions_and_wire_503_retry_after(world):
    store, alert_id, recovery = world
    with Session(store.engine) as session:
        admin = session.get(User, store.actor_id)
        normal = User(email="race-reader@example.test", hashed_password="fixture-only")
        session.add(normal)
        session.commit()
        session.refresh(normal)
        admin_headers = {
            "Authorization": "Bearer "
            + create_access_token(str(admin.id), timedelta(minutes=5))
        }
        reader_headers = {
            "Authorization": "Bearer "
            + create_access_token(str(normal.id), timedelta(minutes=5))
        }
    app = FastAPI()
    app.include_router(api_router, prefix=settings.API_V1_STR)

    def private_db():
        with Session(store.engine) as session:
            yield session

    app.dependency_overrides[get_db] = private_db
    path = f"{settings.API_V1_STR}/alerts/{alert_id}/acknowledge"

    def fail(_connection, _cursor, statement, _parameters, _context, _many):
        if "FOR UPDATE" in statement and "alerts_event" in statement:
            raise OperationalError(
                "synthetic lock failure", {}, RuntimeError("private fault")
            )

    with TestClient(app) as client:
        assert client.post(path).status_code == 401
        assert client.post(path, headers=reader_headers).status_code == 403
        event.listen(store.engine, "before_cursor_execute", fail)
        try:
            for response in (
                client.post(path, headers=admin_headers),
                client.post(
                    f"{settings.API_V1_STR}/candidates",
                    headers=admin_headers,
                    json=recovery.model_dump(mode="json"),
                ),
            ):
                assert response.status_code == 503
                assert response.headers["retry-after"] == "30"
                assert "private fault" not in response.text
        finally:
            event.remove(store.engine, "before_cursor_execute", fail)
        assert counts(store) == (1, 0)
        assert client.post(path, headers=admin_headers).status_code == 200
    assert counts(store) == (1, 1)
