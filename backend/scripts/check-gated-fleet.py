"""Owned 2-warehouse/3-batch/14-device PG+MQTT segment; never full E1."""

import csv
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4


def require(condition, reason):
    if not condition:
        raise RuntimeError("Gated fleet proof: " + reason)


def preflight():
    database = urlparse(os.getenv("DATABASE_URL", ""))
    require(
        Path("/.dockerenv").is_file()
        and os.getenv("FRESHLINK_GATED_FLEET_PROOF") == "1"
        and os.getenv("PROJECT_NAME") == "FreshLink-test"
        and os.getenv("FASTAPI_ENV") == "development"
        and database.hostname == "test-db"
        and database.username == "freshlink_test"
        and database.path == "/freshlink_test"
        and not any(
            os.getenv(name) for name in ("SMTP_HOST", "SMTP_PASSWORD", "SENTRY_DSN")
        ),
        "explicit disposable environment required before imports/migrations",
    )


def identity(prefix, index):
    return UUID(f"{prefix}0000000-0000-4000-8000-{index:012d}")


def run():
    preflight()
    # Reuse the exact empty-business guard, without executing its proof.
    import importlib.util

    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from sqlmodel import Session

    from app.core.config import settings
    from app.core.db import engine
    from app.main import app
    from app.modules.iot.control_worker import ControlWorker, ControlWorkerSettings
    from app.modules.iot.frontier_bootstrap import initialize_fresh_streams
    from app.modules.iot.heartbeat import HeartbeatPolicy
    from app.modules.iot.heartbeat_worker import (
        HeartbeatWorker,
        HeartbeatWorkerSettings,
    )
    from app.modules.iot.high_delivery_worker import HighDeliveryWorker
    from app.modules.iot.high_evaluation_worker import GatedHighWorker
    from app.modules.iot.models import Device
    from app.modules.iot.simulator_fleet import FleetSettings, SimulatorFleet
    from app.modules.iot.telemetry_inbox_models import inbox
    from app.modules.iot.telemetry_storage_bridge import GatedTelemetrySink
    from app.modules.iot.telemetry_worker import (
        TelemetryDeliveryWorker,
        TelemetryReceiver,
    )
    from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session
    from app.modules.telemetry.b_http_candidate import (
        BHttpCandidatePort,
        HttpCandidateReply,
    )
    from app.modules.telemetry.outbox_candidate import outbox
    from app.modules.telemetry.storage_candidate import observations

    spec = importlib.util.spec_from_file_location(
        "gated_segment_guard", Path(__file__).with_name("check-gated-telemetry.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.connect() as connection:
        require(
            connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            == "c01_telemetry_storage",
            "actual C01 required",
        )
        module.require_empty_test_business(connection)
    target = Path("/results/gated-fleet.json")
    require(not target.exists(), "refusing evidence overwrite")
    errors, workers, threads = [], [], []

    def launch(worker):
        def execute():
            try:
                worker.run(max_seconds=75)
            except Exception as error:
                errors.append(type(worker).__name__ + ":" + type(error).__name__)

        workers.append(worker)
        thread = threading.Thread(target=execute)
        threads.append(thread)
        thread.start()

    def wait_for(predicate, seconds=25):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            require(not errors, "worker failed " + ",".join(errors))
            if predicate():
                return
            time.sleep(0.1)
        raise RuntimeError("Gated fleet checkpoint timed out")

    def saved():
        with engine.connect() as connection:
            return list(connection.execute(select(observations)).mappings())

    try:
        with TestClient(app) as api:
            login = api.post(
                "/api/v1/login/access-token",
                data={
                    "username": settings.FIRST_SUPERUSER,
                    "password": settings.FIRST_SUPERUSER_PASSWORD,
                },
            )
            require(login.status_code == 200, "login")
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            actor = api.get("/api/v1/users/me", headers=headers).json()
            warehouses, batches, rule_ids = [], [], {}
            for number in (1, 2):
                response = api.post(
                    "/api/v1/warehouses/",
                    headers=headers,
                    json={
                        "code": f"fleet-{number}",
                        "name": f"Isolated warehouse {number}",
                    },
                )
                require(response.status_code == 201, "real B warehouse create")
                warehouses.append(UUID(response.json()["id"]))
            starts = (datetime.now(UTC) - timedelta(days=1)).isoformat()
            for number, warehouse in enumerate(
                (warehouses[0], warehouses[0], warehouses[1]), 1
            ):
                response = api.post(
                    f"/api/v1/warehouses/{warehouse}/batches",
                    headers=headers,
                    json={
                        "code": f"apple-{number}",
                        "product": "apple",
                        "started_at": starts,
                    },
                )
                require(response.status_code == 201, "real B apple batch create")
                batch_id = UUID(response.json()["id"])
                batches.append(batch_id)
                response = api.post(
                    f"/api/v1/batches/{batch_id}/rule-snapshots",
                    headers=headers,
                    json={
                        "version_id": str(uuid4()),
                        "rule_version": "fleet-normal-proof-v1",
                        "effective_start_at": starts,
                        "rules": [
                            {
                                "metric_code": "temperature",
                                "candidate_kind": "threshold_high",
                                "severity": "warning",
                                "threshold_high": 20,
                                "unit": "°C",
                            }
                        ],
                    },
                )
                require(response.status_code == 201, "real B historical rule create")
                rule_ids[batch_id] = response.json()["id"]

            zones, passwords, bindings, epochs = [], {}, {}, {}
            with Session(engine) as session:
                for number in range(1, 8):
                    sensor, actuator = identity(1, number), identity(2, number)
                    warehouse = warehouses[0 if number <= 4 else 1]
                    for device_id, kind in (
                        (sensor, "temperature_sensor"),
                        (actuator, "cooling_actuator"),
                    ):
                        require(
                            session.get(Device, device_id) is None,
                            "existing fixture identity refused",
                        )
                        session.add(
                            Device(
                                id=device_id,
                                code=f"fleet-{number}-{kind}",
                                name=f"Fixture {number} {kind}",
                                kind=kind,
                            )
                        )
                    bindings[sensor], epochs[sensor] = warehouse, uuid4()
                    passwords[sensor] = f"fixture-sensor-{number}-only"
                    passwords[actuator] = f"fixture-actuator-{number}-only"
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
                            "warehouse_id": warehouse,
                            "state_file": f"/results/fleet-state/zone-{number}.db",
                            "sample_period_seconds": 1,
                            "heartbeat_period_seconds": 1,
                            "resend_period_seconds": 10,
                        }
                    )
                session.commit()
            configuration = FleetSettings.model_validate({"zones": zones})
            for zone in configuration.zones:
                for device_id in (zone.sensor_id, zone.actuator_id):
                    response = api.post(
                        f"/api/v1/devices/{device_id}/binding",
                        headers=headers,
                        json={"warehouse_id": str(zone.warehouse_id)},
                    )
                    require(response.status_code == 200, "real A/B binding")
            with engine.begin() as connection:
                origin = initialize_fresh_streams(
                    connection,
                    actor_id=UUID(actor["id"]),
                    bindings=bindings,
                    epochs=epochs,
                )
                require(origin.created, "fresh 7-stream origin")
            common = {
                "host": "broker",
                "port": 1883,
                "tls": False,
                "isolated_test_plaintext": True,
                "username": "fixture-controller",
                "acl_confirmed": True,
                "scan_period_seconds": 0.2,
            }
            all_ids = [
                value
                for zone in configuration.zones
                for value in (zone.sensor_id, zone.actuator_id)
            ]
            heartbeat = HeartbeatWorker(
                HeartbeatWorkerSettings(
                    **common, client_id="gated-fleet-heartbeat", device_ids=all_ids
                ),
                password="fixture-controller-only",
                policy=HeartbeatPolicy(offline_after_seconds=3),
            )
            control = ControlWorker(
                ControlWorkerSettings(
                    **common,
                    client_id="gated-fleet-control",
                    device_ids=[zone.actuator_id for zone in configuration.zones],
                ),
                password="fixture-controller-only",
                warehouse_lookup_factory=warehouse_lookup_for_session,
            )
            receiver = TelemetryReceiver(
                HeartbeatWorkerSettings(
                    **common,
                    client_id="gated-fleet-receiver",
                    device_ids=list(bindings),
                ),
                password="fixture-controller-only",
                warehouse_lookup_factory=warehouse_lookup_for_session,
                frontier_epochs=epochs,
            )
            delivery = TelemetryDeliveryWorker(
                engine,
                GatedTelemetrySink(engine, frontier_epochs=epochs),
                poll_seconds=0.1,
                frontier_epochs=epochs,
            )
            fleet = SimulatorFleet(configuration, password_for=passwords.__getitem__)
            for worker in (heartbeat, control, receiver, delivery, fleet):
                launch(worker)

            def online_and_saved():
                response = api.get("/api/v1/devices/", headers=headers)
                require(response.status_code == 200, "device list")
                items = response.json()["data"]
                return (
                    len(items) == 14
                    and all(item["connectivity"] == "online" for item in items)
                    and set(bindings) <= {row["device_id"] for row in saved()}
                )

            wait_for(online_and_saved)
            before = {
                device_id: sorted(
                    [row for row in saved() if row["device_id"] == device_id],
                    key=lambda row: row["first_received_at_utc"],
                )[-1]["normalized"]["metrics"]["temperature"]
                for device_id in bindings
            }
            commands = {}
            for zone in configuration.zones:
                command_id = uuid4()
                response = api.post(
                    f"/api/v1/devices/{zone.actuator_id}/commands",
                    headers=headers,
                    json={
                        "command_id": str(command_id),
                        "enabled": True,
                        "lifetime_seconds": 45,
                    },
                )
                require(response.status_code == 201, "real control command")
                commands[zone.actuator_id] = command_id
            details = {}

            def all_acknowledged():
                for device_id, command_id in commands.items():
                    response = api.get(
                        f"/api/v1/devices/{device_id}/commands/{command_id}",
                        headers=headers,
                    )
                    require(response.status_code == 200, "saved command read")
                    details[device_id] = response.json()
                return all(
                    value["status"] == "acknowledged"
                    and len(value["receipts"]) == 1
                    and value["receipts"][0]["result"] == "executed"
                    for value in details.values()
                )

            wait_for(all_acknowledged)

            def cooled_all():
                current = saved()
                return all(
                    any(
                        row["device_id"] == zone.sensor_id
                        and datetime.fromisoformat(row["normalized"]["sampled_at"])
                        > datetime.fromisoformat(
                            details[zone.actuator_id]["ack_received_at"]
                        )
                        and row["normalized"]["metrics"]["temperature"]
                        < before[zone.sensor_id] - 0.01
                        for row in current
                    )
                    for zone in configuration.zones
                )

            wait_for(cooled_all)
            require(
                all(
                    runtime.plant.state()["execution_count"] == 1
                    for runtime in fleet.runtimes
                ),
                "each physical simulation executes exactly once",
            )
            evaluation = GatedHighWorker(engine, frontier_epochs=epochs)
            outcomes = {}
            for sensor in bindings:
                row = sorted(
                    [item for item in saved() if item["device_id"] == sensor],
                    key=lambda item: item["first_received_at_utc"],
                )[-1]
                outcome = evaluation.evaluate(row["id"], now=datetime.now(UTC))
                require(
                    outcome.status == "within_rule",
                    "every active batch evaluated under the real gate",
                )
                if bindings[sensor] == warehouses[0]:
                    with engine.connect() as connection:
                        risk = connection.scalar(
                            select(observations.c.risk).where(
                                observations.c.id == row["id"]
                            )
                        )
                    require(
                        {item["batch_id"]: item["state"] for item in risk["batches"]}
                        == {str(batch): "normal" for batch in batches[:2]},
                        "both active apple batches have explicit normal verdicts",
                    )
                outcomes[str(sensor)] = outcome.status

            # A controlled historical rule switch, never rewriting old normal
            # evaluations: new real MQTT samples must name EVERY active batch.
            high_started = datetime.now(UTC)
            for batch_id in batches:
                response = api.post(
                    f"/api/v1/batches/{batch_id}/rule-snapshots",
                    headers=headers,
                    json={
                        "version_id": str(uuid4()),
                        "rule_version": "fleet-high-proof-v2",
                        "effective_start_at": high_started.isoformat(),
                        "supersedes_snapshot_ids": [rule_ids[batch_id]],
                        "rules": [
                            {
                                "metric_code": "temperature",
                                "candidate_kind": "threshold_high",
                                "severity": "warning",
                                "threshold_high": 1,
                                "unit": "°C",
                            }
                        ],
                    },
                )
                require(response.status_code == 201, "append historical high-test rule")
            high_sensors = (identity(1, 1), identity(1, 5))
            wait_for(
                lambda: all(
                    any(
                        row["device_id"] == sensor
                        and row["sampled_at_utc"] >= high_started
                        for row in saved()
                    )
                    for sensor in high_sensors
                )
            )
            high_sources = {}
            for sensor in high_sensors:
                row = max(
                    (item for item in saved() if item["device_id"] == sensor),
                    key=lambda item: item["first_received_at_utc"],
                )
                require(
                    evaluation.evaluate(row["id"], now=datetime.now(UTC)).status
                    == "candidate_staged",
                    "real MQTT sample stages high candidates",
                )
                expected_batches = set(
                    batches[:2] if sensor == high_sensors[0] else batches[2:]
                )
                with engine.connect() as connection:
                    rows = list(
                        connection.execute(
                            select(outbox).where(
                                outbox.c.observation_id == row["id"],
                                outbox.c.body["kind"].as_string() == "temperature_high",
                            )
                        ).mappings()
                    )
                require(
                    len(rows) == len(expected_batches)
                    and {UUID(item["body"]["batch_id"]) for item in rows}
                    == expected_batches,
                    "all active batches have distinct high candidates",
                )
                high_sources.update(
                    {item["source_event_id"]: item["body"]["batch_id"] for item in rows}
                )

            class RootTransport:
                def request(self, method, path, body=None):
                    response = api.request(
                        method,
                        path,
                        content=body,
                        headers={**headers, "Content-Type": "application/json"},
                    )
                    return HttpCandidateReply(response.status_code, response.content)

            high_port = BHttpCandidatePort(RootTransport())
            sender = HighDeliveryWorker(
                engine, high_port, device_ids=list(high_sensors)
            )
            sent = sender.run_once()
            require(
                {source for source, outcome in sent if outcome == "delivered"}
                == set(high_sources)
                and len(sent) == 3,
                "three distinct high events committed through authenticated B ASGI HTTP",
            )
            require(
                not HighDeliveryWorker(
                    engine, high_port, device_ids=list(high_sensors)
                ).run_once(),
                "restarted high sender does not resend delivered work",
            )
            for source, batch_id in high_sources.items():
                response = api.get(
                    f"/api/v1/candidates/by-source/{source}", headers=headers
                )
                require(
                    response.status_code == 200
                    and response.json()["batch_id"] == batch_id
                    and response.json()["rule_version"] == "fleet-high-proof-v2",
                    "B readback preserves batch and historical rule",
                )
            fleet.stop_requested.set()
            for thread, worker in zip(threads, workers, strict=True):
                if worker is fleet:
                    thread.join(10)
                    require(not thread.is_alive(), "all software devices stopped")

            def all_offline():
                response = api.get("/api/v1/devices/", headers=headers)
                require(response.status_code == 200, "offline list read")
                items = response.json()["data"]
                return len(items) == 14 and all(
                    item["connectivity"] == "offline" for item in items
                )

            wait_for(all_offline)
            old_ids = {row["id"] for row in saved()}
            restarted_at = datetime.now(UTC)
            restarted = SimulatorFleet(
                configuration, password_for=passwords.__getitem__
            )
            launch(restarted)

            def resumed():
                rows = saved()
                return online_and_saved() and set(bindings) <= {
                    row["device_id"]
                    for row in rows
                    if row["sampled_at_utc"] > restarted_at
                }

            wait_for(resumed)
            require(
                old_ids <= {row["id"] for row in saved()}, "restart retained C history"
            )
            require(
                all(
                    runtime.plant.state()["execution_count"] == 1
                    for runtime in restarted.runtimes
                ),
                "durable simulator restart did not reexecute commands",
            )
            for device_id in all_ids:
                response = api.get(
                    f"/api/v1/devices/{device_id}/connectivity-events", headers=headers
                )
                require(response.status_code == 200, "connectivity trace read")
                events = response.json()["data"]
                offline = [event for event in events if event["kind"] == "offline"]
                recovered = [event for event in events if event["kind"] == "recovered"]
                require(
                    len(offline) == 1
                    and len(recovered) == 1
                    and recovered[0]["related_source_event_id"]
                    == offline[0]["source_event_id"],
                    "offline/recovered trace links exact original event",
                )
            restarted.stop_requested.set()
            for thread, worker in zip(threads, workers, strict=True):
                if worker is restarted:
                    thread.join(10)
                    require(
                        not thread.is_alive(), "restarted fleet stopped before draining"
                    )
            receiver.stop_requested.set()
            for thread, worker in zip(threads, workers, strict=True):
                if worker is receiver:
                    thread.join(10)
                    require(not thread.is_alive(), "receiver stopped before draining")

            # Stop storage only after draining: freeze the rows checked by CSV.
            def drained():
                with engine.connect() as connection:
                    return (
                        connection.execute(
                            select(inbox.c.reception_id).limit(1)
                        ).first()
                        is None
                    )

            wait_for(drained)
            delivery.stop_requested.set()
            for thread, worker in zip(threads, workers, strict=True):
                if worker is delivery:
                    thread.join(10)
                    require(not thread.is_alive(), "storage stopped before snapshots")
            counts = {}
            for warehouse in warehouses:
                route = "/api/v1/telemetry/observations"
                response = api.get(
                    route + "/",
                    headers=headers,
                    params={"warehouse_id": str(warehouse)},
                )
                require(response.status_code == 200, "real warehouse history")
                snapshot = response.json()
                exported = api.get(
                    route + "/export.csv",
                    headers=headers,
                    params={
                        "warehouse_id": str(warehouse),
                        "snapshot_id": snapshot["snapshot_id"],
                    },
                )
                require(exported.status_code == 200, "real warehouse CSV")
                ids = {
                    row["observation_id"]
                    for row in csv.DictReader(
                        StringIO(exported.content.decode("utf-8-sig"))
                    )
                }
                expected = {
                    str(row["id"])
                    for row in saved()
                    if row["warehouse_id"] == warehouse
                }
                require(
                    ids == expected and len(ids) == snapshot["total"],
                    "same warehouse filter/snapshot exact IDs",
                )
                counts[str(warehouse)] = len(ids)
            result = {
                "phase": "gated_14_device_storage_control_segment_not_E1",
                "source": os.environ["GATED_FLEET_SOURCE"],
                "dirty": os.environ["GATED_FLEET_DIRTY"] == "1",
                "checked_at": datetime.now(UTC).isoformat(),
                "warehouses": [str(value) for value in warehouses],
                "batches": [str(value) for value in batches],
                "logical_devices": 14,
                "registered_ids_are_explicit_acl_fixtures": True,
                "actual_c01_alembic_no_test_ddl": True,
                "online_devices": 14,
                "sensor_streams_with_actual_c_data": 7,
                "actuators_with_real_execution_ack_and_later_cooling_sample": 7,
                "offline_recovered_trace_devices": 14,
                "test_offline_threshold_seconds_not_production_default": 3,
                "same_sqlite_runtime_restart_retained_execution_count": 1,
                "restart_retained_c_history_and_resumed_7_streams": True,
                "warehouse_snapshot_csv_counts": counts,
                "actual_all_active_batch_normal_outcomes": outcomes,
                "actual_multi_batch_high_mqtt_to_b_asgi_http_events": len(high_sources),
                "multi_batch_high_source_to_batch": {
                    str(source): batch for source, batch in high_sources.items()
                },
                "high_delivery_transport": "authenticated ASGI TestClient; not TCP",
                "high_sender_restart_resend_count": 0,
                "notifications_sent": 0,
                "complete_e1": False,
                "not_tested": [
                    "C recovery",
                    "two terminal receipts",
                    "second machine",
                    "full E1",
                ],
            }
    finally:
        for worker in workers:
            worker.stop_requested.set()
        for thread in threads:
            thread.join(10)
        require(
            all(not thread.is_alive() for thread in threads), "owned thread cleanup"
        )
        require(not errors, "owned worker failures " + ",".join(errors))
        engine.dispose()
    with target.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    if sys.argv[1:] == ["--preflight"]:
        preflight()
    elif not sys.argv[1:]:
        run()
    else:
        raise SystemExit("Unsupported gated fleet proof arguments")
