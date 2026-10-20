"""Two-process PG/MQTT container-recreation proof, not application startup.

prepare persists an A lease, then queues one message for an offline subscriber.
resume must work after the caller replaces BOTH containers, keeping ONLY their
explicit synthetic named volumes. No new publisher runs in resume.
"""

import hashlib
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from sqlalchemy import func, select, text

from app.core.config import settings
from app.core.db import engine
from app.modules.iot.heartbeat_worker import HeartbeatWorkerSettings
from app.modules.iot.telemetry_inbox import claim_next
from app.modules.iot.telemetry_inbox_models import inbox, receipts
from app.modules.iot.telemetry_worker import TelemetryDeliveryWorker, TelemetryReceiver
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session

# isort: split
from app.modules.telemetry.storage_candidate import (
    ingest_observation,
    observations,
    receptions,
)

SENSOR = UUID("11111111-1111-4111-8111-111111111111")
TOPIC = f"freshlink/v1/devices/{SENSOR}/telemetry"
PREPARED = Path("/results/restart-prepared.json")
FINAL = Path("/results/restart-resumed.json")


def rows(table):
    with engine.connect() as connection:
        return list(connection.execute(select(table)).mappings())


def count(table):
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(table))


def boot_time():
    with engine.connect() as connection:
        return connection.scalar(text("SELECT pg_postmaster_start_time()"))


def write_once(path, value):
    with path.open("x", encoding="utf-8") as target:
        json.dump(value, target, indent=2)


def packet(value):
    return json.dumps(
        {
            "schema_version": "1.0",
            "device_id": str(SENSOR),
            "message_id": str(uuid4()),
            "sampled_at": datetime.now(UTC).isoformat(),
            "metrics": [{"key": "temperature", "value": value, "unit": "°C"}],
        },
        ensure_ascii=False,
        indent=2,
    ).encode()


def run():
    database = urlparse(str(settings.DATABASE_URL))
    stage = os.getenv("TELEMETRY_RESTART_STAGE")
    if (
        database.hostname != "test-db"
        or database.path != "/freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
        or settings.FASTAPI_ENV != "development"
        or os.environ["TELEMETRY_C_REVISION"]
        != "6a29ecf3c43b0df41d1b2e8ba19f1bf34f634aa1"
        or stage not in {"prepare", "resume"}
    ):
        raise RuntimeError("Refusing unpinned/non-disposable restart proof")
    if FINAL.exists() or (stage == "prepare" and PREPARED.exists()):
        raise RuntimeError("Refusing evidence overwrite")
    if stage == "resume" and not PREPARED.exists():
        raise RuntimeError("No persisted preparation record")

    config = HeartbeatWorkerSettings(
        host="broker",
        port=1883,
        tls=False,
        isolated_test_plaintext=True,
        acl_confirmed=True,
        device_ids=[SENSOR],
        username="test-controller",
        client_id="a09-restart-proof-receiver",
    )
    receiver = TelemetryReceiver(
        config,
        password="fixture-controller-only",
        warehouse_lookup_factory=warehouse_lookup_for_session,
    )
    failures, session_flags, acked = [], [], []
    original_connect, original_ack = receiver._on_connect, receiver.client.ack

    def on_connect(client, data, flags, reason, properties):
        session_flags.append(bool(flags.session_present))
        original_connect(client, data, flags, reason, properties)

    def on_ack(mid, qos):
        assert count(inbox) > 0  # A second DB connection sees durable admission.
        acked.append(mid)
        return original_ack(mid, qos)

    receiver.client.on_connect = on_connect
    receiver.client.ack = on_ack

    def serving():
        try:
            receiver.run(max_seconds=100)
        except Exception as error:
            failures.append(type(error).__name__)

    thread = threading.Thread(target=serving)

    def wait_until(predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if failures:
                raise AssertionError("Receiver failure: " + failures[0])
            if predicate():
                return
            time.sleep(0.1)
        raise AssertionError("Restart checkpoint timed out")

    def stop_receiver():
        receiver.stop_requested.set()
        if thread.ident is not None:
            thread.join(8)
            assert not thread.is_alive()
        else:
            receiver.engine.dispose()

    publisher = None
    try:
        if stage == "prepare":
            assert rows(inbox) == []
            initial_counts = [count(observations), count(receptions), count(receipts)]
            thread.start()
            wait_until(receiver.ready.is_set)
            assert session_flags == [False]
            publisher = mqtt.Client(
                CallbackAPIVersion.VERSION2,
                client_id="a09-proof-sensor",
                protocol=mqtt.MQTTv5,
            )
            publisher.username_pw_set("test-sensor", "fixture-sensor-only")
            connected = threading.Event()
            publisher.on_connect = lambda _c, _d, _f, reason, _p: (
                connected.set() if not reason.is_failure else None
            )
            publisher.connect("broker", 1883)
            publisher.loop_start()
            wait_until(connected.is_set)
            first_raw = packet(14)
            publisher.publish(TOPIC, first_raw, qos=1).wait_for_publish(5)
            wait_until(lambda: len(acked) == 1)
            claim = claim_next(engine)
            assert claim is not None and claim.attempt == 1
            assert claim.envelope.raw_text == first_raw.decode()
            stop_receiver()
            # Offline durable subscription is retained. PUBACK alone is only a
            # preparation observation; resume proves whether delivery survives.
            second_raw = packet(15)
            publisher.publish(TOPIC, second_raw, qos=1).wait_for_publish(5)
            assert len(rows(inbox)) == 1
            assert [
                count(observations),
                count(receptions),
                count(receipts),
            ] == initial_counts
            pending = rows(inbox)[0]
            write_once(
                PREPARED,
                {
                    "phase": "restart_prepared",
                    "a_revision": os.environ["TELEMETRY_A_REVISION"],
                    "a_worktree_dirty": os.environ["TELEMETRY_A_DIRTY"] == "1",
                    "c_revision": os.environ["TELEMETRY_C_REVISION"],
                    "pg_started_at": boot_time().isoformat(),
                    "initial_counts": initial_counts,
                    "leased_reception_id": str(claim.envelope.reception_id),
                    "leased_received_at": claim.envelope.received_at.isoformat(),
                    "leased_raw_sha256": hashlib.sha256(first_raw).hexdigest(),
                    "lease_id": str(pending["lease_id"]),
                    "lease_until": pending["lease_until"].isoformat(),
                    "offline_raw_sha256": hashlib.sha256(second_raw).hexdigest(),
                    "offline_message_id": json.loads(second_raw)["message_id"],
                    "not_yet_proved": "DB/container volume and offline MQTT survival",
                },
            )
        else:
            prepared = json.loads(PREPARED.read_text(encoding="utf-8"))
            assert prepared["phase"] == "restart_prepared"
            assert prepared["a_revision"] == os.environ["TELEMETRY_A_REVISION"]
            assert prepared["c_revision"] == os.environ["TELEMETRY_C_REVISION"]
            assert prepared["pg_started_at"] != boot_time().isoformat()
            persisted = rows(inbox)
            assert len(persisted) == 1
            first = persisted[0]
            assert str(first["reception_id"]) == prepared["leased_reception_id"]
            assert first["received_at"].isoformat() == prepared["leased_received_at"]
            assert first["payload_sha256"] == prepared["leased_raw_sha256"]
            assert str(first["lease_id"]) == prepared["lease_id"]
            assert first["lease_until"].isoformat() == prepared["lease_until"]
            assert first["state"] == "leased" and first["attempt_count"] == 1
            assert [
                count(observations),
                count(receptions),
                count(receipts),
            ] == prepared["initial_counts"]
            thread.start()
            wait_until(receiver.ready.is_set)
            wait_until(lambda: len(acked) == 1 and len(rows(inbox)) == 2)
            assert session_flags == [True]  # Broker restored the offline session.
            second = next(
                row
                for row in rows(inbox)
                if str(row["reception_id"]) != prepared["leased_reception_id"]
            )
            assert second["payload_sha256"] == prepared["offline_raw_sha256"]
            assert (
                json.loads(second["raw_text"])["message_id"]
                == prepared["offline_message_id"]
            )
            stop_receiver()
            attempts = {}

            def sink(envelope):
                pending = next(
                    row
                    for row in rows(inbox)
                    if row["reception_id"] == envelope.reception_id
                )
                attempts[str(envelope.reception_id)] = pending["attempt_count"]
                return ingest_observation(engine, envelope)

            worker = TelemetryDeliveryWorker(engine, sink)

            def drained():
                worker.run_once()
                return count(inbox) == 0

            wait_until(drained, timeout=45)
            assert attempts[prepared["leased_reception_id"]] == 2
            assert attempts[str(second["reception_id"])] == 1
            before = prepared["initial_counts"]
            assert [count(observations), count(receptions), count(receipts)] == [
                n + 2 for n in before
            ]
            with engine.connect() as connection:
                for source_id, digest in (
                    (
                        UUID(prepared["leased_reception_id"]),
                        prepared["leased_raw_sha256"],
                    ),
                    (second["reception_id"], prepared["offline_raw_sha256"]),
                ):
                    raw = connection.scalar(
                        select(receptions.c.raw_text).where(
                            receptions.c.id == source_id
                        )
                    )
                    assert hashlib.sha256(raw.encode()).hexdigest() == digest
            assert not failures
            write_once(
                FINAL,
                {
                    "phase": "pg_broker_container_recreation_recovery_passed",
                    "checked_at": datetime.now(UTC).isoformat(),
                    "a_revision": os.environ["TELEMETRY_A_REVISION"],
                    "a_worktree_dirty": os.environ["TELEMETRY_A_DIRTY"] == "1",
                    "c_revision": os.environ["TELEMETRY_C_REVISION"],
                    "pg_boot_changed": True,
                    "pg_original_raw_id_lease_budget_preserved": True,
                    "broker_session_present": True,
                    "offline_mqtt_packet_survived_broker_replacement": True,
                    "previous_claim_resumed_at_attempt": 2,
                    "new_c_observations": 2,
                    "new_a_terminal_receipts": 2,
                    "pending_raw": 0,
                    "not_tested": [
                        "abrupt power loss",
                        "filesystem/disk failure",
                        "session expiry",
                        "production migration/deployment",
                        "real notifications",
                        "full E1",
                    ],
                },
            )
    finally:
        stop_receiver()
        if publisher is not None:
            publisher.disconnect()
            publisher.loop_stop()


if __name__ == "__main__":
    run()
