"""A24 real PG candidate checks; synthetic sources except explicit C tests.

No app/public-schema DDL. Reuse the existing random private inbox fixture and
add only this candidate's metadata. Verified origin is fixture-only, never an
application bootstrap or permission to enable automatic recovery.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.modules.iot import stream_frontier as frontier
from app.modules.iot.ingress import IngressRejected
from app.modules.iot.stream_frontier_models import entries, metadata, streams
from app.modules.iot.telemetry_inbox import (
    QueueLimits,
    claim_next,
    deliver_claim,
    enqueue,
    prune_history,
    record_rejection,
)
from app.modules.iot.telemetry_inbox_models import inbox, receipts
from tests.api.routes import test_telemetry_inbox as inbox_checks

T0 = inbox_checks.T0


@dataclass
class FrontierStore(inbox_checks.Store):
    epoch: UUID


@pytest.fixture
def store():
    if os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") != "1":
        pytest.skip("requires controlled inbox PG test entry")
    url = make_url(str(settings.DATABASE_URL))
    disposable = (
        url.host == "test-db"
        and url.database == "freshlink_test"
        and settings.PROJECT_NAME == "FreshLink-test"
    )
    cloud_test = (
        os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("CI") == "true"
        and url.host in {"localhost", "127.0.0.1", "db"}
        and url.database == "app"
        and settings.FASTAPI_ENV == "development"
    )
    if not (disposable or cloud_test):
        raise AssertionError("frontier tests require owned disposable PG or hosted CI")
    with contextmanager(inbox_checks.store.__wrapped__)() as base:
        fixture = FrontierStore(
            base.engine,
            base.schema,
            base.device_id,
            base.warehouse_id,
            base.actor_id,
            uuid4(),
        )
        metadata.create_all(fixture.engine)
        try:
            seed(fixture)
            yield fixture
        finally:
            metadata.drop_all(fixture.engine)


def seed(store):
    # Explicit test-only seed. No production service creates/rotates an epoch.
    with store.engine.begin() as connection:
        connection.execute(
            streams.insert().values(
                device_id=store.device_id,
                warehouse_id=store.warehouse_id,
                stream_epoch=store.epoch,
                origin_verified=True,
                admitted_seq=0,
                settled_through=0,
                evidence_revision=0,
                gap_reason="",
            )
        )


def state(store):
    with store.engine.connect() as connection:
        return (
            connection.execute(
                select(streams).where(streams.c.device_id == store.device_id)
            )
            .mappings()
            .one()
        )


def ledger(store):
    with store.engine.connect() as connection:
        return (
            connection.execute(
                select(entries)
                .where(entries.c.device_id == store.device_id)
                .order_by(entries.c.admission_seq)
            )
            .mappings()
            .all()
        )


def admit(store, envelope=None, **kwargs):
    envelope = inbox_checks.frame(store) if envelope is None else envelope
    return enqueue(store.engine, envelope, now=T0, frontier_epoch=store.epoch, **kwargs)


def stamp(store, connection=None):
    if connection is not None:
        return frontier.read_complete_frontier(
            connection,
            device_id=store.device_id,
            warehouse_id=store.warehouse_id,
            epoch=store.epoch,
        )
    with store.engine.begin() as own:
        return stamp(store, own)


def terminal(store, envelope, outcome="accepted_new", observation_id=None):
    # This is a bookkeeping unit port, not a claim of a real C commit.
    with store.engine.begin() as connection:
        saved = (
            connection.execute(
                select(entries).where(entries.c.reception_id == envelope.reception_id)
            )
            .mappings()
            .one()
        )
        frontier.record_terminal(
            connection,
            reception_id=envelope.reception_id,
            fingerprint=saved["fingerprint"],
            epoch=store.epoch,
            outcome=outcome,
            observation_id=observation_id
            or (uuid4() if outcome.startswith("accepted_") else None),
        )


def test_monotonic_admission_not_timestamp_or_uuid_order_and_replay(store):
    first = replace(inbox_checks.frame(store), reception_id=UUID(int=200))
    later = replace(
        inbox_checks.frame(store),
        reception_id=UUID(int=1),
        received_at=T0 - timedelta(seconds=1),
    )
    assert admit(store, first).status == "queued"
    assert admit(store, first).status == "already_queued"
    assert admit(store, later).status == "queued"
    assert [row["reception_id"] for row in ledger(store)] == [
        first.reception_id,
        later.reception_id,
    ]
    assert state(store)["admitted_seq"] == 2
    assert stamp(store) is None


def test_out_of_order_terminal_never_skips_the_first_gap(store):
    first, second = inbox_checks.frame(store), inbox_checks.frame(store)
    assert admit(store, first).status == admit(store, second).status == "queued"
    terminal(store, second)
    assert state(store)["settled_through"] == 0 and stamp(store) is None
    terminal(store, first)
    assert state(store)["settled_through"] == 2
    assert stamp(store).admitted_seq == 2


def test_raw_and_sequence_roll_back_together_and_no_sequence_hole(store):
    envelope = inbox_checks.frame(store)

    def fail(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            statement.lstrip().startswith("INSERT INTO")
            and "iot_stream_admission" in statement
        ):
            raise OperationalError(
                "injected A ledger failure", {}, RuntimeError("offline")
            )

    event.listen(store.engine, "before_cursor_execute", fail)
    try:
        assert admit(store, envelope).status == "retryable_failure"
    finally:
        event.remove(store.engine, "before_cursor_execute", fail)
    assert state(store)["admitted_seq"] == 0 and ledger(store) == []
    assert inbox_checks.rows(store, inbox) == []
    assert admit(store, envelope).status == "queued"
    assert ledger(store)[0]["admission_seq"] == 1


def test_missing_epoch_and_missing_tables_fail_closed_without_auto_creation(store):
    assert (
        enqueue(
            store.engine, inbox_checks.frame(store), now=T0, frontier_epoch=uuid4()
        ).status
        == "retryable_failure"
    )
    with store.engine.begin() as connection:
        connection.execute(delete(streams))
    assert admit(store).status == "retryable_failure"
    metadata.drop_all(store.engine)
    assert admit(store).status == "retryable_failure"
    from sqlalchemy import inspect

    assert "iot_stream_frontier" not in inspect(store.engine).get_table_names(
        schema=store.schema
    )


def test_two_writers_allocate_distinct_contiguous_committed_numbers(store):
    gate = Barrier(2)

    def receive():
        gate.wait(timeout=5)
        return admit(store).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(receive) for _ in range(2)]
        assert [future.result(timeout=10) for future in futures] == ["queued", "queued"]
    assert [row["admission_seq"] for row in ledger(store)] == [1, 2]


@pytest.mark.parametrize("outcome", ["invalid", "conflict"])
def test_bad_terminal_advances_completeness_but_never_clears_safety_hold(
    store, outcome
):
    envelope = inbox_checks.frame(store)
    assert admit(store, envelope).status == "queued"
    terminal(store, envelope, outcome)
    assert state(store)["settled_through"] == 1
    assert state(store)["evidence_revision"] == 1 and stamp(store) is None
    prune_history(store.engine, now=T0 + timedelta(days=8))
    assert state(store)["gap_reason"] == "terminal_bad_evidence"


def test_cross_device_reception_reuse_holds_both_without_replacing_raw(store):
    base_other = inbox_checks.second_device(store)
    other = replace(base_other, epoch=uuid4())
    seed(other)
    original = inbox_checks.frame(store)
    assert admit(store, original).status == "queued"
    attempted = replace(inbox_checks.frame(other), reception_id=original.reception_id)
    assert admit(other, attempted).status == "conflict"
    assert (
        state(store)["gap_reason"] == state(other)["gap_reason"] == "reception_id_reuse"
    )
    assert inbox_checks.rows(store, inbox)[0]["raw_text"] == original.raw_text
    assert len(ledger(store)) == 1 and ledger(other) == []


def test_queue_full_and_known_rejection_are_persistent_holds(store):
    assert admit(store).status == "queued"
    assert admit(store, limits=QueueLimits(max_items=1)).status == "queue_full"
    assert state(store)["admitted_seq"] == 1 and state(store)["gap_reason"]
    assert record_rejection(
        store.engine,
        IngressRejected("invalid_json", b"bad"),
        device_id=store.device_id,
        frontier_epoch=store.epoch,
        now=T0,
    )
    assert state(store)["evidence_revision"] == 2


def test_legacy_pending_is_not_silently_assigned_a_new_sequence(store):
    envelope = inbox_checks.frame(store)
    assert enqueue(store.engine, envelope, now=T0).status == "queued"
    assert admit(store, envelope).status == "retryable_failure"
    assert state(store)["admitted_seq"] == 0 and state(store)["gap_reason"]
    assert len(inbox_checks.rows(store, inbox)) == 1


def test_metadata_loss_unverified_origin_and_binding_change_hold(store):
    envelope = inbox_checks.frame(store)
    assert admit(store, envelope).status == "queued"
    terminal(store, envelope)
    assert stamp(store) is not None
    with store.engine.begin() as connection:
        connection.execute(
            inbox_checks.Device.__table__.update().values(warehouse_id=uuid4())
        )
    assert stamp(store) is None
    with store.engine.begin() as connection:
        connection.execute(
            inbox_checks.Device.__table__.update().values(
                warehouse_id=store.warehouse_id
            )
        )
    with store.engine.begin() as connection:
        connection.execute(update(streams).values(origin_verified=False))
    assert stamp(store) is None
    with store.engine.begin() as connection:
        connection.execute(update(streams).values(origin_verified=True))
        connection.execute(delete(entries))
    assert stamp(store) is None


def test_stale_claim_does_not_advance_the_frontier_or_call_c(store):
    assert admit(store).status == "queued"
    old = claim_next(store.engine, now=T0)
    current = claim_next(store.engine, now=T0 + timedelta(seconds=31))
    assert old is not None and current is not None

    def forbidden(_envelope):
        raise AssertionError("old claim must not call C")

    result = deliver_claim(
        store.engine,
        old,
        forbidden,
        now=T0 + timedelta(seconds=31),
        frontier_epoch=store.epoch,
    )
    assert result.status == "lease_lost" and state(store)["settled_through"] == 0


def test_actual_c_commit_atomic_a_checkpoint_and_retained_replay(store):
    from app.modules.telemetry.storage_candidate import ingest_observation
    from app.modules.telemetry.storage_candidate import metadata as c_metadata

    c_metadata.create_all(store.engine)
    try:
        envelope = inbox_checks.frame(store)
        assert admit(store, envelope).status == "queued"
        claim = claim_next(store.engine, now=T0)
        assert claim is not None
        result = deliver_claim(
            store.engine,
            claim,
            lambda body: ingest_observation(store.engine, body),
            now=T0,
            frontier_epoch=store.epoch,
        )
        assert result.status == "delivered" and stamp(store) is not None
        prune_history(store.engine, now=T0 + timedelta(days=8))
        assert inbox_checks.rows(store, receipts) == []
        assert admit(store, envelope).status == "already_processed"
        assert state(store)["admitted_seq"] == 1
    finally:
        c_metadata.drop_all(store.engine)


def test_c_commit_then_a_failure_keeps_frontier_pending_until_same_id_replay(store):
    from app.modules.telemetry.storage_candidate import ingest_observation, observations
    from app.modules.telemetry.storage_candidate import metadata as c_metadata

    c_metadata.create_all(store.engine)

    def fail(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            statement.lstrip().startswith("UPDATE")
            and "iot_stream_admission" in statement
        ):
            raise OperationalError(
                "injected checkpoint failure", {}, RuntimeError("offline")
            )

    try:
        envelope = inbox_checks.frame(store)
        assert admit(store, envelope).status == "queued"
        first = claim_next(store.engine, now=T0)
        event.listen(store.engine, "before_cursor_execute", fail)
        try:
            result = deliver_claim(
                store.engine,
                first,
                lambda body: ingest_observation(store.engine, body),
                now=T0,
                frontier_epoch=store.epoch,
            )
        finally:
            event.remove(store.engine, "before_cursor_execute", fail)
        assert result.status == "retryable_failure" and stamp(store) is None
        second = claim_next(store.engine, now=T0 + timedelta(seconds=31))
        assert (
            second is not None and second.envelope.reception_id == envelope.reception_id
        )
        assert (
            deliver_claim(
                store.engine,
                second,
                lambda body: ingest_observation(store.engine, body),
                now=T0 + timedelta(seconds=31),
                frontier_epoch=store.epoch,
            ).status
            == "delivered"
        )
        assert stamp(store) is not None
        with store.engine.connect() as connection:
            assert len(connection.execute(select(observations)).all()) == 1
    finally:
        c_metadata.drop_all(store.engine)


def test_freeze_lock_orders_concurrent_admission_and_old_stamp_becomes_invalid(store):
    envelope = inbox_checks.frame(store)
    assert admit(store, envelope).status == "queued"
    terminal(store, envelope)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.engine.begin() as connection:
            frozen = stamp(store, connection)
            assert frozen is not None
            future = pool.submit(admit, store)
            deadline = time.monotonic() + 3
            waited = False
            while time.monotonic() < deadline:
                with store.engine.connect() as monitor:
                    waited = bool(
                        monitor.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' "
                                "AND position(:schema IN query)>0"
                            ),
                            {"schema": store.schema},
                        )
                    )
                if waited:
                    break
                time.sleep(0.02)
            assert waited and not future.done()
            assert frontier.stamp_is_current(connection, frozen)
        assert future.result(timeout=8).status == "queued"
    with store.engine.begin() as connection:
        assert not frontier.stamp_is_current(connection, frozen)


def test_connection_without_outer_transaction_and_repeatable_read_are_refused(store):
    with store.engine.connect() as connection:
        with pytest.raises(ValueError):
            stamp(store, connection)
        assert not connection.in_transaction()
    with store.engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        with connection.begin():
            with pytest.raises(frontier.FrontierUnavailable):
                stamp(store, connection)


def test_frontier_capacity_refuses_before_raw_insert_and_persists_hold(store):
    # Boundary fixture, not a claim of having processed 10,000 actual samples.
    with store.engine.begin() as connection:
        connection.execute(update(streams).values(admitted_seq=frontier.MAX_ENTRIES))
    assert admit(store).status == "queue_full"
    assert state(store)["gap_reason"] == "frontier_capacity"
    assert inbox_checks.rows(store, inbox) == [] and ledger(store) == []


def test_admission_lock_timeout_is_retryable_and_cannot_ack_or_allocate(store):
    envelope = inbox_checks.frame(store)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.engine.begin() as connection:
            frontier._locked(connection, {store.device_id})
            future = pool.submit(admit, store, envelope)
            assert future.result(timeout=8).status == "retryable_failure"
            assert state(store)["admitted_seq"] == 0
    assert ledger(store) == [] and inbox_checks.rows(store, inbox) == []
    assert admit(store, envelope).status == "queued"


def test_c_outbox_staging_uses_same_frozen_connection_and_outer_rollback(store):
    from app.modules.telemetry.outbox_candidate import (
        outbox,
        stage_f03_on_connection_candidate,
    )
    from app.modules.telemetry.storage_candidate import ingest_observation
    from app.modules.telemetry.storage_candidate import metadata as c_metadata

    c_metadata.create_all(store.engine)
    try:
        envelope = inbox_checks.frame(store)
        assert admit(store, envelope).status == "queued"
        claim = claim_next(store.engine, now=T0)
        delivered = deliver_claim(
            store.engine,
            claim,
            lambda body: ingest_observation(store.engine, body),
            now=T0,
            frontier_epoch=store.epoch,
        )
        assert delivered.status == "delivered"
        source = uuid4()
        body = {
            "schema_version": "1.0",
            "source_event_id": str(source),
            "warehouse_id": str(store.warehouse_id),
            "batch_id": str(uuid4()),
            "observation_ids": [str(delivered.observation_id)],
            "metric_code": "temperature",
            "sampled_at": T0.isoformat(),
            "value": 11.0,
            "unit": "°C",
            "kind": "temperature_high",
            "severity": "warning",
            "rule_snapshot_id": str(uuid4()),
            "rule_version": "probe-v1",
            "algorithm_version": "threshold-rule/probe",
            "evidence": {"threshold": 8},
            "related_source_event_id": None,
        }
        with store.engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert stamp(store, connection) is not None
                assert (
                    stage_f03_on_connection_candidate(
                        connection,
                        source_event_id=source,
                        observation_id=delivered.observation_id,
                        body=body,
                        now=T0,
                    )
                    == "staged"
                )
            finally:
                transaction.rollback()
        with store.engine.connect() as connection:
            assert connection.execute(select(outbox)).all() == []
        assert stamp(store) is not None
        with store.engine.begin() as connection:
            assert stamp(store, connection) is not None
            assert (
                stage_f03_on_connection_candidate(
                    connection,
                    source_event_id=source,
                    observation_id=delivered.observation_id,
                    body=body,
                    now=T0,
                )
                == "staged"
            )
        with store.engine.connect() as connection:
            assert len(connection.execute(select(outbox)).all()) == 1
    finally:
        c_metadata.drop_all(store.engine)
