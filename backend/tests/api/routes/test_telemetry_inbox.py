"""Real PG, private per-test schemas; never create queue tables in app schema."""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import settings
from app.models import User
from app.modules.iot.ingress import DeviceContext, IngressRejected, receive_telemetry
from app.modules.iot.models import Device
from app.modules.iot.telemetry_inbox import (
    MAX_ATTEMPTS,
    QueueLimits,
    claim_next,
    deliver_claim,
    enqueue,
    prune_history,
    record_rejection,
    retry_pending,
)
from app.modules.iot.telemetry_inbox_models import (
    audit,
    inbox,
    metadata,
    queue_guard,
    receipts,
)

T0 = datetime(2026, 10, 18, tzinfo=UTC)


@dataclass
class Store:
    engine: Engine
    schema: str
    device_id: UUID
    warehouse_id: UUID
    actor_id: UUID


@dataclass(frozen=True)
class SinkReceipt:
    status: str
    reception_id: UUID
    observation_id: UUID | None = None


@pytest.fixture
def store():
    if os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") != "1":
        pytest.skip("requires explicit disposable inbox PG test opt-in")
    url = make_url(str(settings.DATABASE_URL))
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"test-db", "localhost", "127.0.0.1", "db"}
        or settings.FASTAPI_ENV != "development"
    ):
        raise AssertionError("Refusing non-local/non-development inbox test database")
    base = create_engine(url, pool_pre_ping=True)
    schema = "a_inbox_test_" + uuid4().hex
    assert re.fullmatch(r"a_inbox_test_[0-9a-f]{32}", schema)
    view = base.execution_options(schema_translate_map={None: schema})
    user_table, device_table = User.__table__, Device.__table__
    fixture = Store(view, schema, uuid4(), uuid4(), uuid4())
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        # Deliberately private test copies, not the application's public tables.
        user_table.create(view)
        device_table.create(view)
        metadata.create_all(view)
        with view.begin() as connection:
            connection.execute(queue_guard.insert().values(id=1))
            connection.execute(
                user_table.insert().values(
                    id=fixture.actor_id,
                    email="inbox-admin@example.com",
                    hashed_password="fixture-only",
                    is_active=True,
                    is_superuser=True,
                )
            )
            connection.execute(
                device_table.insert().values(
                    id=fixture.device_id,
                    code="inbox-fixture",
                    name="Inbox fixture",
                    kind="temperature_sensor",
                    warehouse_id=fixture.warehouse_id,
                    created_at=T0,
                )
            )
        yield fixture
    finally:
        metadata.drop_all(view)
        device_table.drop(view, checkfirst=True)
        user_table.drop(view, checkfirst=True)
        with base.begin() as connection:
            # No CASCADE; unexpected extra objects must stop schema removal.
            connection.execute(DropSchema(schema))
        base.dispose()


def frame(store, *, value=11, extra=None):
    raw = {
        "schema_version": "1.0",
        "device_id": str(store.device_id),
        "message_id": str(uuid4()),
        "sampled_at": T0.isoformat(),
        "metrics": [{"key": "temperature", "value": value, "unit": "°C"}],
        **(extra or {}),
    }
    return receive_telemetry(
        topic=f"freshlink/v1/devices/{store.device_id}/telemetry",
        payload=json.dumps(raw, ensure_ascii=False, indent=2).encode(),
        context=DeviceContext(store.device_id, store.warehouse_id),
        received_at=T0,
    )


def rows(store, table):
    with store.engine.connect() as connection:
        return connection.execute(select(table)).mappings().all()


def admit_claim(store):
    envelope = frame(store)
    assert enqueue(store.engine, envelope, now=T0).status == "queued"
    claim = claim_next(store.engine, now=T0)
    assert claim is not None
    return envelope, claim


def test_committed_exact_frame_and_stable_admission_retry(store):
    envelope = frame(store, extra={"note": "测试原文"})
    assert enqueue(store.engine, envelope, now=T0).status == "queued"
    assert enqueue(store.engine, envelope, now=T0).status == "already_queued"
    saved = rows(store, inbox)
    assert len(saved) == 1 and saved[0]["raw_text"] == envelope.raw_text
    assert saved[0]["payload_bytes"] == len(envelope.raw_text.encode())
    assert saved[0]["received_at"] == T0 and saved[0]["attempt_count"] == 0


def test_changed_same_reception_conflicts_without_rewriting_original(store):
    envelope = frame(store)
    assert enqueue(store.engine, envelope, now=T0).status == "queued"
    changed = replace(
        envelope, raw_text=envelope.raw_text.replace('"value": 11', '"value": 12')
    )
    assert enqueue(store.engine, changed, now=T0).status == "conflict"
    assert rows(store, inbox)[0]["raw_text"] == envelope.raw_text
    assert rows(store, audit)[0]["reason"] == "reception_conflict"


@pytest.mark.parametrize("field,value", [("device_id", None), ("warehouse_id", None)])
def test_registration_rechecked_in_admission_transaction(store, field, value):
    envelope = frame(store)
    table = Device.__table__
    with store.engine.begin() as connection:
        if field == "device_id":
            connection.execute(table.delete())
        else:
            connection.execute(table.update().values(warehouse_id=value))
    result = enqueue(store.engine, envelope, now=T0)
    assert result.status == "invalid" and rows(store, inbox) == []


def test_queue_count_limit_includes_leased_and_exhausted_raw(store):
    limits = QueueLimits(max_items=1)
    first = frame(store)
    assert enqueue(store.engine, first, now=T0, limits=limits).status == "queued"
    assert claim_next(store.engine, now=T0) is not None
    assert (
        enqueue(store.engine, frame(store), now=T0, limits=limits).status
        == "queue_full"
    )
    with store.engine.begin() as connection:
        connection.execute(
            inbox.update().values(state="exhausted", lease_id=None, lease_until=None)
        )
    assert (
        enqueue(store.engine, frame(store), now=T0, limits=limits).status
        == "queue_full"
    )


def test_utf8_byte_limit_not_character_count(store):
    envelope = frame(store, extra={"note": "数据" * 10})
    limits = QueueLimits(max_bytes=len(envelope.raw_text.encode()) - 1)
    assert enqueue(store.engine, envelope, now=T0, limits=limits).status == "queue_full"
    assert rows(store, inbox) == []


def test_two_writers_cannot_exceed_shared_capacity(store):
    limits = QueueLimits(max_items=1)
    gate = Barrier(2)
    envelopes = [frame(store), frame(store)]

    def receive(envelope):
        gate.wait(timeout=10)
        return enqueue(store.engine, envelope, now=T0, limits=limits).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(receive, envelopes))
    assert sorted(results) == ["queue_full", "queued"]
    assert len(rows(store, inbox)) == 1


def test_two_concurrent_claims_never_share_a_live_lease(store):
    other = second_device(store)
    for device_store in (store, other):
        assert enqueue(store.engine, frame(device_store), now=T0).status == "queued"
    gate = Barrier(2)

    def claim():
        gate.wait(timeout=10)
        return claim_next(store.engine, now=T0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        claims = [future.result(timeout=15) for future in futures]
    assert all(item is not None for item in claims)
    assert len({item.envelope.reception_id for item in claims}) == 2
    assert all(item.attempt == 1 for item in claims)


def second_device(store):
    other = replace(store, device_id=uuid4())
    with store.engine.begin() as connection:
        connection.execute(
            Device.__table__.insert().values(
                id=other.device_id,
                code="other-inbox-fixture",
                name="Other inbox fixture",
                kind="temperature_sensor",
                warehouse_id=store.warehouse_id,
                created_at=T0,
            )
        )
    return other


def ordered_pair(store):
    first = replace(frame(store), reception_id=UUID(int=1))
    later = replace(frame(store), reception_id=UUID(int=2))
    for envelope in (first, later):
        assert enqueue(store.engine, envelope, now=T0).status == "queued"
    return first, later


def test_same_device_concurrent_claims_only_lease_oldest_unsettled_row(store):
    first, _later = ordered_pair(store)
    gate = Barrier(2)

    def claim():
        gate.wait(timeout=10)
        return claim_next(store.engine, now=T0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        claims = [future.result(timeout=15) for future in futures]
    leased = [item for item in claims if item is not None]
    assert len(leased) == 1
    assert leased[0].envelope == first
    assert len(rows(store, inbox)) == 2


def test_retry_backoff_cannot_be_overtaken_by_later_same_device(store):
    first, later = ordered_pair(store)
    claim = claim_next(store.engine, now=T0)
    assert claim.envelope == first
    assert (
        deliver_claim(
            store.engine,
            claim,
            lambda _e: SinkReceipt("retryable_failure", first.reception_id),
            now=T0,
        ).status
        == "retry_scheduled"
    )
    assert claim_next(store.engine, now=T0 + timedelta(milliseconds=500)) is None
    retry = claim_next(store.engine, now=T0 + timedelta(seconds=1))
    assert retry.envelope == first and retry.attempt == 2
    assert (
        deliver_claim(
            store.engine,
            retry,
            lambda _e: SinkReceipt("accepted_new", first.reception_id, uuid4()),
            now=T0 + timedelta(seconds=1),
        ).status
        == "delivered"
    )
    assert claim_next(store.engine, now=T0 + timedelta(seconds=1)).envelope == later


@pytest.mark.parametrize("state", ["blocked", "exhausted"])
def test_unsettled_failed_predecessor_holds_same_device_not_other_devices(store, state):
    first, _later = ordered_pair(store)
    other = second_device(store)
    other_frame = replace(frame(other), received_at=T0 + timedelta(seconds=2))
    enqueue(store.engine, other_frame, now=T0 + timedelta(seconds=2))
    with store.engine.begin() as connection:
        connection.execute(
            inbox.update()
            .where(
                inbox.c.reception_id == first.reception_id,
            )
            .values(state=state)
        )
    claim = claim_next(store.engine, now=T0 + timedelta(seconds=3))
    assert claim.envelope == other_frame
    assert claim_next(store.engine, now=T0 + timedelta(seconds=3)) is None
    assert len(rows(store, inbox)) == 3
    assert (
        retry_pending(
            store.engine,
            first.reception_id,
            actor_id=store.actor_id,
            now=T0 + timedelta(seconds=3),
        ).status
        == "retry_requested"
    )
    assert claim_next(store.engine, now=T0 + timedelta(seconds=3)).envelope == first


def test_expired_lease_reclaims_predecessor_not_later_row(store):
    first, _later = ordered_pair(store)
    old = claim_next(store.engine, now=T0)
    fresh = create_engine(store.engine.url).execution_options(
        schema_translate_map={None: store.schema}
    )
    try:
        reclaimed = claim_next(fresh, now=T0 + timedelta(seconds=31))
        assert reclaimed.envelope == first and reclaimed.attempt == 2
        assert reclaimed.lease_id != old.lease_id
        assert claim_next(fresh, now=T0 + timedelta(seconds=31)) is None
    finally:
        fresh.dispose()


def test_received_time_precedes_uuid_order_without_cross_device_starvation(store):
    later = replace(
        frame(store), reception_id=UUID(int=1), received_at=T0 + timedelta(seconds=1)
    )
    first = replace(frame(store), reception_id=UUID(int=2))
    enqueue(store.engine, later, now=T0 + timedelta(seconds=1))
    enqueue(store.engine, first, now=T0 + timedelta(seconds=1))
    claim = claim_next(store.engine, now=T0 + timedelta(seconds=1))
    assert claim.envelope == first
    assert claim_next(store.engine, now=T0 + timedelta(seconds=1)) is None


def test_reopened_engine_reclaims_same_envelope_after_lease_timeout(store):
    envelope, old = admit_claim(store)
    assert claim_next(store.engine, now=T0 + timedelta(seconds=29)) is None
    fresh = create_engine(store.engine.url).execution_options(
        schema_translate_map={None: store.schema}
    )
    try:
        recovered = claim_next(fresh, now=T0 + timedelta(seconds=31))
        assert recovered.envelope == envelope and recovered.attempt == 2
        assert recovered.lease_id != old.lease_id
    finally:
        fresh.dispose()


def test_success_releases_capacity_only_with_terminal_metadata_receipt(store):
    envelope, claim = admit_claim(store)
    observation_id = uuid4()
    result = deliver_claim(
        store.engine,
        claim,
        lambda _e: SinkReceipt("accepted_new", envelope.reception_id, observation_id),
        now=T0,
    )
    assert result.status == "delivered" and result.observation_id == observation_id
    assert (
        rows(store, inbox) == []
        and rows(store, receipts)[0]["outcome"] == "accepted_new"
    )
    assert enqueue(store.engine, envelope, now=T0).status == "already_processed"
    assert (
        enqueue(
            store.engine,
            replace(envelope, received_at=T0 + timedelta(seconds=1)),
            now=T0,
        ).status
        == "conflict"
    )


@pytest.mark.parametrize("status", ["accepted_duplicate", "invalid", "conflict"])
def test_terminal_sink_outcomes_are_distinguished_not_all_business_success(
    store, status
):
    envelope, claim = admit_claim(store)
    observation = uuid4() if status.startswith("accepted_") else None
    result = deliver_claim(
        store.engine,
        claim,
        lambda _e: SinkReceipt(status, envelope.reception_id, observation),
        now=T0,
    )
    assert result.status == ("delivered" if status.startswith("accepted_") else status)
    assert rows(store, receipts)[0]["outcome"] == status and rows(store, inbox) == []


def test_no_a_transaction_lock_is_held_across_sink_call(store):
    envelope, claim = admit_claim(store)
    another = frame(store)

    def sink(_envelope):
        assert enqueue(store.engine, another, now=T0).status == "queued"
        return SinkReceipt("accepted_new", envelope.reception_id, uuid4())

    assert deliver_claim(store.engine, claim, sink, now=T0).status == "delivered"
    assert len(rows(store, inbox)) == 1


def test_retry_budget_and_backoff_survive_fresh_connections(store):
    envelope = frame(store)
    assert enqueue(store.engine, envelope, now=T0).status == "queued"
    now = T0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        claim = claim_next(store.engine, now=now)
        assert claim.attempt == attempt and claim.envelope == envelope
        result = deliver_claim(
            store.engine,
            claim,
            lambda _e: SinkReceipt("retryable_failure", envelope.reception_id),
            now=now,
        )
        assert result.status == (
            "exhausted" if attempt == MAX_ATTEMPTS else "retry_scheduled"
        )
        delay = min(30, 2 ** (attempt - 1))
        assert (
            claim_next(store.engine, now=now + timedelta(seconds=delay - 0.1)) is None
        )
        now += timedelta(seconds=delay)
    assert claim_next(store.engine, now=now + timedelta(days=1)) is None
    assert rows(store, inbox)[0]["raw_text"] == envelope.raw_text
    assert rows(store, inbox)[0]["state"] == "exhausted"


def test_crashed_final_attempt_exhausts_instead_of_resetting_budget(store):
    envelope = frame(store)
    enqueue(store.engine, envelope, now=T0)
    with store.engine.begin() as connection:
        connection.execute(inbox.update().values(attempt_count=9))
    assert claim_next(store.engine, now=T0).attempt == 10
    assert claim_next(store.engine, now=T0 + timedelta(seconds=31)) is None
    assert rows(store, inbox)[0]["state"] == "exhausted"


@pytest.mark.parametrize(
    "receipt",
    [
        SinkReceipt("accepted_new", uuid4(), uuid4()),
        SinkReceipt("accepted_new", uuid4()),
        SinkReceipt("unexpected", uuid4()),
    ],
)
def test_bad_receipts_keep_raw_blocked(store, receipt):
    envelope, claim = admit_claim(store)
    result = deliver_claim(store.engine, claim, lambda _e: receipt, now=T0)
    assert result.status == "blocked"
    assert rows(store, inbox)[0]["raw_text"] == envelope.raw_text
    assert rows(store, receipts) == []


def test_accepted_without_observation_identity_is_not_success(store):
    envelope, claim = admit_claim(store)
    result = deliver_claim(
        store.engine,
        claim,
        lambda _e: SinkReceipt("accepted_new", envelope.reception_id),
        now=T0,
    )
    assert result.status == "blocked"


def test_sink_exception_retries_without_storing_exception_text(store):
    _, claim = admit_claim(store)

    def failure(_envelope):
        raise RuntimeError("synthetic private diagnostic must not persist")

    result = deliver_claim(store.engine, claim, failure, now=T0)
    assert result.status == "retry_scheduled" and result.reason == "sink_error"
    assert rows(store, inbox)[0]["last_reason"] == "sink_error"


def test_stale_claim_cannot_call_sink_or_mutate_new_lease(store):
    _, old = admit_claim(store)
    new = claim_next(store.engine, now=T0 + timedelta(seconds=31))
    called = []
    result = deliver_claim(
        store.engine, old, lambda e: called.append(e), now=T0 + timedelta(seconds=31)
    )
    assert result.status == "lease_lost" and called == []
    assert rows(store, inbox)[0]["lease_id"] == new.lease_id


def test_raw_corruption_before_claim_is_blocked(store):
    envelope = frame(store)
    enqueue(store.engine, envelope, now=T0)
    with store.engine.begin() as connection:
        connection.execute(
            inbox.update().values(
                raw_text=envelope.raw_text.replace('"value": 11', '"value": 12')
            )
        )
    assert claim_next(store.engine, now=T0) is None
    assert rows(store, inbox)[0]["state"] == "blocked"


def test_claim_body_tampering_does_not_reach_sink(store):
    envelope, claim = admit_claim(store)
    changed = replace(
        claim,
        envelope=replace(
            envelope, raw_text=envelope.raw_text.replace('"value": 11', '"value": 12')
        ),
    )
    called = []
    result = deliver_claim(store.engine, changed, lambda e: called.append(e), now=T0)
    assert result.status == "blocked" and called == []


def test_a_checkpoint_failure_after_sink_commit_replays_original_identity(store):
    envelope, claim = admit_claim(store)
    external_rows = {}

    def sink(received):
        external_rows.setdefault(received.reception_id, uuid4())
        return SinkReceipt(
            "accepted_new", received.reception_id, external_rows[received.reception_id]
        )

    def fail_checkpoint(_conn, _cursor, statement, _params, _context, _many):
        if "INSERT INTO" in statement and "iot_telemetry_ingest_receipt" in statement:
            raise OperationalError(
                statement, {}, RuntimeError("synthetic lost checkpoint")
            )

    event.listen(store.engine, "before_cursor_execute", fail_checkpoint)
    try:
        assert (
            deliver_claim(store.engine, claim, sink, now=T0).status
            == "retryable_failure"
        )
    finally:
        event.remove(store.engine, "before_cursor_execute", fail_checkpoint)
    assert len(external_rows) == 1 and len(rows(store, inbox)) == 1
    retry = claim_next(store.engine, now=T0 + timedelta(seconds=31))
    assert retry.envelope == envelope
    assert (
        deliver_claim(store.engine, retry, sink, now=T0 + timedelta(seconds=31)).status
        == "delivered"
    )
    assert len(external_rows) == 1 and len(rows(store, receipts)) == 1


def test_manual_retry_requires_live_active_admin_not_stale_actor_claim(store):
    envelope, claim = admit_claim(store)
    deliver_claim(
        store.engine,
        claim,
        lambda _e: SinkReceipt("bad", envelope.reception_id),
        now=T0,
    )
    user_table = User.__table__
    with store.engine.begin() as connection:
        connection.execute(user_table.update().values(is_superuser=False))
    with pytest.raises(PermissionError):
        retry_pending(
            store.engine, envelope.reception_id, actor_id=store.actor_id, now=T0
        )
    with store.engine.begin() as connection:
        connection.execute(
            user_table.update().values(is_superuser=True, is_active=False)
        )
    with pytest.raises(PermissionError):
        retry_pending(
            store.engine, envelope.reception_id, actor_id=store.actor_id, now=T0
        )
    with store.engine.begin() as connection:
        connection.execute(user_table.update().values(is_active=True))
    assert (
        retry_pending(
            store.engine, envelope.reception_id, actor_id=store.actor_id, now=T0
        ).status
        == "retry_requested"
    )
    assert (
        retry_pending(
            store.engine, envelope.reception_id, actor_id=store.actor_id, now=T0
        ).status
        == "already_queued"
    )
    saved = rows(store, inbox)[0]
    assert saved["manual_retry_count"] == 1 and saved["attempt_count"] == 0
    assert (
        saved["raw_text"] == envelope.raw_text
        and rows(store, audit)[0]["actor_id"] == store.actor_id
    )


def test_manual_retry_does_not_reset_live_lease_or_create_missing_frame(store):
    envelope, _claim = admit_claim(store)
    assert (
        retry_pending(
            store.engine, envelope.reception_id, actor_id=store.actor_id, now=T0
        ).status
        == "conflict"
    )
    assert (
        retry_pending(store.engine, uuid4(), actor_id=store.actor_id, now=T0).status
        == "not_found"
    )


def test_audit_and_receipt_metadata_are_bounded_but_pending_raw_is_not_pruned(store):
    limits = QueueLimits(history_records=2)
    for _ in range(3):
        envelope = frame(store)
        enqueue(store.engine, envelope, now=T0)
        claim = claim_next(store.engine, now=T0)
        deliver_claim(
            store.engine,
            claim,
            lambda e: SinkReceipt("accepted_new", e.reception_id, uuid4()),
            now=T0,
            limits=limits,
        )
        assert record_rejection(
            store.engine,
            IngressRejected("invalid_json", b"synthetic raw"),
            device_id=store.device_id,
            now=T0,
            limits=limits,
        )
    assert len(rows(store, receipts)) == len(rows(store, audit)) == 2
    pending = frame(store)
    enqueue(store.engine, pending, now=T0)
    prune_history(store.engine, now=T0 + timedelta(days=8), limits=limits)
    assert rows(store, audit) == rows(store, receipts) == []
    assert rows(store, inbox)[0]["raw_text"] == pending.raw_text


def test_missing_queue_installation_fails_closed_without_self_initializing(store):
    queue_guard.drop(store.engine)
    envelope = frame(store)
    assert enqueue(store.engine, envelope, now=T0).status == "retryable_failure"
    assert not record_rejection(
        store.engine,
        IngressRejected("invalid_json", b"x"),
        device_id=store.device_id,
        now=T0,
    )
    assert rows(store, inbox) == []


@pytest.mark.parametrize("value", [True, 0, -1, 1001])
def test_invalid_capacity_configuration_rejected(value):
    with pytest.raises(ValueError):
        QueueLimits(max_items=value)
