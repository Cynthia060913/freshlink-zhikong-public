"""Opt-in disposable real HTTP/MQTT/browser harness, never an app entrypoint."""

import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID


def require(condition: bool, reason: str) -> None:
    """Evidence gates must remain active under python -O/PYTHONOPTIMIZE."""
    if not condition:
        raise RuntimeError("Browser control verification failed: " + reason)


def preflight() -> str:
    source = os.environ.get("CONTROL_BROWSER_SOURCE", "")
    database = urlparse(os.environ.get("DATABASE_URL", ""))
    if (
        not Path("/.dockerenv").is_file()
        or database.hostname != "test-db"
        or database.path != "/freshlink_test"
        or database.username != "freshlink_test"
        or os.environ.get("PROJECT_NAME") != "FreshLink-test"
        or os.environ.get("FASTAPI_ENV") != "development"
        or os.environ.get("SMTP_HOST") != ""
        or os.environ.get("SENTRY_DSN") != ""
        or not re.fullmatch(r"[0-9a-f]{40}", source)
    ):
        raise RuntimeError("Refusing non-disposable browser control environment")
    return source


def run() -> None:
    source = preflight()
    # Import DB/application code only AFTER the exact disposable guards.
    import httpx
    import paho.mqtt.client as mqtt
    import uvicorn
    from paho.mqtt.enums import CallbackAPIVersion
    from sqlalchemy import func
    from sqlmodel import Session, select

    from app.core.db import engine
    from app.main import app
    from app.modules.iot.control_models import DeviceCommand
    from app.modules.iot.control_routes import control_warehouse_lookup
    from app.modules.iot.control_worker import ControlWorker, ControlWorkerSettings
    from app.modules.iot.heartbeat_worker import (
        HeartbeatWorker,
        HeartbeatWorkerSettings,
    )
    from app.modules.iot.models import Device
    from app.modules.iot.proof_schema_guard import require_b01_candidate_schema
    from app.modules.iot.simulator_runtime import DeviceRuntime, RuntimeSettings
    from app.modules.iot.telemetry_inbox_models import metadata as inbox_metadata
    from app.modules.iot.telemetry_inbox_models import queue_guard
    from app.modules.iot.telemetry_worker import (
        TelemetryDeliveryWorker,
        TelemetryReceiver,
    )
    from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session
    from app.modules.telemetry.storage_candidate import ingest_observation, observations
    from app.modules.telemetry.storage_candidate import metadata as c_metadata

    sensor = UUID("11111111-1111-4111-8111-111111111111")
    actuator = UUID("66666666-6666-4666-8666-666666666666")
    results = Path("/results")
    for name in ("ready.json", "browser.json", "server.json", "browser-device.db"):
        if (results / name).exists():
            raise RuntimeError("Refusing to overwrite browser control evidence")
    if control_warehouse_lookup in app.dependency_overrides:
        raise RuntimeError("Refusing fake warehouse lookup")
    # Never invent substitute B DDL: Compose must have run actual candidate B01.
    require_b01_candidate_schema(engine)
    # Candidate-only DDL in the already guarded, newly owned test DB. This is
    # not a substitute for the still-unpublished A04/C formal migration chain.
    c_metadata.create_all(engine)
    inbox_metadata.create_all(engine, checkfirst=False)
    with engine.begin() as connection:
        connection.execute(queue_guard.insert().values(id=1))
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    )
    threads = []
    workers = []
    failures = []
    samples = []
    observed_ids = set()
    sample_lock = threading.Lock()
    subscribed = threading.Event()

    def launch(target):
        def execute():
            try:
                target()
            except Exception as error:
                failures.append(type(error).__name__)

        thread = threading.Thread(target=execute, daemon=True)
        threads.append(thread)
        thread.start()

    def wait_until(predicate, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if failures:
                raise RuntimeError("Owned browser harness worker failed")
            if predicate():
                return
            time.sleep(0.1)
        raise RuntimeError("Browser control checkpoint timed out")

    observer = mqtt.Client(
        CallbackAPIVersion.VERSION2,
        client_id="browser-control-observer",
        protocol=mqtt.MQTTv5,
    )
    observer.username_pw_set("test-controller", "fixture-controller-only")

    def on_connect(client, _userdata, _flags, reason, _properties):
        if not reason.is_failure:
            client.subscribe(f"freshlink/v1/devices/{sensor}/telemetry", qos=1)

    def on_subscribe(_client, _userdata, _mid, reasons, _properties):
        if all(not reason.is_failure for reason in reasons):
            subscribed.set()

    def on_message(_client, _userdata, message):
        raw = json.loads(message.payload)
        identity = UUID(raw["message_id"])
        value = raw["metrics"][0]["value"]
        with sample_lock:
            if identity in observed_ids:
                return
            if len(samples) >= 500:
                failures.append("sample_capacity")
            else:
                observed_ids.add(identity)
                samples.append(value)

    observer.on_connect = on_connect
    observer.on_subscribe = on_subscribe
    observer.on_message = on_message
    try:
        launch(server.run)
        wait_until(lambda: server.started)
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=5) as api:
            login = api.post(
                "/api/v1/login/access-token",
                data={
                    "username": "admin@example.com",
                    "password": "a01-disposable-admin-only",
                },
            )
            login.raise_for_status()
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            warehouse = api.post(
                "/api/v1/warehouses/",
                headers=headers,
                json={"code": "browser-proof", "name": "Isolated browser warehouse"},
            )
            require(warehouse.status_code == 201, "warehouse create")
            warehouse_id = UUID(warehouse.json()["id"])
            with Session(engine) as session:
                for identity, kind in (
                    (sensor, "temperature_sensor"),
                    (actuator, "cooling_actuator"),
                ):
                    session.add(
                        Device(
                            id=identity,
                            code="browser-" + kind,
                            name="Browser proof " + kind,
                            kind=kind,
                        )
                    )
                session.commit()
            for identity in (sensor, actuator):
                bound = api.post(
                    f"/api/v1/devices/{identity}/binding",
                    headers=headers,
                    json={"warehouse_id": str(warehouse_id)},
                )
                require(bound.status_code == 200, "device binding")
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
                        "client_id": "browser-control-hb",
                        "device_ids": [sensor, actuator],
                    }
                ),
                password="fixture-controller-only",
            )
            control = ControlWorker(
                ControlWorkerSettings.model_validate(
                    {
                        **common,
                        "client_id": "browser-control-worker",
                        "device_ids": [actuator],
                    }
                ),
                password="fixture-controller-only",
                warehouse_lookup_factory=warehouse_lookup_for_session,
            )
            telemetry = TelemetryReceiver(
                HeartbeatWorkerSettings.model_validate(
                    {
                        **common,
                        "client_id": "browser-telemetry-receiver",
                        "device_ids": [sensor],
                    }
                ),
                password="fixture-controller-only",
                warehouse_lookup_factory=warehouse_lookup_for_session,
            )
            delivery = TelemetryDeliveryWorker(
                engine,
                lambda envelope: ingest_observation(engine, envelope),
                poll_seconds=0.1,
            )
            device = DeviceRuntime(
                RuntimeSettings.model_validate(
                    {
                        "host": "broker",
                        "port": 1883,
                        "tls": False,
                        "isolated_test_plaintext": True,
                        "sensor_username": "test-sensor",
                        "actuator_username": "test-actuator",
                        "sensor_id": sensor,
                        "actuator_id": actuator,
                        "warehouse_id": warehouse_id,
                        "state_file": results / "browser-device.db",
                        "sample_period_seconds": 0.5,
                        "heartbeat_period_seconds": 0.5,
                        "resend_period_seconds": 1,
                    }
                ),
                sensor_password="fixture-sensor-only",
                actuator_password="fixture-actuator-only",
            )
            observer.connect("broker", 1883)
            observer.loop_start()
            for worker in (heartbeat, control, telemetry, delivery, device):
                workers.append(worker)
                launch(lambda worker=worker: worker.run(max_seconds=180))

            def enough_distinct_c_observations():
                with engine.connect() as connection:
                    return (
                        connection.scalar(
                            select(func.count()).select_from(observations)
                        )
                        >= 25
                    )

            wait_until(
                lambda: (
                    heartbeat.ready.is_set()
                    and control.ready.is_set()
                    and telemetry.ready.is_set()
                    and enough_distinct_c_observations()
                    and subscribed.is_set()
                    and bool(samples)
                    and api.get(f"/api/v1/devices/{actuator}", headers=headers).json()[
                        "connectivity"
                    ]
                    == "online"
                )
            )
            with sample_lock:
                before = samples[-1]
            with (results / "ready.json").open("x", encoding="utf-8") as output:
                json.dump(
                    {
                        "source": source,
                        "actuator_id": str(actuator),
                        "sensor_id": str(sensor),
                        "warehouse_id": str(warehouse_id),
                    },
                    output,
                )
            wait_until(lambda: (results / "browser.json").is_file(), timeout=120)
            browser = json.loads((results / "browser.json").read_text(encoding="utf-8"))
            require(
                browser["phase"] == "real_browser_http_mqtt_control_passed",
                "browser result phase",
            )
            command_id = UUID(browser["command_id"])
            prefix = f"/api/v1/devices/{actuator}/commands"
            detail = api.get(f"{prefix}/{command_id}", headers=headers).json()
            require(detail["status"] == "acknowledged", "saved ACK status")
            require(detail["enabled"] is True, "command intent")
            require(len(detail["receipts"]) == 1, "receipt count")
            require(detail["receipts"][0]["outcome"] == "accepted", "receipt outcome")
            require(device.plant.state()["execution_count"] == 1, "execution count")
            require(
                browser.get("telemetry_history_csv_passed") is True,
                "real telemetry page/fixed-snapshot CSV",
            )
            require(
                delivery.completed >= browser["telemetry_snapshot_total"],
                "C durable delivery count",
            )
            with Session(engine) as session:
                require(len(session.exec(select(DeviceCommand)).all()) == 1, "DB count")
            wait_until(lambda: bool(samples) and samples[-1] < before - 0.05)
            with sample_lock:
                after = samples[-1]
            result = {
                "phase": "browser_to_real_http_pg_mqtt_device_ack_passed",
                "source": source,
                "checked_at": datetime.now(UTC).isoformat(),
                "command": detail,
                "execution_count": 1,
                "telemetry_history_csv_passed": True,
                "c_durable_deliveries": delivery.completed,
                "temperature_before": before,
                "temperature_after": after,
                "warehouse_lookup": "actual B HTTP/service; no dependency override",
                "ddl_scope": "actual B01 Alembic; guarded A/C candidate metadata, not formal published chain",
                "not_tested": [
                    "continuous evaluation and automatic recovery",
                    "multi-batch evaluation",
                    "real notifications",
                    "formal B/C migrations",
                    "full E1",
                    "production deployment or TLS provisioning",
                ],
            }
    finally:
        server.should_exit = True
        observer.disconnect()
        observer.loop_stop()
        for worker in workers:
            worker.stop_requested.set()
        for thread in threads:
            thread.join(timeout=10)
            if thread.is_alive():
                failures.append("owned_thread_did_not_stop")
        engine.dispose()
    if failures:
        raise RuntimeError("Browser control harness did not stop cleanly")
    with (results / "server.json").open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["--preflight"]:
        preflight()
        sys.stdout.write("PASS disposable browser environment before migrations\n")
    elif not sys.argv[1:]:
        run()
    else:
        raise SystemExit("Unsupported browser harness arguments")
