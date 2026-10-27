"""Three-minute real MQTT/PG load regression, no external notifications.

Requires the existing owned, empty candidate migration test environment. This
does not reconcile a previously held stream or certify complete E1.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.modules.alerts.models import NotificationAttempt
from app.modules.iot.frontier_bootstrap import initialize_fresh_streams
from app.modules.iot.models import Device
from app.modules.iot.simulator_runtime import DeviceRuntime, RuntimeSettings
from app.modules.iot.stream_frontier_models import streams
from app.modules.iot.telemetry_service import TelemetryServiceSettings
from app.modules.telemetry.storage_candidate import observations

SENSOR = UUID("11111111-1111-4111-8111-111111111111")
ACTUATOR = UUID("66666666-6666-4666-8666-666666666666")
ROOT = Path("/results")


def run():
    if (
        str(settings.DATABASE_URL).split("@")[-1] != "test-db:5432/freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
        or os.environ.get("FRESHLINK_GATED_PROOF") != "1"
        or os.environ.get("SMTP_HOST")
        or os.environ.get("FEISHU_WEBHOOK_URL")
    ):
        raise RuntimeError("Owned empty notification-free environment required")
    report_file = ROOT / "sustained-telemetry.json"
    if report_file.exists():
        raise RuntimeError("Refusing evidence overwrite")
    children, threads, workers, errors = [], [], [], []
    report = {
        "source_revision": os.environ["GATED_SOURCE_REVISION"],
        "source_dirty": os.environ["GATED_SOURCE_DIRTY"],
        "notifications_sent": 0,
        "complete_e1": False,
        "snapshots": [],
    }

    def rows(table):
        with engine.connect() as connection:
            return list(connection.execute(select(table)).mappings())

    def start(name, arguments, extra=None):
        log = (ROOT / ("sustained-" + name + ".log")).open("x", encoding="utf-8")
        child = subprocess.Popen(
            [sys.executable, "-B", *arguments],
            env={**os.environ, **(extra or {})},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        children.append((name, child, log))

    def wait(check, seconds=25):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if errors or any(child.poll() is not None for _, child, _ in children):
                raise RuntimeError("Sustained role exited")
            if check():
                return
            time.sleep(0.1)
        raise RuntimeError("Sustained checkpoint timeout")

    try:
        assert not rows(streams) and not rows(observations)
        start(
            "api",
            [
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "18080",
                "--no-access-log",
            ],
        )
        with httpx.Client(
            base_url="http://127.0.0.1:18080", timeout=5, trust_env=False
        ) as api:

            def healthy():
                try:
                    return api.get("/api/v1/utils/health-check/").status_code == 200
                except httpx.ConnectError:
                    return False

            wait(healthy)
            login = api.post(
                "/api/v1/login/access-token",
                data={
                    "username": settings.FIRST_SUPERUSER,
                    "password": settings.FIRST_SUPERUSER_PASSWORD,
                },
            )
            assert login.status_code == 200
            api.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            actor = UUID(api.get("/api/v1/users/me").json()["id"])
            warehouse = api.post(
                "/api/v1/warehouses/",
                json={"code": "sustained-proof", "name": "Owned sustained proof"},
            )
            assert warehouse.status_code == 201
            warehouse_id = UUID(warehouse.json()["id"])
            with Session(engine) as session:
                session.add(
                    Device(
                        id=SENSOR,
                        code="sustained-sensor",
                        name="Owned sensor",
                        kind="temperature_sensor",
                    )
                )
                session.add(
                    Device(
                        id=ACTUATOR,
                        code="sustained-actuator",
                        name="Owned actuator",
                        kind="cooling_actuator",
                    )
                )
                session.commit()
            for identifier in (SENSOR, ACTUATOR):
                assert (
                    api.post(
                        f"/api/v1/devices/{identifier}/binding",
                        json={"warehouse_id": str(warehouse_id)},
                    ).status_code
                    == 200
                )
            epoch = uuid4()
            with engine.begin() as connection:
                initialize_fresh_streams(
                    connection,
                    actor_id=actor,
                    bindings={SENSOR: warehouse_id},
                    epochs={SENSOR: epoch},
                )
            common = {
                "host": "broker",
                "port": 1883,
                "tls": False,
                "isolated_test_plaintext": True,
            }
            config = TelemetryServiceSettings(
                **common,
                acl_confirmed=True,
                username="test-controller",
                client_id="sustained-receiver",
                device_ids=[SENSOR],
                frontier_epochs={SENSOR: epoch},
                scan_period_seconds=0.1,
            )
            config_path = ROOT / "sustained-config.json"
            config_path.write_text(config.model_dump_json(), encoding="utf-8")
            for role in ("receive", "store", "evaluate"):
                extra = (
                    {"FRESHLINK_TELEMETRY_MQTT_PASSWORD": "fixture-controller-only"}
                    if role == "receive"
                    else {}
                )
                start(
                    role,
                    [
                        "-m",
                        "app.modules.iot.telemetry_service",
                        "--role",
                        role,
                        "--config",
                        str(config_path),
                        "--max-seconds",
                        "300",
                    ],
                    extra,
                )
            quiesce = threading.Event()
            sampling_paused = threading.Event()

            class DrainableProofDevice(DeviceRuntime):
                def step(self, *, now, monotonic):
                    # The proof pauses new sampling in its owner thread before
                    # stopping MQTT. Product stop() still preserves any unsent
                    # journal tail for a later restart; it does not drain it.
                    if quiesce.is_set():
                        self._sample_due = float("inf")
                        sampling_paused.set()
                    super().step(now=now, monotonic=monotonic)

            device = DrainableProofDevice(
                RuntimeSettings(
                    **common,
                    sensor_username="test-sensor",
                    actuator_username="test-actuator",
                    sensor_id=SENSOR,
                    actuator_id=ACTUATOR,
                    warehouse_id=warehouse_id,
                    state_file=ROOT / "sustained-device.db",
                    sample_period_seconds=0.5,
                    heartbeat_period_seconds=0.5,
                    resend_period_seconds=1,
                ),
                sensor_password="fixture-sensor-only",
                actuator_password="fixture-actuator-only",
            )
            workers.append(device)

            def execute():
                try:
                    device.run(max_seconds=240)
                except Exception as error:
                    errors.append(type(error).__name__)

            thread = threading.Thread(target=execute)
            threads.append(thread)
            thread.start()
            wait(lambda: device.ready_sensor.is_set() and bool(rows(observations)))
            began = time.monotonic()
            peak = 0
            while time.monotonic() - began < 180:
                frontier = rows(streams)[0]
                lag = frontier["admitted_seq"] - frontier["settled_through"]
                peak = max(peak, lag)
                snapshot = {
                    "seconds": round(time.monotonic() - began),
                    "admitted": frontier["admitted_seq"],
                    "settled": frontier["settled_through"],
                    "lag": lag,
                    "gap_reason": frontier["gap_reason"],
                    "stored": len(rows(observations)),
                }
                report["snapshots"].append(snapshot)
                sys.stdout.write(json.dumps(snapshot) + "\n")
                sys.stdout.flush()
                assert not frontier["gap_reason"] and lag < 100
                if errors or any(child.poll() is not None for _, child, _ in children):
                    raise RuntimeError("Sustained role exited")
                time.sleep(5)
            report["checkpoint"] = "quiesce_sampling_and_drain_transport"
            quiesce.set()
            assert sampling_paused.wait(2)
            expected = {
                UUID(sample.payload["message_id"]) for sample in device.plant.pending()
            }
            wait(
                lambda: expected == {row["message_id"] for row in rows(observations)},
                25,
            )
            device.stop_requested.set()
            thread.join(12)
            assert not thread.is_alive() and not errors
            wait(
                lambda: (
                    rows(streams)[0]["admitted_seq"]
                    == rows(streams)[0]["settled_through"]
                )
            )
            saved = rows(observations)
            assert len(saved) >= 300
            assert all(row["raw_text"] and row["raw_payload"] for row in saved)
            assert rows(NotificationAttempt.__table__) == []
            report.update(
                phase="sustained_real_mqtt_pg_passed",
                duration_seconds=round(time.monotonic() - began, 2),
                stored_unique_samples=len(saved),
                durable_device_samples=len(expected),
                all_sample_ids_preserved=True,
                proof_sampling_quiesced_before_transport_stop=True,
                frontier_gap_reason=rows(streams)[0]["gap_reason"],
                frontier_caught_up=True,
                peak_pending_admissions=peak,
            )
    except BaseException as error:
        report.update(phase="failed", error_type=type(error).__name__)
        report["failure_frontiers"] = [
            {key: row[key] for key in ("admitted_seq", "settled_through", "gap_reason")}
            for row in rows(streams)
        ]
        raise
    finally:
        for worker in workers:
            worker.stop_requested.set()
        for thread in threads:
            thread.join(12)
        report["forced_child_stops"] = []
        report["child_exit_codes"] = {}
        for name, child, log in reversed(children):
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=12)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
                report["forced_child_stops"].append(name)
            report["child_exit_codes"][name] = child.returncode
            log.close()
        report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        engine.dispose()


if __name__ == "__main__":
    run()
