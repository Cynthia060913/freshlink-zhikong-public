"""Disposable authenticated API -> PG queue -> MQTT -> device -> PG ACK proof.

Default warehouse mode is an explicit fixture. Opt-in real mode creates only
B's Warehouse table in the guarded disposable DB, then uses real B HTTP create,
A HTTP binding and the actual lookup. Neither mode is a formal B migration or
C telemetry persistence. No real email/APP or production data.
"""

import json
import logging
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt
from fastapi.testclient import TestClient
from paho.mqtt.enums import CallbackAPIVersion
from sqlmodel import Session, SQLModel

from app.core.config import settings
from app.core.db import engine
from app.main import app
from app.modules.iot.control_models import DeviceCommand
from app.modules.iot.control_routes import control_warehouse_lookup
from app.modules.iot.control_worker import ControlWorker, ControlWorkerSettings
from app.modules.iot.heartbeat_worker import HeartbeatWorker, HeartbeatWorkerSettings
from app.modules.iot.models import Device
from app.modules.iot.simulator_runtime import DeviceRuntime, RuntimeSettings
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session

SENSOR = UUID("11111111-1111-4111-8111-111111111111")
ACTUATOR = UUID("66666666-6666-4666-8666-666666666666")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")


def authenticate(api):
    login = api.post(
        "/api/v1/login/access-token",
        data={
            "username": settings.FIRST_SUPERUSER,
            "password": settings.FIRST_SUPERUSER_PASSWORD,
        },
    )
    assert login.status_code == 200
    return {"Authorization": "Bearer " + login.json()["access_token"]}


def run() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    database = urlparse(str(settings.DATABASE_URL))
    if (
        database.hostname != "test-db"
        or database.path != "/freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
    ):
        raise RuntimeError("Refusing non-disposable database")
    target = Path("/results/mqtt-control.json")
    state_file = Path("/results/control-device.db")
    if target.exists() or state_file.exists():
        raise RuntimeError("Refusing to overwrite previous evidence")
    mode = os.environ.get("CONTROL_MQTT_WAREHOUSE_MODE", "fixture")
    if mode not in ("fixture", "real"):
        raise RuntimeError("Invalid isolated warehouse mode")
    warehouse_identity = WAREHOUSE
    binding_proof = None
    if mode == "real":
        # TEST-ONLY DDL, after exact disposable host/name/project guards. Do not
        # install all B models, add startup create_all, or claim B Alembic done.
        SQLModel.metadata.tables["alerts_warehouse"].create(engine, checkfirst=False)
        with TestClient(app) as api:
            headers = authenticate(api)
            warehouse = api.post(
                "/api/v1/warehouses/",
                headers=headers,
                json={"code": "control-proof", "name": "Isolated B warehouse"},
            )
            assert warehouse.status_code == 201
            warehouse_identity = UUID(warehouse.json()["id"])
    with Session(engine) as session:
        for identity, kind in [
            (SENSOR, "temperature_sensor"),
            (ACTUATOR, "cooling_actuator"),
        ]:
            assert session.get(Device, identity) is None
            session.add(
                Device(
                    id=identity,
                    code="control-fixture-" + kind,
                    name="Explicit isolated control fixture",
                    kind=kind,
                    warehouse_id=None if mode == "real" else warehouse_identity,
                )
            )
        session.commit()
    if mode == "real":
        with TestClient(app) as api:
            headers = authenticate(api)
            for identity in (SENSOR, ACTUATOR):
                path = f"/api/v1/devices/{identity}/binding"
                missing = api.post(
                    path, headers=headers, json={"warehouse_id": str(uuid4())}
                )
                assert missing.status_code == 404
                assert (
                    api.get(f"/api/v1/devices/{identity}", headers=headers).json()[
                        "warehouse_id"
                    ]
                    is None
                )
                for _ in range(2):
                    bound = api.post(
                        path,
                        headers=headers,
                        json={"warehouse_id": str(warehouse_identity)},
                    )
                    assert bound.status_code == 200
                    assert bound.json()["warehouse_id"] == str(warehouse_identity)
                    assert bound.json()["connectivity"] == "unknown"
            binding_proof = {
                "warehouse_id": str(warehouse_identity),
                "device_ids": [str(SENSOR), str(ACTUATOR)],
                "unknown_warehouse_rejected": True,
                "same_binding_retry_safe": True,
                "did_not_claim_online": True,
                "ddl_scope": "test-only alerts_warehouse; NOT B Alembic",
            }
    lookup_factory = (
        warehouse_lookup_for_session
        if mode == "real"
        else lambda _session: lambda identity: identity == warehouse_identity
    )
    common = {
        "host": "broker",
        "port": 1883,
        "tls": False,
        "isolated_test_plaintext": True,
        "username": "test-controller",
        "acl_confirmed": True,
        "scan_period_seconds": 0.1,
    }
    heartbeat = HeartbeatWorker(
        HeartbeatWorkerSettings.model_validate(
            {
                **common,
                "client_id": "control-proof-hb",
                "device_ids": [SENSOR, ACTUATOR],
            }
        ),
        password="fixture-controller-only",
    )
    control = ControlWorker(
        ControlWorkerSettings.model_validate(
            {**common, "client_id": "control-proof-worker", "device_ids": [ACTUATOR]}
        ),
        password="fixture-controller-only",
        warehouse_lookup_factory=lookup_factory,
    )
    device_config = RuntimeSettings.model_validate(
        {
            "host": "broker",
            "port": 1883,
            "tls": False,
            "isolated_test_plaintext": True,
            "sensor_username": "test-sensor",
            "actuator_username": "test-actuator",
            "sensor_id": SENSOR,
            "actuator_id": ACTUATOR,
            "warehouse_id": warehouse_identity,
            "state_file": state_file,
            "sample_period_seconds": 0.5,
            "heartbeat_period_seconds": 0.5,
            "resend_period_seconds": 1,
        }
    )
    failures = []
    workers = []
    threads = []

    def launch(worker):
        def execute():
            try:
                worker.run(max_seconds=50)
            except Exception as error:
                failures.append(type(error).__name__)

        thread = threading.Thread(target=execute)
        workers.append(worker)
        threads.append(thread)
        thread.start()
        return thread

    def new_device():
        return DeviceRuntime(
            device_config,
            sensor_password="fixture-sensor-only",
            actuator_password="fixture-actuator-only",
        )

    def wait_until(predicate, timeout=12):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if failures:
                raise AssertionError("Worker failed; inspect fixed reason log")
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError("Live control checkpoint timed out")

    temperatures = []
    lock = threading.Lock()
    observer = mqtt.Client(
        CallbackAPIVersion.VERSION2,
        client_id="control-proof-observer",
        protocol=mqtt.MQTTv5,
    )
    observer.username_pw_set("test-controller", "fixture-controller-only")
    subscribed = threading.Event()
    denied = []

    def on_connect(client, _d, _f, reason, _p):
        assert not reason.is_failure
        client.subscribe(f"freshlink/v1/devices/{SENSOR}/telemetry", qos=1)

    def on_message(_c, _d, message):
        value = json.loads(message.payload)["metrics"][0]["value"]
        with lock:
            assert len(temperatures) < 200
            temperatures.append(value)

    def temperature():
        with lock:
            return temperatures[-1] if temperatures else None

    observer.on_connect = on_connect
    observer.on_subscribe = lambda _c, _d, _m, reasons, _p: (
        subscribed.set() if all(not r.is_failure for r in reasons) else None
    )
    observer.on_message = on_message
    observer.on_publish = lambda _c, _d, _m, r, _p: (
        denied.append(r.value) if r.is_failure else None
    )
    assert control_warehouse_lookup not in app.dependency_overrides
    if mode == "fixture":
        app.dependency_overrides[control_warehouse_lookup] = lambda: (
            lambda identity: identity == warehouse_identity
        )
    try:
        launch(heartbeat)
        control_thread = launch(control)
        observer.connect("broker", 1883)
        observer.loop_start()
        device = new_device()
        device_thread = launch(device)
        wait_until(
            lambda: (
                heartbeat.ready.is_set()
                and control.ready.is_set()
                and subscribed.is_set()
            )
        )
        with TestClient(app) as api:
            headers = authenticate(api)
            prefix = f"/api/v1/devices/{ACTUATOR}/commands"

            def detail(identity):
                response = api.get(f"{prefix}/{identity}", headers=headers)
                assert response.status_code == 200
                return response.json()

            wait_until(
                lambda: (
                    api.get(f"/api/v1/devices/{ACTUATOR}", headers=headers).json()[
                        "connectivity"
                    ]
                    == "online"
                    and temperature() is not None
                )
            )
            before = temperature()
            command_id = uuid4()
            body = {
                "command_id": str(command_id),
                "enabled": True,
                "lifetime_seconds": 30,
            }
            response = api.post(prefix, headers=headers, json=body)
            assert response.status_code == 201
            wait_until(
                lambda: (
                    detail(command_id)["status"] == "acknowledged"
                    and detail(command_id)["transport_accepted_at"] is not None
                )
            )
            wait_until(lambda: temperature() < before - 0.05)
            first = detail(command_id)
            assert len(first["receipts"]) == 1
            assert first["receipts"][0]["result"] == "executed"
            assert device.plant.state()["execution_count"] == 1
            controlled = temperature()
            assert api.post(prefix, headers=headers, json=body).json()[
                "command_id"
            ] == str(command_id)
            # Actual broker replay of the original DB payload, not fabricated ACK.
            with Session(engine) as session:
                original = session.get(DeviceCommand, command_id).payload_text
            committed = control.committed
            observer.publish(
                f"freshlink/v1/devices/{ACTUATOR}/commands", original, qos=1
            ).wait_for_publish(5)
            wait_until(lambda: control.committed > committed)
            assert device.plant.state()["execution_count"] == 1
            assert len(detail(command_id)["receipts"]) == 1
            # Controller credentials cannot forge an execution receipt.
            observer.publish(
                f"freshlink/v1/devices/{ACTUATOR}/acks", b"{}", qos=1
            ).wait_for_publish(5)
            wait_until(lambda: bool(denied))
            device.stop_requested.set()
            device_thread.join(timeout=10)
            assert not device_thread.is_alive()
            # A real server-worker restart while a device is absent: the DB,
            # not a volatile MQTT queue, supplies the original command again.
            restart_id = uuid4()
            queued = api.post(
                prefix,
                headers=headers,
                json={
                    "command_id": str(restart_id),
                    "enabled": False,
                    "lifetime_seconds": 30,
                },
            )
            assert queued.status_code == 201
            wait_until(lambda: detail(restart_id)["transport_accepted_at"] is not None)
            with Session(engine) as session:
                restart_payload = session.get(DeviceCommand, restart_id).payload_text
            control.stop_requested.set()
            control_thread.join(timeout=10)
            assert not control_thread.is_alive()
            control = ControlWorker(
                control.settings,
                password="fixture-controller-only",
                warehouse_lookup_factory=lookup_factory,
            )
            launch(control)
            resumed = new_device()
            resumed_thread = launch(resumed)
            wait_until(lambda: detail(restart_id)["status"] == "acknowledged")
            with Session(engine) as session:
                assert (
                    session.get(DeviceCommand, restart_id).payload_text
                    == restart_payload
                )
            assert resumed.plant.state()["execution_count"] == 2
            restart_detail = detail(restart_id)
            resumed.stop_requested.set()
            resumed_thread.join(timeout=10)
            assert not resumed_thread.is_alive()
            timeout_id = uuid4()
            timed = api.post(
                prefix,
                headers=headers,
                json={
                    "command_id": str(timeout_id),
                    "enabled": True,
                    "lifetime_seconds": 2,
                },
            )
            assert timed.status_code == 201
            wait_until(lambda: detail(timeout_id)["status"] == "timeout")
            expired = detail(timeout_id)
            assert (
                expired["transport_accepted_at"] is not None and not expired["receipts"]
            )
            # Device restart plus expired original payload produces a real late
            # failed ACK. It must not rewrite timeout into success or execute.
            restarted = new_device()
            launch(restarted)
            wait_until(restarted.ready_actuator.is_set)
            with Session(engine) as session:
                expired_payload = session.get(DeviceCommand, timeout_id).payload_text
            observer.publish(
                f"freshlink/v1/devices/{ACTUATOR}/commands", expired_payload, qos=1
            ).wait_for_publish(5)
            wait_until(lambda: bool(detail(timeout_id)["receipts"]))
            late = detail(timeout_id)
            assert (
                late["status"] == "timeout" and late["receipts"][0]["outcome"] == "late"
            )
            assert late["receipts"][0]["reason"] == "expired"
            assert restarted.plant.state()["execution_count"] == 2
            result = {
                "phase": "live_api_queue_mqtt_device_ack_postgresql_passed",
                "checked_at": datetime.now(UTC).isoformat(),
                "command_id": str(command_id),
                "timeout_command_id": str(timeout_id),
                "command": first,
                "timeout_with_late_ack": late,
                "temperature_before": before,
                "temperature_after": controlled,
                "replayed_first_command_execution_count": 1,
                "worker_restart_command": restart_detail,
                "worker_restart_preserved_original_payload": True,
                "execution_count_after_two_valid_commands_and_expired_replay": 2,
                "controller_ack_publish_denied": True,
                "warehouse_lookup": (
                    "actual B service, same-session lookup; no dependency override"
                    if mode == "real"
                    else "explicit isolated fixture, NOT B integration"
                ),
                "warehouse_mode": mode,
                "binding_proof": binding_proof,
                "not_tested": [
                    "formal B migration/alerts/notifications",
                    "C telemetry persistence",
                    "browser control UI",
                    "two real notification receipts",
                    "production TLS provisioning",
                    "E1",
                ],
            }
    finally:
        app.dependency_overrides.pop(control_warehouse_lookup, None)
        observer.disconnect()
        observer.loop_stop()
        for worker in workers:
            worker.stop_requested.set()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive(), "Owned worker did not stop"
    assert not failures
    target.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    run()
