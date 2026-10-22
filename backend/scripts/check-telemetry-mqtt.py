"""Disposable MQTT -> actual A registry -> pinned C transaction/snapshot proof.

The callback below is a test harness, not a durable production MQTT worker.
C candidate tables are created only after refusing every non-test database.
"""

import csv
import json
import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt
from fastapi.testclient import TestClient
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.subscribeoptions import SubscribeOptions
from sqlalchemy import event, func
from sqlalchemy import select as core_select
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.main import app
from app.modules.iot.ingress import IngressRejected
from app.modules.iot.models import Device
from app.modules.iot.proof_schema_guard import require_b01_candidate_schema
from app.modules.iot.registry import receive_registered_telemetry
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session

# C exists only in the disposable proof overlay, not this A source tree.
# isort: split
from app.modules.telemetry.history_candidate import HistoryFiltersCandidate
from app.modules.telemetry.snapshot_candidate import (
    create_snapshot_candidate,
    read_snapshot_page_candidate,
    render_snapshot_csv_candidate,
)
from app.modules.telemetry.storage_candidate import (
    evaluation_jobs,
    ingest_observation,
    metadata,
    observations,
    receptions,
)

SENSOR = UUID("11111111-1111-4111-8111-111111111111")
TOPIC = f"freshlink/v1/devices/{SENSOR}/telemetry"


def run():
    logging.getLogger("httpx").setLevel(logging.WARNING)
    database = urlparse(str(settings.DATABASE_URL))
    if (
        database.hostname != "test-db"
        or database.path != "/freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
    ):
        raise RuntimeError("Refusing non-disposable database")
    target = Path("/results/mqtt-telemetry.json")
    if target.exists():
        raise RuntimeError("Refusing evidence overwrite")
    source_revision = os.environ["TELEMETRY_C_REVISION"]
    if source_revision != "58f6bf76710bbbe68cde94dafea65bdf30232be2":
        raise RuntimeError("C source must match this proof's pinned revision")
    require_b01_candidate_schema(engine)
    # C-only candidate metadata; never called by application startup.
    metadata.create_all(engine)
    with Session(engine) as session:
        assert session.get(Device, SENSOR) is None
        session.add(
            Device(
                id=SENSOR,
                code="telemetry-proof-sensor",
                name="Isolated telemetry proof",
                kind="temperature_sensor",
            )
        )
        session.commit()

    subscribed = threading.Event()
    connected = threading.Event()
    lock = threading.Lock()
    delivered = []
    rejected = []
    pending = []
    denied = []
    failures = []
    acked_receipts = []
    started_at = datetime.now(UTC)

    def count(table):
        with engine.connect() as connection:
            return connection.scalar(core_select(func.count()).select_from(table))

    def wait_until(predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if failures:
                raise AssertionError("Callback failure: " + failures[0])
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError("Telemetry integration checkpoint timed out")

    def ack_committed(client, envelope, message):
        with engine.connect() as connection:
            persisted = connection.execute(
                core_select(receptions.c.id).where(
                    receptions.c.id == envelope.reception_id
                )
            ).scalar_one()
        assert persisted == envelope.reception_id
        assert client.ack(message.mid, message.qos) == mqtt.MQTT_ERR_SUCCESS
        acked_receipts.append(str(persisted))

    subscriber = mqtt.Client(
        CallbackAPIVersion.VERSION2,
        client_id="telemetry-proof-consumer",
        protocol=mqtt.MQTTv5,
        manual_ack=True,
    )
    subscriber.username_pw_set("test-controller", "fixture-controller-only")

    def on_connect(client, _data, _flags, reason, _properties):
        if reason.is_failure:
            failures.append("subscriber_authentication_rejected")
            return
        client.subscribe(TOPIC, options=SubscribeOptions(qos=1, retainAsPublished=True))

    def on_subscribe(_client, _data, _mid, reasons, _properties):
        if len(reasons) == 1 and not reasons[0].is_failure:
            subscribed.set()
        else:
            failures.append("subscription_rejected")

    def on_message(client, _data, message):
        try:
            if message.retain:
                raise IngressRejected("retained_telemetry", message.payload)
            if message.qos != 1:
                raise IngressRejected("unexpected_qos", message.payload)
            with Session(engine) as session:
                envelope = receive_registered_telemetry(
                    session,
                    authenticated_device_id=SENSOR,
                    credential_enabled=True,
                    warehouse_exists=warehouse_lookup_for_session(session),
                    topic=message.topic,
                    payload=message.payload,
                    received_at=datetime.now(UTC),
                )
            # Registration read session is closed before C starts its transaction.
            result = ingest_observation(engine, envelope)
            with lock:
                if result.status == "retryable_failure":
                    pending.append((envelope, message, result))
                else:
                    ack_committed(client, envelope, message)
                    delivered.append((envelope, result))
        except IngressRejected as error:
            # Test-only bounded rejection list, not a persistent production audit.
            with lock:
                rejected.append(error.reason_code)
            client.ack(message.mid, message.qos)
        except Exception as error:
            failures.append(type(error).__name__)

    subscriber.on_connect = on_connect
    subscriber.on_subscribe = on_subscribe
    subscriber.on_message = on_message
    subscriber.on_publish = lambda _c, _d, _m, reason, _p: (
        denied.append(reason.value) if reason.is_failure else None
    )
    publisher = mqtt.Client(
        CallbackAPIVersion.VERSION2,
        client_id="telemetry-proof-sensor",
        protocol=mqtt.MQTTv5,
    )
    publisher.username_pw_set("test-sensor", "fixture-sensor-only")
    publisher.on_connect = lambda _c, _d, _f, reason, _p: (
        connected.set() if not reason.is_failure else None
    )

    def received_count():
        with lock:
            return len(delivered) + len(rejected) + len(pending)

    def send(payload, *, retained=False):
        before = received_count()
        publisher.publish(TOPIC, payload, qos=1, retain=retained).wait_for_publish(5)
        wait_until(lambda: received_count() > before)

    def packet(value, *, message_id=None, sampled_at=None, unit="°C", device=SENSOR):
        return json.dumps(
            {
                "schema_version": "1.0",
                "device_id": str(device),
                "message_id": str(message_id or uuid4()),
                "sampled_at": sampled_at
                or (started_at - timedelta(seconds=60)).isoformat(),
                "received_at": "2000-01-01T00:00:00Z",
                "metrics": [
                    {"key": "temperature", "value": value, "unit": unit},
                    {"key": "humidity", "value": 70, "unit": "%RH"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

    try:
        with TestClient(app) as api:
            login = api.post(
                "/api/v1/login/access-token",
                data={
                    "username": settings.FIRST_SUPERUSER,
                    "password": settings.FIRST_SUPERUSER_PASSWORD,
                },
            )
            assert login.status_code == 200
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            actor = api.get("/api/v1/users/me", headers=headers)
            assert actor.status_code == 200
            actor_id = UUID(actor.json()["id"])
            warehouse = api.post(
                "/api/v1/warehouses/",
                headers=headers,
                json={"code": "telemetry-proof", "name": "Isolated real B warehouse"},
            )
            assert warehouse.status_code == 201
            warehouse_id = UUID(warehouse.json()["id"])
            binding = api.post(
                f"/api/v1/devices/{SENSOR}/binding",
                headers=headers,
                json={"warehouse_id": str(warehouse_id)},
            )
            assert binding.status_code == 200
            subscriber.connect("broker", 1883)
            subscriber.loop_start()
            wait_until(subscribed.is_set)
            publisher.connect("broker", 1883)
            publisher.loop_start()
            wait_until(connected.is_set)

            message_id = uuid4()
            first_raw = packet(11, message_id=message_id)
            send(first_raw)
            envelope, first = delivered[-1]
            assert first.status == "accepted_new"
            assert envelope.warehouse_id == warehouse_id
            assert envelope.received_at >= started_at
            assert ingest_observation(engine, envelope) == first
            send(first_raw)
            assert delivered[-1][1].status == "accepted_duplicate"
            assert delivered[-1][1].observation_id == first.observation_id
            send(packet(12, message_id=message_id))
            assert delivered[-1][1].status == "conflict"
            assert count(observations) == 1 and count(receptions) == 3
            send(packet(11, unit="K"))
            bad_unit_id = delivered[-1][1].observation_id
            send(packet(11, sampled_at="not-a-time"))
            bad_time_id = delivered[-1][1].observation_id
            send(packet(7, sampled_at=(started_at - timedelta(seconds=20)).isoformat()))
            send(
                packet(15, sampled_at=(started_at - timedelta(seconds=90)).isoformat())
            )
            late_id = delivered[-1][1].observation_id
            assert count(observations) == 5
            with engine.connect() as connection:
                rows = {
                    row["id"]: row
                    for row in connection.execute(core_select(observations)).mappings()
                }
                assert rows[first.observation_id]["raw_text"] == first_raw.decode()
                assert rows[first.observation_id]["first_received_at_utc"] >= started_at
                for identity in (bad_unit_id, bad_time_id):
                    assert rows[identity]["risk"]["state"] == "unknown"
                    assert rows[identity]["quality"] != ["ok"]
                    job = connection.execute(
                        core_select(evaluation_jobs.c.state).where(
                            evaluation_jobs.c.observation_id == identity
                        )
                    ).scalar_one()
                    assert job == "held_unknown"
                assert rows[late_id]["normalized"]["metrics"]["temperature"] == 15

            # The controller is read-only on telemetry; topic identity alone is not auth.
            before = received_count()
            subscriber.publish(TOPIC, packet(19), qos=1).wait_for_publish(5)
            wait_until(lambda: bool(denied))
            assert received_count() == before and count(observations) == 5
            send(packet(19, device=uuid4()))
            send(b'{"schema_version":')
            send(packet(19), retained=True)
            assert sorted(rejected) == [
                "identity_mismatch",
                "invalid_json",
                "retained_telemetry",
            ]
            assert count(observations) == 5

            filters = HistoryFiltersCandidate(
                device_id=SENSOR, warehouse_id=warehouse_id
            )
            query_time = datetime.now(UTC)
            snapshot = create_snapshot_candidate(
                engine,
                actor_id=actor_id,
                filters=filters,
                policy_version="active-user-read-v1",
                now=query_time,
            )
            before_counts = (
                count(observations),
                count(receptions),
                count(evaluation_jobs),
            )
            injected = False

            def fail_evaluation(_conn, _cursor, statement, _params, _context, _many):
                nonlocal injected
                if (
                    not injected
                    and "INSERT INTO" in statement
                    and "telemetry_evaluation_candidate" in statement
                ):
                    injected = True
                    raise OperationalError(
                        statement, {}, RuntimeError("synthetic C outage")
                    )

            event.listen(engine, "before_cursor_execute", fail_evaluation)
            try:
                send(packet(13))
            finally:
                event.remove(engine, "before_cursor_execute", fail_evaluation)
            assert injected and len(pending) == 1
            assert (
                count(observations),
                count(receptions),
                count(evaluation_jobs),
            ) == before_counts
            retry_envelope, retry_message, failed = pending[0]
            assert failed.status == "retryable_failure"
            assert str(retry_envelope.reception_id) not in acked_receipts
            recovered = ingest_observation(engine, retry_envelope)
            assert recovered.status == "accepted_new"
            ack_committed(subscriber, retry_envelope, retry_message)
            assert ingest_observation(engine, retry_envelope) == recovered
            assert count(observations) == 6 and count(receptions) == 8

            def snapshot_ids(identity):
                parameters = {
                    "engine": engine,
                    "snapshot_id": identity,
                    "actor_id": actor_id,
                    "filters": filters,
                    "policy_version": "active-user-read-v1",
                    "now": query_time,
                }
                items = []
                ordinal = -1
                while True:
                    page = read_snapshot_page_candidate(
                        **parameters, limit=2, after_ordinal=ordinal
                    )
                    items.extend(item["observation_id"] for item in page.items)
                    if page.next_after_ordinal is None:
                        assert len(items) == page.total
                        break
                    ordinal = page.next_after_ordinal
                exported = render_snapshot_csv_candidate(**parameters)
                csv_ids = [
                    row["observation_id"]
                    for row in csv.DictReader(StringIO(exported.decode("utf-8-sig")))
                ]
                assert csv_ids == items
                return items

            old_ids = snapshot_ids(snapshot)
            assert len(old_ids) == 5 and str(recovered.observation_id) not in old_ids
            fresh = create_snapshot_candidate(
                engine,
                actor_id=actor_id,
                filters=filters,
                policy_version="active-user-read-v1",
                now=query_time,
            )
            assert len(snapshot_ids(fresh)) == 6
            assert str(recovered.observation_id) in snapshot_ids(fresh)
            assert len(acked_receipts) == 8 and not failures
            target.write_text(
                json.dumps(
                    {
                        "phase": "live_mqtt_a_registry_c_transaction_snapshot_passed",
                        "checked_at": datetime.now(UTC).isoformat(),
                        "a_revision": os.environ["TELEMETRY_A_REVISION"],
                        "a_worktree_dirty": os.environ["TELEMETRY_A_DIRTY"] == "1",
                        "c_revision": source_revision,
                        "b_schema_mode": "actual_alembic_b01_candidate_not_published",
                        "observation_count": 6,
                        "reception_count": 8,
                        "mqtt_receipts_acknowledged_after_commit": 8,
                        "actual_b_warehouse_and_a_binding": True,
                        "exact_raw_and_trusted_received_at_preserved": True,
                        "same_envelope_retry_stable": True,
                        "network_duplicate_and_conflict_preserved": True,
                        "invalid_quality_held_unknown": True,
                        "late_true_high_retained": True,
                        "controller_publish_denied": True,
                        "rejections": rejected,
                        "c_failure_rolled_back_without_mqtt_ack": True,
                        "same_snapshot_page_total_csv_equal": True,
                        "old_snapshot_excludes_retry_commit_new_snapshot_sees_it": True,
                        "not_tested": [
                            "durable production MQTT inbox/retry scheduling",
                            "C formal migration/application HTTP/CSV download",
                            "B rule evaluation/alerts/real notifications",
                            "browser",
                            "E1",
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
    finally:
        publisher.disconnect()
        publisher.loop_stop()
        subscriber.disconnect()
        subscriber.loop_stop()


if __name__ == "__main__":
    run()
