"""A-owned candidate gate, real private PG; not enabled C business wrappers."""

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text, update
from sqlmodel import col

from app.modules.iot import stream_frontier as frontier
from app.modules.iot.models import Device
from tests.api.routes.test_stream_frontier import (
    admit,
    stamp,
    state,
    terminal,
)
from tests.api.routes.test_stream_frontier import store as frontier_store
from tests.api.routes.test_telemetry_inbox import frame


@pytest.fixture
def store():
    yield from frontier_store.__wrapped__()


def write(store, connection):
    return frontier.begin_evidence_write(
        connection,
        device_id=store.device_id,
        warehouse_id=store.warehouse_id,
        epoch=store.epoch,
    )


def complete(store):
    envelope = frame(store)
    assert admit(store, envelope).status == "queued"
    terminal(store, envelope)
    frozen = stamp(store)
    assert frozen is not None
    return frozen


def wait_for_lock(store, future):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with store.engine.connect() as monitor:
            waiting = monitor.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' "
                    "AND position(:schema IN query)>0"
                ),
                {"schema": store.schema},
            )
        if waiting:
            assert not future.done()
            return
        time.sleep(0.02)
    pytest.fail("independent PG connection never entered actual lock wait")


def test_revision_and_caller_rollback_without_a_new_admission(store):
    frozen = complete(store)
    with store.engine.connect() as connection:
        outer = connection.begin()
        assert write(store, connection) == frozen.evidence_revision + 1
        assert not frontier.stamp_is_current(connection, frozen)
        outer.rollback()
    assert stamp(store) == frozen
    with store.engine.begin() as connection:
        assert write(store, connection) == frozen.evidence_revision + 1
    assert state(store)["admitted_seq"] == frozen.admitted_seq
    assert stamp(store).evidence_revision == frozen.evidence_revision + 1


@pytest.mark.parametrize("commit", [True, False])
def test_writer_blocks_freeze_until_its_outer_outcome(store, commit):
    frozen = complete(store)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.engine.connect() as connection:
            outer = connection.begin()
            try:
                write(store, connection)
                future = pool.submit(stamp, store)
                wait_for_lock(store, future)
                if commit:
                    outer.commit()
                else:
                    outer.rollback()
            finally:
                if outer.is_active:
                    outer.rollback()
        result = future.result(timeout=8)
    assert result.evidence_revision == frozen.evidence_revision + int(commit)


def test_freeze_blocks_writer_and_committed_write_invalidates_stamp(store):
    complete(store)

    def attempt():
        with store.engine.begin() as connection:
            return write(store, connection)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.engine.begin() as connection:
            frozen = stamp(store, connection)
            future = pool.submit(attempt)
            wait_for_lock(store, future)
            assert frontier.stamp_is_current(connection, frozen)
        assert future.result(timeout=8) == frozen.evidence_revision + 1
    assert stamp(store) != frozen


def test_binding_epoch_missing_stream_and_no_outer_transaction_fail_closed(store):
    initial = state(store)["evidence_revision"]
    with store.engine.connect() as connection:
        with pytest.raises(ValueError):
            write(store, connection)
        assert not connection.in_transaction()
    for parameters in (
        {"warehouse_id": uuid4()},
        {"epoch": uuid4()},
        {"device_id": uuid4()},
    ):
        with store.engine.begin() as connection:
            with pytest.raises(frontier.FrontierUnavailable):
                frontier.begin_evidence_write(
                    connection,
                    **{
                        "device_id": store.device_id,
                        "warehouse_id": store.warehouse_id,
                        "epoch": store.epoch,
                        **parameters,
                    },
                )
    with store.engine.begin() as connection:
        connection.execute(
            update(Device)
            .where(col(Device.id) == store.device_id)
            .values(warehouse_id=None)
        )
    with store.engine.begin() as connection:
        with pytest.raises(frontier.FrontierUnavailable, match="current device"):
            write(store, connection)
    assert state(store)["evidence_revision"] == initial
