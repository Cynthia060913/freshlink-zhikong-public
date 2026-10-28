"""Bounded local demo supervisor; reuse frozen application services, no DDL."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run():
    database = urlparse(os.getenv("DATABASE_URL", ""))
    require(
        Path("/.dockerenv").is_file()
        and os.getenv("FRESHLINK_INTERACTIVE_DEMO") == "1"
        and os.getenv("PROJECT_NAME") == "FreshLink-test"
        and database.hostname == "test-db"
        and database.username == "freshlink_test"
        and database.path == "/freshlink_test"
        and not any(
            value
            for key, value in os.environ.items()
            if key in {"SMTP_HOST", "SMTP_PASSWORD", "SENTRY_DSN"} or "FEISHU" in key
        ),
        "explicit isolated demo environment required",
    )
    seconds = int(os.getenv("FRESHLINK_DEMO_SECONDS", "300"))
    require(60 <= seconds <= 600, "demo duration must be 60..600 seconds")
    # No application imports, configuration validation or network before guard.
    import httpx
    from app.core.config import settings
    from app.core.db import engine
    from app.modules.iot.control_worker import ControlWorker, ControlWorkerSettings
    from app.modules.iot.heartbeat_worker import (
        HeartbeatWorker,
        HeartbeatWorkerSettings,
    )
    from app.modules.iot.models import Device
    from app.modules.iot.simulator_fleet import FleetSettings, SimulatorFleet
    from app.modules.iot.stream_frontier_models import streams
    from app.modules.iot.telemetry_service import TelemetryServiceSettings, verify_scope
    from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session
    from app.modules.telemetry.storage_candidate import observations
    from sqlalchemy import func
    from sqlmodel import Session, select

    root = Path("/results")
    source = os.environ["FRESHLINK_DEMO_SOURCE"]
    require(
        source == "e7fffd61c199234918dcf1713cc1f3dd6ce78c51",
        "review this operator before using a different application",
    )
    report_path = root / "interactive-runtime.json"
    require(not report_path.exists(), "runtime already used; do not start twice")
    sensors = [UUID(f"10000000-0000-4000-8000-{i:012d}") for i in range(1, 8)]
    actuators = [UUID(f"20000000-0000-4000-8000-{i:012d}") for i in range(1, 8)]
    with Session(engine) as session:
        devices = {d.id: d for d in session.exec(select(Device)).all()}
    require(
        set(devices) == set(sensors + actuators),
        "exact seeded 14-device scope required",
    )
    with engine.connect() as connection:
        origins = connection.execute(select(streams)).mappings().all()
        before = connection.scalar(select(func.count()).select_from(observations))
    require(len(origins) == 7, "exact initialized stream scope required")
    epochs = {row["device_id"]: row["stream_epoch"] for row in origins}
    require(set(epochs) == set(sensors), "unexpected initialized stream")
    for row in origins:
        require(
            row["origin_verified"] is True
            and row["warehouse_id"] == devices[row["device_id"]].warehouse_id,
            "unverified or rebound stream; no automatic repair",
        )
    common = {
        "host": "broker",
        "port": 1883,
        "tls": False,
        "isolated_test_plaintext": True,
        "username": "fixture-controller",
        "acl_confirmed": True,
        "scan_period_seconds": 0.2,
    }
    telemetry = TelemetryServiceSettings(
        **common,
        client_id="interactive-demo-receive",
        device_ids=sensors,
        frontier_epochs=epochs,
    )
    verify_scope(engine, telemetry)
    config_path = root / "interactive-telemetry.json"
    config_path.write_text(telemetry.model_dump_json(), encoding="utf-8")
    zones, passwords = [], {}
    for number, (sensor, actuator) in enumerate(
        zip(sensors, actuators, strict=True), 1
    ):
        require(
            devices[sensor].kind == "temperature_sensor"
            and devices[actuator].kind == "cooling_actuator"
            and devices[sensor].warehouse_id is not None
            and devices[actuator].warehouse_id == devices[sensor].warehouse_id,
            "invalid seeded device binding",
        )
        zones.append(
            {
                "host": "broker",
                "port": 1883,
                "tls": False,
                "isolated_test_plaintext": True,
                "sensor_username": f"fixture-sensor-{number}",
                "actuator_username": f"fixture-actuator-{number}",
                "sensor_id": sensor,
                "actuator_id": actuator,
                "warehouse_id": devices[sensor].warehouse_id,
                "state_file": str(root / "fleet-state" / f"zone-{number}.db"),
                "sample_period_seconds": 1,
                "heartbeat_period_seconds": 1,
                "resend_period_seconds": 10,
            }
        )
        passwords[sensor] = f"fixture-sensor-{number}-only"
        passwords[actuator] = f"fixture-actuator-{number}-only"
    require(
        len({zone["warehouse_id"] for zone in zones}) == 2,
        "two seeded warehouses required",
    )
    stop, errors, threads, children = threading.Event(), [], [], []
    workers = [
        HeartbeatWorker(
            HeartbeatWorkerSettings(
                **common,
                client_id="interactive-demo-heartbeat",
                device_ids=sensors + actuators,
            ),
            password="fixture-controller-only",
        ),
        ControlWorker(
            ControlWorkerSettings(
                **common, client_id="interactive-demo-control", device_ids=actuators
            ),
            password="fixture-controller-only",
            warehouse_lookup_factory=warehouse_lookup_for_session,
        ),
        SimulatorFleet(FleetSettings(zones=zones), password_for=passwords.__getitem__),
    ]
    previous = {}
    report = {
        "source": source,
        "status": "starting",
        "duration_limit_seconds": seconds,
        "initial_observations": before,
        "logical_devices": 14,
        "automatic_recovery_enabled": False,
        "real_notifications_enabled": False,
        "application_source_mount": False,
    }

    def execute(worker):
        try:
            worker.run(max_seconds=seconds)
        except Exception as error:  # noqa: BLE001 - redact arbitrary worker/config errors
            errors.append(type(worker).__name__ + ":" + type(error).__name__)
            stop.set()

    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, lambda *_: stop.set())
        with httpx.Client(
            base_url="http://127.0.0.1:8000", timeout=10, trust_env=False
        ) as api:
            response = api.post(
                "/api/v1/login/access-token",
                data={
                    "username": settings.FIRST_SUPERUSER,
                    "password": settings.FIRST_SUPERUSER_PASSWORD,
                },
            )
            require(response.status_code == 200, "local API login failed")
            bearer = response.json()["access_token"]
        for role in ("receive", "store", "evaluate", "deliver-high"):
            environment = dict(os.environ)
            if role == "receive":
                environment["FRESHLINK_TELEMETRY_MQTT_PASSWORD"] = (
                    "fixture-controller-only"
                )
            if role == "deliver-high":
                environment["FRESHLINK_B_INTERNAL_ORIGIN"] = "http://127.0.0.1:8000"
                environment["FRESHLINK_B_SERVICE_BEARER"] = bearer
            log = (root / f"interactive-{role}.log").open("x", encoding="utf-8")
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "app.modules.iot.telemetry_service",
                    "--role",
                    role,
                    "--config",
                    str(config_path),
                    "--max-seconds",
                    str(seconds + 15),
                ],
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            children.append((role, child, log))
        for worker in workers:
            thread = threading.Thread(target=execute, args=(worker,))
            threads.append(thread)
            thread.start()
        began = time.monotonic()
        while not stop.wait(0.5) and time.monotonic() - began < seconds:
            require(not errors, "demo worker failed")
            require(
                all(child.poll() is None for _, child, _ in children),
                "demo service exited",
            )
            if not (root / "interactive-ready.json").exists():
                with engine.connect() as connection:
                    count = connection.scalar(
                        select(func.count()).select_from(observations)
                    )
                if count >= before + 14:
                    (root / "interactive-ready.json").write_text(
                        json.dumps(
                            {
                                "source": source,
                                "status": "live_acquisition_ready",
                                "stored_before": before,
                                "stored_now": count,
                                "real_notifications_enabled": False,
                            }
                        ),
                        encoding="utf-8",
                    )
        report["status"] = "stopped" if stop.is_set() else "bounded_demo_completed"
    except Exception as error:  # noqa: BLE001 - redact arbitrary worker/config errors
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
    finally:
        stop.set()
        for worker in workers:
            worker.stop_requested.set()
        for thread in threads:
            thread.join(15)
        for _, child, _ in children:
            if child.poll() is None:
                child.terminate()
        exits = {}
        for role, child, log in children:
            try:
                child.wait(15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(5)
                report["forced_child_cleanup"] = True
            exits[role] = child.returncode
            log.close()
        report["child_exit_codes"] = exits
        report["thread_cleanup_ok"] = all(not t.is_alive() for t in threads)
        report["worker_errors"] = errors
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        for number, handler in previous.items():
            signal.signal(number, handler)
        engine.dispose()
    require(
        report["status"] != "failed"
        and report["thread_cleanup_ok"]
        and not report.get("forced_child_cleanup")
        and all(code == 0 for code in report["child_exit_codes"].values())
        and not errors,
        "demo stopped with an error; inspect typed status",
    )


if __name__ == "__main__":
    try:
        run()
    except Exception as error:  # noqa: BLE001 - redact arbitrary worker/config errors
        print(
            json.dumps(
                {"status": "demo_refused_or_failed", "error_type": type(error).__name__}
            )
        )
        raise SystemExit(1) from None
