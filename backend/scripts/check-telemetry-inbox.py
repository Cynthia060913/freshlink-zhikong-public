"""Actual receiver + durable A queue + pinned C, in a disposable proof only.

Runs after check-telemetry-mqtt.py in the same guarded, owned test project.
No real external recipient, host port, production DB or official migration.
"""

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
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.db import engine
from app.modules.iot.heartbeat_worker import HeartbeatWorkerSettings
from app.modules.iot.proof_schema_guard import require_a04_candidate_schema
from app.modules.iot.telemetry_inbox_models import (
    inbox,
    receipts,
)
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


def run():
    database = urlparse(str(settings.DATABASE_URL))
    if (
        database.hostname != "test-db"
        or database.path != "/freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
        or settings.FASTAPI_ENV != "development"
        or os.environ["TELEMETRY_C_REVISION"]
        != "58f6bf76710bbbe68cde94dafea65bdf30232be2"
    ):
        raise RuntimeError("Refusing unpinned/non-disposable proof")
    target = Path("/results/mqtt-telemetry-inbox.json")
    if target.exists():
        raise RuntimeError("Refusing evidence overwrite")
    require_a04_candidate_schema(engine)

    settings_mqtt = HeartbeatWorkerSettings(
        host="broker",
        port=1883,
        tls=False,
        isolated_test_plaintext=True,
        acl_confirmed=True,
        device_ids=[SENSOR],
        username="test-controller",
        client_id="a08-persistent-receiver-proof",
    )
    acknowledged, failures = [], []
    receiver = None
    receiver_thread = None

    def wait_until(predicate, seconds=12):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if failures:
                raise AssertionError("Receiver failed: " + failures[0])
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError("Inbox checkpoint timed out")

    def rows(table):
        with engine.connect() as connection:
            return list(connection.execute(select(table)).mappings())

    def count(table):
        with engine.connect() as connection:
            return connection.scalar(select(func.count()).select_from(table))

    def start_receiver():
        nonlocal receiver, receiver_thread
        receiver = TelemetryReceiver(
            settings_mqtt,
            password="fixture-controller-only",
            warehouse_lookup_factory=warehouse_lookup_for_session,
        )
        original_ack = receiver.client.ack

        def committed_ack(mid, qos):
            saved = rows(inbox)
            assert saved  # Real second connection sees committed A raw first.
            acknowledged.append(saved[-1]["reception_id"])
            return original_ack(mid, qos)

        receiver.client.ack = committed_ack

        def serving():
            try:
                receiver.run(max_seconds=100)
            except Exception as error:
                failures.append(type(error).__name__)

        receiver_thread = threading.Thread(target=serving)
        receiver_thread.start()
        wait_until(receiver.ready.is_set)

    def stop_receiver():
        if receiver is not None:
            receiver.stop_requested.set()
        if receiver_thread is not None:
            receiver_thread.join(8)
            assert not receiver_thread.is_alive()

    publisher = mqtt.Client(
        CallbackAPIVersion.VERSION2, client_id="a08-proof-sensor", protocol=mqtt.MQTTv5
    )
    publisher.username_pw_set("test-sensor", "fixture-sensor-only")
    connected = threading.Event()
    publisher.on_connect = lambda _c, _d, _f, reason, _p: (
        connected.set() if not reason.is_failure else None
    )
    # Separate A delivery engine; no shared Session or A lock across C call.
    delivery_engine = create_engine(
        engine.url, pool_pre_ping=True, connect_args={"connect_timeout": 5}
    )

    def sink(envelope):
        return ingest_observation(engine, envelope)

    initial = count(observations), count(receptions)

    def send(value):
        raw = json.dumps(
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
        previous = len(acknowledged)
        publisher.publish(TOPIC, raw, qos=1).wait_for_publish(5)
        wait_until(lambda: len(acknowledged) == previous + 1)
        saved = rows(inbox)
        assert len(saved) == 1 and saved[0]["raw_text"] == raw.decode()
        assert saved[0]["received_at"].tzinfo is not None
        return saved[0]

    try:
        publisher.connect("broker", 1883)
        publisher.loop_start()
        wait_until(connected.is_set)
        # Remove A07's deliberately retained test packet before this subscription.
        publisher.publish(TOPIC, b"", qos=1, retain=True).wait_for_publish(5)
        start_receiver()
        first = send(12)
        assert (count(observations), count(receptions)) == initial
        # Receiver exits; no in-memory pending envelope is passed to delivery.
        stop_receiver()
        start_receiver()
        assert rows(inbox)[0]["reception_id"] == first["reception_id"]
        delivery = TelemetryDeliveryWorker(delivery_engine, sink)
        injected = False

        def c_failure(_conn, _cursor, statement, _params, _context, _many):
            nonlocal injected
            if (
                "INSERT INTO" in statement
                and "telemetry_evaluation_candidate" in statement
            ):
                injected = True
                raise OperationalError(
                    statement, {}, RuntimeError("synthetic C outage")
                )

        event.listen(engine, "before_cursor_execute", c_failure)
        try:
            assert delivery.run_once().status == "retry_scheduled"
        finally:
            event.remove(engine, "before_cursor_execute", c_failure)
        assert injected and (count(observations), count(receptions)) == initial
        assert rows(inbox)[0]["attempt_count"] == 1
        assert rows(inbox)[0]["reception_id"] == first["reception_id"]
        # Fresh worker object reads the already-committed retry timestamp/budget.
        delivery = TelemetryDeliveryWorker(delivery_engine, sink)
        time.sleep(1.1)
        assert delivery.run_once().status == "delivered"
        assert count(inbox) == 0 and count(receipts) == 1
        with engine.connect() as connection:
            c_row = (
                connection.execute(
                    select(receptions).where(receptions.c.id == first["reception_id"])
                )
                .mappings()
                .one()
            )
            assert c_row["raw_text"] == first["raw_text"]

        second = send(13)

        def lose_a_checkpoint(_conn, _cursor, statement, _params, _context, _many):
            if (
                "INSERT INTO" in statement
                and "iot_telemetry_ingest_receipt" in statement
            ):
                raise OperationalError(
                    statement, {}, RuntimeError("synthetic A checkpoint failure")
                )

        event.listen(delivery_engine, "before_cursor_execute", lose_a_checkpoint)
        try:
            assert delivery.run_once().status == "retryable_failure"
        finally:
            event.remove(delivery_engine, "before_cursor_execute", lose_a_checkpoint)
        after_c_commit = count(observations), count(receptions)
        assert after_c_commit == (initial[0] + 2, initial[1] + 2)
        assert rows(inbox)[0]["reception_id"] == second["reception_id"]
        assert rows(inbox)[0]["state"] == "leased"
        # Real lease expiry, not clock injection. Restart only the receiver;
        # PG/broker remain the disposable services, never the user's app DB.
        stop_receiver()
        delivery_engine.dispose()
        delivery_engine = create_engine(engine.url, connect_args={"connect_timeout": 5})
        delivery = TelemetryDeliveryWorker(delivery_engine, sink)
        assert delivery.run_once() is None
        time.sleep(31)
        assert delivery.run_once().status == "delivered"
        assert (count(observations), count(receptions)) == after_c_commit
        assert count(inbox) == 0 and count(receipts) == 2
        assert len(acknowledged) == 2 and not failures
        target.write_text(
            json.dumps(
                {
                    "phase": "durable_receiver_a_queue_c_recovery_passed",
                    "checked_at": datetime.now(UTC).isoformat(),
                    "a_revision": os.environ["TELEMETRY_A_REVISION"],
                    "a_worktree_dirty": os.environ["TELEMETRY_A_DIRTY"] == "1",
                    "c_revision": os.environ["TELEMETRY_C_REVISION"],
                    "ack_after_a_commit_before_c": True,
                    "receiver_restart_original_raw_id": True,
                    "c_rollback_retry_same_identity": True,
                    "a_checkpoint_lost_c_commit_no_duplicate": True,
                    "actual_lease_expiry_wait_seconds": 31,
                    "new_c_observations": 2,
                    "a_terminal_receipts": 2,
                    "pending_raw": 0,
                    "not_tested": [
                        "formal shared migrations",
                        "database or broker restart",
                        "authenticated history HTTP/CSV UI",
                        "real notifications",
                        "full E1",
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        stop_receiver()
        publisher.disconnect()
        publisher.loop_stop()
        delivery_engine.dispose()


if __name__ == "__main__":
    run()
