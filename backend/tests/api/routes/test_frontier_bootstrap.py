"""Explicit fresh-origin candidate on strict random private PG schemas only."""

import importlib.util
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Table, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, col

from app.core.config import settings
from app.models import User
from app.modules.alerts.models import Batch, Warehouse
from app.modules.alerts.services.events import receive_candidate
from app.modules.iot.frontier_bootstrap import initialize_fresh_streams
from app.modules.iot.models import Device
from app.modules.iot.stream_frontier import (
    FrontierUnavailable,
    mark_uncertain,
    read_complete_frontier,
)
from app.modules.iot.stream_frontier_models import streams
from app.modules.iot.telemetry_inbox import enqueue
from app.modules.telemetry import (  # noqa: F401 - registers C fixture tables
    outbox_candidate,
    snapshot_candidate,
)
from app.modules.telemetry.recovery_progress_candidate import (
    metadata as progress_metadata,
)
from app.modules.telemetry.storage_candidate import (
    ingest_observation,
)
from app.modules.telemetry.storage_candidate import (
    metadata as c_metadata,
)
from tests.api.routes import test_telemetry_inbox as inbox_checks
from tests.modules.alerts.test_pr10_boundary_regressions import _rc2

SPEC = importlib.util.spec_from_file_location(
    "a05_bootstrap_fixture",
    Path(__file__).resolve().parents[3] / "app/alembic/versions/a05_stream_frontier.py",
)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


@pytest.fixture
def world():
    if os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") != "1":
        pytest.skip("requires strict owned PG bootstrap entry")
    url = make_url(str(settings.DATABASE_URL))
    local = (
        url.host == "test-db"
        and url.database == "freshlink_test"
        and settings.PROJECT_NAME == "FreshLink-test"
    )
    hosted = (
        os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("CI") == "true"
        and url.host in {"db", "localhost", "127.0.0.1"}
        and url.database == "app"
    )
    if not (local or hosted) or settings.FASTAPI_ENV != "development":
        raise AssertionError("refusing non-owned bootstrap database")
    with contextmanager(inbox_checks.store.__wrapped__)() as store:
        b_tables = [
            table
            for table in SQLModel.metadata.sorted_tables
            if table.name.startswith("alerts_")
        ]
        SQLModel.metadata.create_all(store.engine, tables=b_tables)
        with store.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            connection.execute(
                Warehouse.__table__.insert().values(
                    id=store.warehouse_id,
                    code="bootstrap",
                    name="bootstrap",
                    created_at=inbox_checks.T0,
                )
            )
        c_metadata.create_all(store.engine)
        progress_metadata.create_all(store.engine)
        try:
            names = set(inspect(store.engine).get_table_names(schema=store.schema))
            assert {"iot_stream_frontier", "iot_stream_admission"} <= names
            yield store, {store.device_id: uuid4()}
        finally:
            progress_metadata.drop_all(store.engine)
            c_metadata.drop_all(store.engine)
            with store.engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            SQLModel.metadata.drop_all(store.engine, tables=b_tables)


def initialize(store, epochs, connection=None, **changes):
    arguments = {
        "actor_id": store.actor_id,
        "bindings": {store.device_id: store.warehouse_id},
        "epochs": epochs,
        **changes,
    }
    if connection is not None:
        return initialize_fresh_streams(connection, **arguments)
    with store.engine.begin() as own:
        return initialize_fresh_streams(own, **arguments)


def saved(store):
    with store.engine.connect() as connection:
        return connection.execute(select(streams)).mappings().all()


def test_fresh_origin_joins_outer_rollback_and_zero_samples_are_not_complete(world):
    store, epochs = world
    with store.engine.connect() as connection:
        outer = connection.begin()
        assert initialize(store, epochs, connection).created
        assert (
            read_complete_frontier(
                connection,
                device_id=store.device_id,
                warehouse_id=store.warehouse_id,
                epoch=epochs[store.device_id],
            )
            is None
        )
        outer.rollback()
    assert saved(store) == []
    result = initialize(store, epochs)
    assert result.created and dict(result.epochs) == epochs
    row = saved(store)[0]
    assert row["origin_verified"] and row["admitted_seq"] == row["settled_through"] == 0


@pytest.mark.parametrize("evidence", ["raw", "c_observation", "b_event"])
def test_prior_real_evidence_refuses_fresh_origin_without_deleting_it(world, evidence):
    store, epochs = world
    envelope = inbox_checks.frame(store)
    if evidence == "raw":
        assert enqueue(store.engine, envelope, now=inbox_checks.T0).status == "queued"
    elif evidence == "c_observation":
        assert ingest_observation(store.engine, envelope).status == "accepted_new"
    else:
        with Session(store.engine) as session:
            batch = Batch(
                warehouse_id=store.warehouse_id, code="bootstrap-batch", product="apple"
            )
            session.add(batch)
            session.commit()
            receipt = receive_candidate(
                session,
                _rc2(
                    source_event_id=str(uuid4()),
                    warehouse_id=store.warehouse_id,
                    batch_id=batch.id,
                ),
            )
            assert receipt.status == "accepted_new"
    with pytest.raises(FrontierUnavailable, match="prior A/B/C"):
        initialize(store, epochs)
    assert saved(store) == []
    with store.engine.connect() as connection:
        name = {
            "raw": "iot_telemetry_inbox",
            "c_observation": "telemetry_observation_candidate",
            "b_event": "alerts_event",
        }[evidence]
        quote = connection.dialect.identifier_preparer.quote
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM " + quote(store.schema) + "." + quote(name)
            ).scalar()
            == 1
        )


def test_matching_retry_preserves_pending_counters_and_bad_evidence_hold(world):
    store, epochs = world
    assert initialize(store, epochs).created
    assert (
        enqueue(
            store.engine,
            inbox_checks.frame(store),
            now=inbox_checks.T0,
            frontier_epoch=epochs[store.device_id],
        ).status
        == "queued"
    )
    with store.engine.begin() as connection:
        mark_uncertain(
            connection, device_id=store.device_id, epoch=epochs[store.device_id]
        )
    before = dict(saved(store)[0])
    assert not initialize(store, epochs).created
    assert dict(saved(store)[0]) == before
    with pytest.raises(FrontierUnavailable, match="must not be rewritten"):
        initialize(store, {store.device_id: uuid4()})
    assert dict(saved(store)[0]) == before


def test_inactive_or_non_admin_and_unknown_actor_have_zero_writes(world):
    store, epochs = world
    with pytest.raises(FrontierUnavailable, match="administrator"):
        initialize(store, epochs, actor_id=uuid4())
    for flags in ({"is_superuser": False}, {"is_superuser": True, "is_active": False}):
        with store.engine.begin() as connection:
            connection.execute(
                update(User).where(col(User.id) == store.actor_id).values(**flags)
            )
        with pytest.raises(FrontierUnavailable, match="administrator"):
            initialize(store, epochs)
    assert saved(store) == []


def test_missing_c_schema_is_not_replaced_with_ddl(world):
    store, epochs = world
    c_metadata.tables["telemetry_quality_tag_candidate"].drop(store.engine)
    with pytest.raises(FrontierUnavailable, match="complete evidence schema"):
        initialize(store, epochs)
    assert saved(store) == []
    assert "telemetry_quality_tag_candidate" not in inspect(
        store.engine
    ).get_table_names(schema=store.schema)


@pytest.mark.parametrize("changed", [False, True])
def test_concurrent_bootstrap_waits_for_commit_and_cannot_rotate_epoch(world, changed):
    store, epochs = world
    second = {store.device_id: uuid4()} if changed else epochs
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.engine.begin() as connection:
            assert initialize(store, epochs, connection).created
            future = pool.submit(initialize, store, second)
            deadline, waited = time.monotonic() + 3, False
            while time.monotonic() < deadline:
                with store.engine.connect() as monitor:
                    waited = bool(
                        monitor.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND position(:schema IN query)>0"
                            ),
                            {"schema": store.schema},
                        )
                    )
                if waited:
                    break
                time.sleep(0.02)
            assert waited and not future.done()
        if changed:
            with pytest.raises(FrontierUnavailable, match="must not be rewritten"):
                future.result(timeout=8)
        else:
            assert not future.result(timeout=8).created
    assert (
        len(saved(store)) == 1
        and saved(store)[0]["stream_epoch"] == epochs[store.device_id]
    )


def test_without_outer_transaction_or_wrong_current_binding_is_refused(world):
    store, epochs = world
    with store.engine.connect() as connection:
        with pytest.raises(ValueError):
            initialize(store, epochs, connection)
        assert not connection.in_transaction()
    with pytest.raises(FrontierUnavailable, match="binding mismatch"):
        initialize(store, epochs, bindings={store.device_id: uuid4()})
    assert saved(store) == []


@pytest.mark.parametrize("mode", ["unbound", "actuator", "unknown_warehouse"])
def test_actual_registered_sensor_and_real_b_warehouse_are_required(world, mode):
    store, epochs = world
    target = uuid4() if mode == "unknown_warehouse" else store.warehouse_id
    values = (
        {"warehouse_id": None}
        if mode == "unbound"
        else {"kind": "cooling_actuator"}
        if mode == "actuator"
        else {"warehouse_id": target}
    )
    with store.engine.begin() as connection:
        connection.execute(
            update(Device).where(col(Device.id) == store.device_id).values(**values)
        )
    with pytest.raises(FrontierUnavailable):
        initialize(store, epochs, bindings={store.device_id: target})
    assert saved(store) == []


def test_unknown_c_evidence_table_requires_new_inventory_review(world):
    store, epochs = world
    unknown = Table(
        "telemetry_unreviewed", MetaData(), Column("id", Integer, primary_key=True)
    )
    unknown.create(store.engine)
    try:
        with pytest.raises(FrontierUnavailable, match="complete evidence schema"):
            initialize(store, epochs)
        assert saved(store) == []
    finally:
        unknown.drop(store.engine)


def test_repeatable_read_and_unverified_existing_origin_cannot_be_promoted(world):
    store, epochs = world
    with store.engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        with connection.begin():
            with pytest.raises(FrontierUnavailable, match="READ COMMITTED"):
                initialize(store, epochs, connection)
    assert saved(store) == []
    assert initialize(store, epochs).created
    with store.engine.begin() as connection:
        connection.execute(update(streams).values(origin_verified=False))
    with pytest.raises(FrontierUnavailable, match="must not be rewritten"):
        initialize(store, epochs)
    assert saved(store)[0]["origin_verified"] is False


@pytest.mark.parametrize(
    "mode", ["empty", "oversize", "actor", "binding", "epoch", "mismatched_scope"]
)
def test_bad_input_is_rejected_before_any_database_operation(mode):
    device, warehouse, epoch = uuid4(), uuid4(), uuid4()
    actor, bindings, epochs = uuid4(), {device: warehouse}, {device: epoch}
    if mode == "empty":
        bindings, epochs = {}, {}
    elif mode == "oversize":
        bindings = {uuid4(): warehouse for _ in range(15)}
        epochs = {identity: uuid4() for identity in bindings}
    elif mode == "actor":
        actor = str(actor)
    elif mode == "binding":
        bindings = {device: str(warehouse)}
    elif mode == "epoch":
        epochs = {device: str(epoch)}
    else:
        epochs = {uuid4(): epoch}
    connection = MagicMock()
    with pytest.raises(ValueError):
        initialize_fresh_streams(
            connection, actor_id=actor, bindings=bindings, epochs=epochs
        )
    assert connection.mock_calls == []
