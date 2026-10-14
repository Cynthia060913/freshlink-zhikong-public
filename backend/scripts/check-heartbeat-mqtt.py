"""Live authenticated broker -> registered device -> PostgreSQL -> API proof.

No production data, C/B calls, external notifications or host ports.
"""

import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt
from fastapi.testclient import TestClient
from paho.mqtt.enums import CallbackAPIVersion
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.main import app
from app.modules.iot.heartbeat import HeartbeatPolicy
from app.modules.iot.heartbeat_worker import HeartbeatWorker, HeartbeatWorkerSettings
from app.modules.iot.models import Device

SENSOR = UUID("11111111-1111-4111-8111-111111111111")
FOREIGN = UUID("66666666-6666-4666-8666-666666666666")


def wait_until(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Live heartbeat checkpoint timed out")


def run() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    database = urlparse(str(settings.DATABASE_URL))
    if (
        database.hostname != "test-db"
        or database.path != "/freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
    ):
        raise RuntimeError("Refusing non-disposable database")
    target = Path("/results/mqtt-heartbeat.json")
    if target.exists():
        raise RuntimeError("Refusing to overwrite previous evidence")
    with Session(engine) as session:
        assert session.get(Device, SENSOR) is None
        session.add(
            Device(
                id=SENSOR,
                code="mqtt-hb-fixture",
                name="Live test only",
                kind="temperature_sensor",
            )
        )
        session.commit()
    worker = HeartbeatWorker(
        HeartbeatWorkerSettings.model_validate(
            {
                "host": "broker",
                "port": 1883,
                "tls": False,
                "isolated_test_plaintext": True,
                "username": "test-controller",
                "client_id": "isolated-heartbeat-worker",
                "acl_confirmed": True,
                "device_ids": [str(SENSOR)],
                "scan_period_seconds": 0.1,
            }
        ),
        password="fixture-controller-only",
        policy=HeartbeatPolicy(offline_after_seconds=3),
    )
    failures = []

    def receiver():
        try:
            worker.run(max_seconds=30)
        except Exception as error:
            failures.append(type(error).__name__)

    thread = threading.Thread(target=receiver)
    publisher = mqtt.Client(
        CallbackAPIVersion.VERSION2,
        client_id="isolated-heartbeat-publisher",
        protocol=mqtt.MQTTv5,
    )
    publisher.username_pw_set("test-sensor", "fixture-sensor-only")
    connected = threading.Event()
    denied = []
    publisher.on_connect = lambda _c, _d, _f, r, _p: (
        connected.set() if not r.is_failure else None
    )
    publisher.on_publish = lambda _c, _d, _m, r, _p: (
        denied.append(r.value) if r.is_failure else None
    )
    thread.start()
    impostor = None
    try:
        wait_until(worker.ready.is_set)
        publisher.connect("broker", 1883)
        publisher.loop_start()
        wait_until(connected.is_set)
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

            def detail():
                response = api.get(f"/api/v1/devices/{SENSOR}", headers=headers)
                assert response.status_code == 200
                return response.json()

            def events():
                response = api.get(
                    f"/api/v1/devices/{SENSOR}/connectivity-events", headers=headers
                )
                assert response.status_code == 200
                return response.json()["data"]

            assert detail()["connectivity"] == "unknown"
            topic = f"freshlink/v1/devices/{SENSOR}/heartbeat"

            def body(identity=SENSOR):
                return json.dumps(
                    {
                        "schema_version": "1.0",
                        "device_id": str(identity),
                        "message_id": str(uuid4()),
                        "sent_at": datetime.now(UTC).isoformat(),
                    }
                ).encode()

            # Broker service account is read-only on heartbeat topics too.
            impostor = mqtt.Client(
                CallbackAPIVersion.VERSION2,
                client_id="isolated-readonly-check",
                protocol=mqtt.MQTTv5,
            )
            impostor.username_pw_set("test-controller", "fixture-controller-only")
            service_ready = threading.Event()
            service_denied = []
            impostor.on_connect = lambda _c, _d, _f, r, _p: (
                service_ready.set() if not r.is_failure else None
            )
            impostor.on_publish = lambda _c, _d, _m, r, _p: (
                service_denied.append(r.value) if r.is_failure else None
            )
            impostor.connect("broker", 1883)
            impostor.loop_start()
            wait_until(service_ready.is_set)
            impostor.publish(topic, body(), qos=1).wait_for_publish(5)
            wait_until(lambda: bool(service_denied))
            assert detail()["connectivity"] == "unknown"

            publisher.publish(topic, body(), qos=1, retain=True).wait_for_publish(5)
            wait_until(lambda: worker.rejections == 1)
            assert detail()["connectivity"] == "unknown"
            publisher.publish(topic, b"", qos=1, retain=True).wait_for_publish(5)
            wait_until(lambda: worker.rejections == 2)
            publisher.publish(
                f"freshlink/v1/devices/{FOREIGN}/heartbeat", body(FOREIGN), qos=1
            ).wait_for_publish(5)
            wait_until(lambda: bool(denied))
            publisher.publish(topic, body(FOREIGN), qos=1).wait_for_publish(5)
            wait_until(lambda: worker.rejections == 3)
            assert detail()["connectivity"] == "unknown"
            original = body()
            publisher.publish(topic, original, qos=0).wait_for_publish(5)
            wait_until(lambda: detail()["connectivity"] == "online")
            seen = detail()["last_seen_at"]
            publisher.publish(topic, original, qos=1).wait_for_publish(5)
            wait_until(lambda: worker.committed >= 2)
            assert detail()["last_seen_at"] == seen
            wait_until(lambda: detail()["connectivity"] == "offline")
            offline = events()
            assert len(offline) == 1 and offline[0]["kind"] == "offline"
            publisher.publish(topic, body(), qos=1).wait_for_publish(5)
            wait_until(lambda: detail()["connectivity"] == "online")
            history = events()
            recovery = next(e for e in history if e["kind"] == "recovered")
            assert recovery["related_source_event_id"] == offline[0]["source_event_id"]
            assert len(history) == 2 and all(
                e["delivery_state"] == "held_unbound" for e in history
            )
            assert (
                api.get(f"/api/v1/devices/{SENSOR}/connectivity-events").status_code
                == 401
            )
            assert not failures and worker.fatal_reason is None
            result = {
                "phase": "live_authenticated_mqtt_postgresql_api_passed",
                "device_id": str(SENSOR),
                "server_last_seen_at": seen,
                "event_ids": [e["source_event_id"] for e in history],
                "recovery_references_offline": True,
                "duplicate_did_not_renew": True,
                "retained_and_mismatched_rejected": True,
                "foreign_topic_denied_by_broker": True,
                "service_cannot_publish_heartbeat": True,
                "test_offline_seconds": 3,
                "production_default_offline_seconds": 90,
                "not_tested": [
                    "production TLS/ACL provisioning",
                    "browser",
                    "B notifications",
                    "C persistence",
                    "E1",
                ],
            }
            target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    finally:
        if impostor is not None:
            impostor.disconnect()
            impostor.loop_stop()
        publisher.disconnect()
        publisher.loop_stop()
        worker.stop_requested.set()
        thread.join(timeout=10)
        assert not thread.is_alive(), "Worker did not stop"
    assert not failures


if __name__ == "__main__":
    run()
