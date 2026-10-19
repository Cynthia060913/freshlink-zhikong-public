"""A continuous event delivery: real PG, private test schemas, no notifications."""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlmodel import SQLModel

from app.core.config import settings
from app.models import User
from app.modules.alerts.models import AlertEvent, Batch, Warehouse
from app.modules.alerts.models.event import CandidateReceipt, compute_content_digest
from app.modules.iot import event_dispatch as dispatch
from app.modules.iot.event_dispatch_models import checkpoints, metadata, retry_audit
from app.modules.iot.event_worker import local_receiver
from app.modules.iot.models import Device, DeviceConnectivityEvent

T0 = datetime(2026, 10, 18, tzinfo=UTC)
source = DeviceConnectivityEvent.__table__


@dataclass
class Store:
    engine: object
    device_id: object
    warehouse_id: object
    actor_id: object


@pytest.fixture
def store():
    if os.getenv("FRESHLINK_EVENT_TEST_POSTGRES") != "1":
        pytest.skip("requires explicit disposable event PG test opt-in")
    url = make_url(str(settings.DATABASE_URL))
    local = (
        url.host == "test-db"
        and url.database == "freshlink_test"
        and url.username == "freshlink_test"
        and settings.PROJECT_NAME == "FreshLink-test"
    )
    ci = (
        os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("CI") == "true"
        and url.host in {"localhost", "127.0.0.1", "db"}
        and url.database == "app"
    )
    if (
        url.get_backend_name() != "postgresql"
        or settings.FASTAPI_ENV != "development"
        or not (local or ci)
    ):
        raise AssertionError("Refusing non-disposable event test database")
    base = create_engine(url, pool_pre_ping=True)
    schema = "a_event_test_" + uuid4().hex
    assert re.fullmatch(r"a_event_test_[0-9a-f]{32}", schema)
    view = base.execution_options(schema_translate_map={None: schema})
    tables = [
        User.__table__,
        Device.__table__,
        source,
        Warehouse.__table__,
        Batch.__table__,
        AlertEvent.__table__,
    ]
    fixture = Store(view, uuid4(), uuid4(), uuid4())
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        for table in tables:
            table.create(view)
        metadata.create_all(view)
        with view.begin() as connection:
            connection.execute(
                User.__table__.insert().values(
                    id=fixture.actor_id,
                    email="event-admin@example.com",
                    hashed_password="fixture-only",
                    is_active=True,
                    is_superuser=True,
                )
            )
            connection.execute(
                Device.__table__.insert().values(
                    id=fixture.device_id,
                    code="event-fixture",
                    name="Event fixture",
                    kind="temperature_sensor",
                    warehouse_id=fixture.warehouse_id,
                    created_at=T0,
                )
            )
            connection.execute(
                Warehouse.__table__.insert().values(
                    id=fixture.warehouse_id,
                    code="event-warehouse",
                    name="Event warehouse",
                    created_at=T0,
                )
            )
        yield fixture
    finally:
        metadata.drop_all(view)
        for table in reversed(tables):
            table.drop(view, checkfirst=True)
        with base.begin() as connection:
            connection.execute(DropSchema(schema))  # Never CASCADE.
        base.dispose()


def add(store, *, kind="offline", related=None, state="pending", warehouse=True, at=T0):
    identity = uuid4()
    with store.engine.begin() as connection:
        connection.execute(
            source.insert().values(
                source_event_id=identity,
                device_id=store.device_id,
                warehouse_id=store.warehouse_id if warehouse else None,
                occurred_at=at,
                kind=kind,
                reason="original_heartbeat_timeout",
                related_source_event_id=related,
                delivery_state=state,
            )
        )
    return identity


def rows(store, table):
    with store.engine.connect() as connection:
        return connection.execute(select(table)).mappings().all()


def claim(store, now=T0):
    return dispatch.claim_event(store.engine, (store.device_id,), now=now)


def receipt(payload, status="accepted_new", **extra):
    return CandidateReceipt(
        status=status,
        source_event_id=payload.source_event_id,
        content_digest=compute_content_digest(payload),
        event_id=uuid4(),
        **extra,
    )


def test_real_b_commit_exact_source_and_atomic_a_checkpoint(store):
    identity = add(store)
    leased = claim(store)
    result = dispatch.deliver_event_claim(
        store.engine, leased, local_receiver(store.engine), now=T0
    )
    assert result.status == "delivered"
    original, checkpoint, target = (
        rows(store, source)[0],
        rows(store, checkpoints)[0],
        rows(store, AlertEvent.__table__)[0],
    )
    assert original["source_event_id"] == identity and original["occurred_at"] == T0
    assert (
        original["reason"] == "original_heartbeat_timeout"
        and original["delivery_state"] == "delivered"
    )
    assert checkpoint["target_event_id"] == target["id"] == result.target_event_id
    assert target["source_event_id"] == str(identity) and target["sampled_at"] == T0
    assert json.loads(target["payload_json"])["device_id"] == str(store.device_id)
    assert claim(store) is None


def test_backoff_and_budget_survive_new_engine_view(store):
    identity = add(store)
    now = T0
    for attempt in range(1, 11):
        leased = claim(store, now)
        assert leased.attempt == attempt and leased.snapshot.source_event_id == identity
        result = dispatch.deliver_event_claim(
            store.engine.execution_options(),
            leased,
            lambda p: receipt(p, "retryable_failure"),
            now=now,
        )
        assert result.status == ("exhausted" if attempt == 10 else "retry_scheduled")
        if attempt < 10:
            assert claim(store, now) is None
        now += timedelta(seconds=min(30, 2 ** (attempt - 1)))
    assert claim(store, now) is None
    assert rows(store, source)[0]["delivery_state"] == "failed"
    assert rows(store, checkpoints)[0]["attempt_count"] == 10


def test_crashed_last_attempt_exhausts_without_eleventh_call(store):
    add(store)
    now = T0
    for attempt in range(1, 11):
        assert claim(store, now).attempt == attempt
        now += timedelta(seconds=31)
    assert claim(store, now) is None
    assert rows(store, checkpoints)[0]["state"] == "exhausted"


def test_b_committed_a_checkpoint_failure_replays_original_to_duplicate(store):
    identity = add(store)
    leased = claim(store)

    def fail_checkpoint(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("UPDATE") and "iot_connectivity_event" in statement:
            raise OperationalError(
                "synthetic checkpoint fault", {}, RuntimeError("fixture")
            )

    event.listen(store.engine, "before_cursor_execute", fail_checkpoint)
    try:
        assert (
            dispatch.deliver_event_claim(
                store.engine, leased, local_receiver(store.engine), now=T0
            ).status
            == "retryable_failure"
        )
    finally:
        event.remove(store.engine, "before_cursor_execute", fail_checkpoint)
    assert rows(store, source)[0]["delivery_state"] == "pending"
    assert rows(store, checkpoints)[0]["state"] == "leased"
    target = rows(store, AlertEvent.__table__)[0]
    later = T0 + timedelta(seconds=31)
    repeated = claim(store, later)
    assert repeated.snapshot.source_event_id == identity and repeated.attempt == 2
    result = dispatch.deliver_event_claim(
        store.engine, repeated, local_receiver(store.engine), now=later
    )
    assert result.status == "delivered" and result.target_event_id == target["id"]
    assert len(rows(store, AlertEvent.__table__)) == 1


def test_two_claimers_cannot_share_live_lease(store):
    add(store)
    add(store)
    gate = Barrier(2)

    def race():
        gate.wait(timeout=5)
        return claim(store)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: race(), range(2)))
    assert first.snapshot.source_event_id != second.snapshot.source_event_id
    assert first.lease_id != second.lease_id and claim(store) is None


def test_recovery_waits_for_original_durable_a_checkpoint(store, monkeypatch):
    monkeypatch.setattr(dispatch, "MAX_PENDING", 1)
    offline = add(store)
    recovery = add(
        store, kind="recovered", related=offline, at=T0 - timedelta(seconds=1)
    )
    first = claim(store)
    assert (
        first.snapshot.source_event_id == offline
    )  # Earlier recovery cannot starve parent.
    assert len(rows(store, checkpoints)) == 1 and claim(store) is None
    assert (
        dispatch.deliver_event_claim(
            store.engine, first, local_receiver(store.engine), now=T0
        ).status
        == "delivered"
    )
    next_claim = claim(store)
    assert (
        next_claim.snapshot.source_event_id == recovery
        and next_claim.snapshot.related_source_event_id == offline
    )
    assert (
        dispatch.deliver_event_claim(
            store.engine, next_claim, local_receiver(store.engine), now=T0
        ).status
        == "delivered"
    )
    assert len(rows(store, AlertEvent.__table__)) == 2


@pytest.mark.parametrize("invalid", ["absent", "recovered", "warehouse", "device"])
def test_invalid_recovery_origin_blocks_without_b_call(store, invalid):
    parent = add(
        store,
        kind="recovered" if invalid == "recovered" else "offline",
        state="delivered",
    )
    with store.engine.begin() as connection:
        if invalid == "warehouse":
            connection.execute(
                update(source)
                .where(source.c.source_event_id == parent)
                .values(warehouse_id=uuid4())
            )
        if invalid == "device":
            other = uuid4()
            connection.execute(
                Device.__table__.insert().values(
                    id=other,
                    code="other",
                    name="Other",
                    kind="temperature_sensor",
                    created_at=T0,
                )
            )
            connection.execute(
                update(source)
                .where(source.c.source_event_id == parent)
                .values(device_id=other)
            )
    recovery = add(
        store, kind="recovered", related=uuid4() if invalid == "absent" else parent
    )
    assert claim(store) is None
    checkpoint = rows(store, checkpoints)[0]
    assert (
        checkpoint["source_event_id"] == recovery
        and checkpoint["last_reason"] == "invalid_recovery_origin"
    )


def test_unbound_history_is_never_backfilled_by_later_device_binding(store):
    add(store, state="held_unbound", warehouse=False)
    assert claim(store) is None and rows(store, checkpoints) == []
    assert rows(store, source)[0]["warehouse_id"] is None


@pytest.mark.parametrize("corrupt", ["claim", "source", "lease"])
def test_preflight_tamper_or_stale_lease_never_calls_b(store, corrupt):
    add(store)
    leased = claim(store)
    if corrupt == "claim":
        leased = replace(leased, snapshot=replace(leased.snapshot, reason="modified"))
    if corrupt == "source":
        with store.engine.begin() as connection:
            connection.execute(update(source).values(reason="modified"))
    calls = []
    result = dispatch.deliver_event_claim(
        store.engine,
        leased,
        lambda p: calls.append(p),
        now=T0 + timedelta(seconds=31) if corrupt == "lease" else T0,
    )
    assert not calls and result.status in {"blocked", "lease_lost"}


def test_source_changes_during_b_call_cannot_checkpoint_success(store):
    add(store)
    leased = claim(store)

    def mutate(payload):
        with store.engine.begin() as connection:
            connection.execute(update(source).values(reason="changed during receiver"))
        return receipt(payload)

    assert (
        dispatch.deliver_event_claim(store.engine, leased, mutate, now=T0).status
        == "blocked"
    )
    assert rows(store, source)[0]["delivery_state"] == "failed"


@pytest.mark.parametrize(
    "mode",
    [
        "wrong_source",
        "wrong_digest",
        "no_target",
        "ambiguous_target",
        "invalid",
        "conflict",
        "not_receipt",
        "raises",
    ],
)
def test_bad_receipts_never_mark_a_delivered(store, mode):
    add(store)
    leased = claim(store)

    def bad(payload):
        if mode == "raises":
            raise RuntimeError("private receiver detail must not leak")
        if mode == "not_receipt":
            return {"status": "accepted_new"}
        result = receipt(payload)
        if mode == "wrong_source":
            result.source_event_id = str(uuid4())
        if mode == "wrong_digest":
            result.content_digest = "different"
        if mode == "no_target":
            result.event_id = None
        if mode == "ambiguous_target":
            result.stored_event_id = uuid4()
        if mode in {"invalid", "conflict"}:
            result.status = mode
        return result

    result = dispatch.deliver_event_claim(store.engine, leased, bad, now=T0)
    assert result.status == ("retry_scheduled" if mode == "raises" else "blocked")
    assert rows(store, source)[0]["delivery_state"] != "delivered"
    assert "private" not in result.reason


def test_admin_retry_is_authorized_audited_idempotent_and_preserves_body(store):
    identity = add(store)
    dispatch.deliver_event_claim(store.engine, claim(store), lambda _: object(), now=T0)
    with pytest.raises(PermissionError):
        dispatch.retry_event(store.engine, identity, actor_id=uuid4(), now=T0)
    with store.engine.begin() as connection:
        connection.execute(update(User.__table__).values(is_superuser=False))
    with pytest.raises(PermissionError):
        dispatch.retry_event(store.engine, identity, actor_id=store.actor_id, now=T0)
    with store.engine.begin() as connection:
        connection.execute(
            update(User.__table__).values(is_superuser=True, is_active=False)
        )
    with pytest.raises(PermissionError):
        dispatch.retry_event(store.engine, identity, actor_id=store.actor_id, now=T0)
    with store.engine.begin() as connection:
        connection.execute(update(User.__table__).values(is_active=True))
    assert (
        dispatch.retry_event(store.engine, uuid4(), actor_id=store.actor_id, now=T0)
        == "not_found"
    )
    assert (
        dispatch.retry_event(store.engine, identity, actor_id=store.actor_id, now=T0)
        == "retry_requested"
    )
    assert (
        dispatch.retry_event(store.engine, identity, actor_id=store.actor_id, now=T0)
        == "already_queued"
    )
    audit = rows(store, retry_audit)
    assert (
        len(audit) == 1
        and audit[0]["actor_id"] == store.actor_id
        and audit[0]["cycle"] == 1
    )
    leased = claim(store)
    assert leased.attempt == 1
    assert (
        dispatch.retry_event(store.engine, identity, actor_id=store.actor_id, now=T0)
        == "conflict"
    )
    dispatch.deliver_event_claim(
        store.engine, leased, local_receiver(store.engine), now=T0
    )
    assert (
        dispatch.retry_event(store.engine, identity, actor_id=store.actor_id, now=T0)
        == "conflict"
    )
    assert rows(store, source)[0]["reason"] == "original_heartbeat_timeout"


def test_capacity_retains_unadmitted_sources_and_bounded_metadata(store, monkeypatch):
    monkeypatch.setattr(dispatch, "MAX_PENDING", 1)
    monkeypatch.setattr(dispatch, "HISTORY_LIMIT", 2)
    for _ in range(4):
        add(store)
    leased = claim(store)
    assert len(rows(store, checkpoints)) == 1 and len(rows(store, source)) == 4
    assert claim(store) is None
    for _ in range(4):
        assert (
            dispatch.deliver_event_claim(
                store.engine, leased, local_receiver(store.engine), now=T0
            ).status
            == "delivered"
        )
        leased = claim(store)
    assert leased is None and len(rows(store, checkpoints)) == 2
    assert len(rows(store, source)) == len(rows(store, AlertEvent.__table__)) == 4
    claim(store, T0 + timedelta(days=8))
    assert rows(store, checkpoints) == [] and len(rows(store, source)) == 4


def test_missing_table_fails_closed_without_automatic_ddl(store):
    metadata.drop_all(store.engine)
    with pytest.raises(SQLAlchemyError):
        claim(store)
    assert not set(metadata.tables).intersection(SQLModel.metadata.tables)


def test_changed_source_cannot_be_retried(store):
    identity = add(store)
    dispatch.deliver_event_claim(store.engine, claim(store), lambda _: object(), now=T0)
    with store.engine.begin() as connection:
        connection.execute(update(source).values(reason="modified"))
    assert (
        dispatch.retry_event(store.engine, identity, actor_id=store.actor_id, now=T0)
        == "source_changed"
    )
    assert rows(store, retry_audit) == []


def test_preflight_storage_failure_never_calls_receiver(store):
    add(store)
    leased = claim(store)

    def fail(_conn, _cursor, _statement, _params, _context, _many):
        raise OperationalError("synthetic preflight fault", {}, RuntimeError("fixture"))

    calls = []
    event.listen(store.engine, "before_cursor_execute", fail)
    try:
        result = dispatch.deliver_event_claim(
            store.engine, leased, lambda p: calls.append(p), now=T0
        )
    finally:
        event.remove(store.engine, "before_cursor_execute", fail)
    assert result.status == "retryable_failure" and not calls


def test_lease_lost_during_receiver_does_not_acknowledge_a(store):
    add(store)
    leased = claim(store)

    def expire(payload):
        with store.engine.begin() as connection:
            connection.execute(update(checkpoints).values(lease_until=T0))
        return receipt(payload)

    result = dispatch.deliver_event_claim(store.engine, leased, expire, now=T0)
    assert result.status == "lease_lost"
    assert rows(store, source)[0]["delivery_state"] == "pending"


def test_source_changed_before_reclaim_is_blocked(store):
    add(store)
    claim(store)
    with store.engine.begin() as connection:
        connection.execute(update(source).values(reason="changed after lease"))
    assert claim(store, T0 + timedelta(seconds=31)) is None
    assert rows(store, checkpoints)[0]["last_reason"] == "source_changed"
