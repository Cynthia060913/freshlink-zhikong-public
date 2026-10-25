"""Owned-container proof of actual MQTT -> gated C -> authenticated CSV.

C01 runs through the candidate Alembic chain, not ad-hoc test DDL.
No notifications, recovery dispatch, original data or personal configuration.
"""

import csv
import json
import os
import subprocess
import sys
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
from sqlalchemy import event, func, inspect, select
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.main import app
from app.modules.alerts.models import AlertEvent
from app.modules.iot.control_worker import ControlWorker, ControlWorkerSettings
from app.modules.iot.frontier_bootstrap import (
    C_EVIDENCE_TABLES,
    initialize_fresh_streams,
)
from app.modules.iot.heartbeat_worker import HeartbeatWorker, HeartbeatWorkerSettings
from app.modules.iot.high_evaluation_worker import GatedHighWorker
from app.modules.iot.models import Device
from app.modules.iot.proof_schema_guard import require_a04_candidate_schema
from app.modules.iot.simulator_runtime import DeviceRuntime, RuntimeSettings
from app.modules.iot.stream_frontier import read_complete_frontier
from app.modules.iot.stream_frontier_models import streams
from app.modules.iot.telemetry_inbox_models import inbox, receipts
from app.modules.iot.telemetry_storage_bridge import GatedTelemetrySink
from app.modules.iot.telemetry_worker import TelemetryDeliveryWorker, TelemetryReceiver
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session
from app.modules.telemetry.b_http_candidate import (
    BHttpCandidatePort,
    HttpCandidateReply,
)
from app.modules.telemetry.delivery_candidate import deliver_once_candidate
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.storage_candidate import observations, receptions

SENSOR = UUID("11111111-1111-4111-8111-111111111111")
TOPIC = f"freshlink/v1/devices/{SENSOR}/telemetry"
ACTUATOR = UUID("66666666-6666-4666-8666-666666666666")


def prove_control_to_stored_data(
    api, headers, warehouse_id, batch_id, original_snapshot_id, delivery, rows, wait_for
):
    """Actual simulated execution and later C data in the SAME guarded database.

    Uses real B warehouse lookup, root control HTTP and existing authenticated
    broker ACL. No dependency override, manual ACK, test DDL or recovery claim.
    """
    state_file = Path("/results/gated-control-device.db")
    if state_file.exists():
        raise RuntimeError("Refusing simulator evidence overwrite")
    with Session(engine) as session:
        assert session.get(Device, ACTUATOR) is None
        session.add(
            Device(
                id=ACTUATOR,
                code="gated-control",
                name="Proof actuator",
                kind="cooling_actuator",
            )
        )
        session.commit()
    bound = api.post(
        f"/api/v1/devices/{ACTUATOR}/binding",
        headers=headers,
        json={"warehouse_id": str(warehouse_id)},
    )
    assert bound.status_code == 200
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
        HeartbeatWorkerSettings(
            **common, client_id="gated-control-heartbeat", device_ids=[SENSOR, ACTUATOR]
        ),
        password="fixture-controller-only",
    )
    control_config = ControlWorkerSettings(
        **common, client_id="gated-control-worker", device_ids=[ACTUATOR]
    )
    use_entry = os.environ.get("GATED_CONTROL_ENTRY") == "1"
    control = (
        None
        if use_entry
        else ControlWorker(
            control_config,
            password="fixture-controller-only",
            warehouse_lookup_factory=warehouse_lookup_for_session,
        )
    )
    device = DeviceRuntime(
        RuntimeSettings(
            host="broker",
            port=1883,
            tls=False,
            isolated_test_plaintext=True,
            sensor_username="test-sensor",
            actuator_username="test-actuator",
            sensor_id=SENSOR,
            actuator_id=ACTUATOR,
            warehouse_id=warehouse_id,
            state_file=state_file,
            sample_period_seconds=0.5,
            heartbeat_period_seconds=0.5,
            resend_period_seconds=1,
        ),
        sensor_password="fixture-sensor-only",
        actuator_password="fixture-actuator-only",
    )
    workers = [heartbeat, device] if control is None else [heartbeat, control, device]
    threads, errors = [], []
    process = None
    process_log = None
    result = {}
    started = datetime.now(UTC)

    def execute(worker):
        try:
            worker.run(max_seconds=45)
        except Exception as error:
            errors.append(type(error).__name__)

    def stored():
        if errors:
            raise AssertionError("Control proof worker failed: " + errors[0])
        for _ in range(10):
            outcome = delivery.run_once()
            if outcome is None:
                break
            assert outcome.status == "delivered"
        return sorted(
            [
                row
                for row in rows(observations)
                if row["first_received_at_utc"] >= started
            ],
            key=lambda row: row["first_received_at_utc"],
        )

    prefix = f"/api/v1/devices/{ACTUATOR}/commands"
    try:
        if use_entry:
            config_file = Path("/results/control-entry.json")
            if config_file.exists():
                raise RuntimeError("Refusing control entry config overwrite")
            config_file.write_text(control_config.model_dump_json(), encoding="utf-8")
            process_log = Path("/results/control-entry.log").open("x", encoding="utf-8")
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "app.modules.iot.control_service",
                    "--config",
                    str(config_file),
                    "--max-seconds",
                    "45",
                ],
                env={
                    **os.environ,
                    "FRESHLINK_CONTROL_MQTT_PASSWORD": "fixture-controller-only",
                },
                stdout=process_log,
                stderr=subprocess.STDOUT,
            )
        for worker in workers:
            thread = threading.Thread(target=execute, args=(worker,))
            threads.append(thread)
            thread.start()
        wait_for(
            lambda: (
                heartbeat.ready.is_set()
                # Process liveness is not a subscription/ACK claim; actual
                # executed ACK and later samples below remain mandatory.
                and (
                    control.ready.is_set()
                    if control is not None
                    else process.poll() is None
                )
                and api.get(f"/api/v1/devices/{ACTUATOR}", headers=headers).json()[
                    "connectivity"
                ]
                == "online"
                and bool(stored())
            )
        )
        baseline = stored()[-1]
        before = baseline["normalized"]["metrics"]["temperature"]
        command_id = uuid4()
        body = {"command_id": str(command_id), "enabled": True, "lifetime_seconds": 30}
        created = api.post(prefix, headers=headers, json=body)
        assert created.status_code == 201

        def acknowledged():
            detail = api.get(f"{prefix}/{command_id}", headers=headers)
            assert detail.status_code == 200
            value = detail.json()
            return value if value["status"] == "acknowledged" else None

        wait_for(acknowledged)
        detail = acknowledged()
        assert detail["transport_accepted_at"] is not None
        assert (
            len(detail["receipts"]) == 1
            and detail["receipts"][0]["result"] == "executed"
        )
        ack_seen = datetime.now(UTC)
        normal_rule_start = datetime.now(UTC)
        normal_rule = api.post(
            f"/api/v1/batches/{batch_id}/rule-snapshots",
            headers=headers,
            json={
                "version_id": str(uuid4()),
                "rule_version": "explicit-proof-v2-normal-range",
                "effective_start_at": normal_rule_start.isoformat(),
                "supersedes_snapshot_ids": [original_snapshot_id],
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
        assert normal_rule.status_code == 201

        def cooled():
            return [
                row
                for row in stored()
                if datetime.fromisoformat(row["normalized"]["sampled_at"])
                > max(ack_seen, normal_rule_start)
                and row["normalized"]["metrics"]["temperature"] < before - 0.02
            ]

        wait_for(cooled)
        after = cooled()[-1]
        assert (
            after["quality"] == ["ok"] and device.plant.state()["execution_count"] == 1
        )
        replay = api.post(prefix, headers=headers, json=body)
        assert replay.status_code == 201 and replay.json()["command_id"] == str(
            command_id
        )
        assert device.plant.state()["execution_count"] == 1
        result = {
            "command_id": str(command_id),
            "actuator_id": str(ACTUATOR),
            "before_observation_id": str(baseline["id"]),
            "after_observation_id": str(after["id"]),
            "temperature_before_c": before,
            "temperature_after_c": after["normalized"]["metrics"]["temperature"],
            "actual_device_execution_ack": True,
            "actual_control_process_entry": use_entry,
            "post_ack_sample_persisted_by_c": True,
            "execution_count": 1,
            "normal_rule_snapshot_id": normal_rule.json()["id"],
            "normal_rule_threshold_c": 20,
            "model": "software simulation, not physical cooling measurement",
        }
        return result
    finally:
        # Stop/join all owned threads even if child-process cleanup later fails.
        for worker in workers:
            worker.stop_requested.set()
        for thread in threads:
            thread.join(10)
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise AssertionError(
                    "Owned control process required forced cleanup"
                ) from None
            result["control_process_exit_code"] = process.returncode
            if process_log is not None:
                process_log.close()
            assert process.returncode == 0, (
                "Control entry process failed; inspect private log"
            )
        assert all(not thread.is_alive() for thread in threads), (
            "Control proof thread leak"
        )
        assert not errors, "Control proof worker error"


def require_empty_test_business(connection):
    """Require actual C01 tables, all empty, BEFORE test fixture writes."""
    tables = set(inspect(connection).get_table_names())
    if {name for name in tables if name.startswith("telemetry_")} != C_EVIDENCE_TABLES:
        raise RuntimeError("Actual complete C01 schema required; no fallback test DDL")
    quote = connection.dialect.identifier_preparer.quote
    for name in sorted(
        tables - {"alembic_version", "iot_telemetry_queue_guard", "user"}
    ):
        if (
            connection.exec_driver_sql(
                "SELECT count(*) FROM " + quote(name)
            ).scalar_one()
            != 0
        ):
            raise RuntimeError("Preexisting business data; refusing test writes")
    if connection.exec_driver_sql('SELECT count(*) FROM "user"').scalar_one() != 1:
        raise RuntimeError(
            "Expected only the newly initialized synthetic administrator"
        )


def run():
    database = urlparse(str(settings.DATABASE_URL))
    if (
        database.hostname != "test-db"
        or database.path != "/freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
        or settings.FASTAPI_ENV != "development"
        or os.getenv("FRESHLINK_GATED_PROOF") != "1"
    ):
        raise RuntimeError("Owned test database and explicit opt-in required")
    target = Path("/results/gated-telemetry.json")
    if target.exists():
        raise RuntimeError("Refusing proof overwrite")
    require_a04_candidate_schema(engine)
    with engine.begin() as connection:
        require_empty_test_business(connection)
    # A real PG negative check, rolled back before fixtures. Do not weaken the
    # guard merely because the caller selected familiar database/host names.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                Device.__table__.insert().values(
                    id=uuid4(),
                    code="must-refuse-existing",
                    name="Guard fixture",
                    kind="temperature_sensor",
                    created_at=datetime.now(UTC),
                )
            )
            try:
                require_empty_test_business(connection)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Nonempty business database guard failed")
        finally:
            transaction.rollback()
    with engine.connect() as connection:
        require_empty_test_business(connection)
        assert (
            connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            == "c01_telemetry_storage"
        )

    def rows(table):
        with engine.connect() as connection:
            return list(connection.execute(select(table)).mappings())

    def count(table):
        with engine.connect() as connection:
            return connection.scalar(select(func.count()).select_from(table))

    def wait_for(predicate, seconds=12):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if failures:
                raise AssertionError("Receiver failure: " + failures[0])
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError("Owned gated MQTT checkpoint timed out")

    failures, acknowledgements = [], []
    receiver = None
    serving = None
    publisher = mqtt.Client(
        CallbackAPIVersion.VERSION2,
        client_id="gated-proof-sensor",
        protocol=mqtt.MQTTv5,
    )
    publisher.username_pw_set("test-sensor", "fixture-sensor-only")
    connected = threading.Event()
    publisher.on_connect = lambda _c, _d, _f, reason, _p: (
        connected.set() if not reason.is_failure else None
    )
    epoch = uuid4()
    epochs = {SENSOR: epoch}

    def stop_receiver():
        if receiver is not None:
            receiver.stop_requested.set()
        if serving is not None:
            serving.join(8)
            assert not serving.is_alive()

    def start_receiver():
        nonlocal receiver, serving
        receiver = TelemetryReceiver(
            HeartbeatWorkerSettings(
                host="broker",
                tls=False,
                port=1883,
                isolated_test_plaintext=True,
                acl_confirmed=True,
                device_ids=[SENSOR],
                username="test-controller",
                client_id="gated-proof-receiver",
            ),
            password="fixture-controller-only",
            warehouse_lookup_factory=warehouse_lookup_for_session,
            frontier_epochs=epochs,
        )
        original_ack = receiver.client.ack

        def after_commit(mid, qos):
            # Another real PG connection must see A's durable pending raw.
            pending = rows(inbox)
            assert pending
            acknowledgements.append(pending[-1]["reception_id"])
            return original_ack(mid, qos)

        receiver.client.ack = after_commit

        def receive():
            try:
                receiver.run(max_seconds=90)
            except Exception as error:
                failures.append(type(error).__name__)

        serving = threading.Thread(target=receive)
        serving.start()
        wait_for(receiver.ready.is_set)

    def send(raw):
        prior = len(acknowledgements)
        publisher.publish(TOPIC, raw, qos=1).wait_for_publish(5)
        wait_for(lambda: len(acknowledgements) == prior + 1)
        saved = rows(inbox)
        assert len(saved) == 1 and saved[0]["raw_text"] == raw.decode()
        return saved[0]

    def packet(*, complete=True):
        metrics = [{"key": "temperature", "value": 11, "unit": "°C"}]
        if complete:
            metrics.append({"key": "humidity", "value": 70, "unit": "%RH"})
        return json.dumps(
            {
                "schema_version": "1.0",
                "device_id": str(SENSOR),
                "message_id": str(uuid4()),
                "sampled_at": datetime.now(UTC).isoformat(),
                "metrics": metrics,
            },
            ensure_ascii=False,
            indent=2,
        ).encode()

    sink = GatedTelemetrySink(engine, frontier_epochs=epochs)
    delivery = TelemetryDeliveryWorker(engine, sink, frontier_epochs=epochs)
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
            warehouse = api.post(
                "/api/v1/warehouses/",
                headers=headers,
                json={"code": "gated-proof", "name": "Isolated gated warehouse"},
            )
            assert warehouse.status_code == 201
            warehouse_id = UUID(warehouse.json()["id"])
            starts = (datetime.now(UTC) - timedelta(days=1)).isoformat()
            batch = api.post(
                f"/api/v1/warehouses/{warehouse_id}/batches",
                headers=headers,
                json={"code": "gated-apple", "product": "apple", "started_at": starts},
            )
            assert batch.status_code == 201
            batch_id = UUID(batch.json()["id"])
            rule = api.post(
                f"/api/v1/batches/{batch_id}/rule-snapshots",
                headers=headers,
                json={
                    "version_id": str(uuid4()),
                    "rule_version": "gated-actual-b-v1",
                    "effective_start_at": starts,
                    "rules": [
                        {
                            "metric_code": "temperature",
                            "candidate_kind": "temperature_high",
                            "severity": "warning",
                            "threshold_high": 8,
                            "unit": "°C",
                            "recovery": {
                                "comparison": "<=",
                                "threshold": 7,
                                "min_samples": 3,
                            },
                        }
                    ],
                },
            )
            assert rule.status_code == 201
            with Session(engine) as session:
                # Explicit registered fixture identity matches the test broker ACL.
                session.add(
                    Device(
                        id=SENSOR,
                        code="gated-proof-sensor",
                        name="Isolated sensor fixture",
                        kind="temperature_sensor",
                    )
                )
                session.commit()
            binding = api.post(
                f"/api/v1/devices/{SENSOR}/binding",
                headers=headers,
                json={"warehouse_id": str(warehouse_id)},
            )
            assert binding.status_code == 200
            with engine.begin() as connection:
                initialized = initialize_fresh_streams(
                    connection,
                    actor_id=UUID(actor.json()["id"]),
                    bindings={SENSOR: warehouse_id},
                    epochs=epochs,
                )
                assert initialized.created
            publisher.connect("broker", 1883)
            publisher.loop_start()
            wait_for(connected.is_set)
            start_receiver()
            raw = packet()
            pending = send(raw)
            assert count(observations) == 0
            stop_receiver()
            start_receiver()
            assert rows(inbox)[0]["reception_id"] == pending["reception_id"]
            before = rows(streams)[0]["evidence_revision"]

            fault_injected = False

            def fault(_conn, _cursor, statement, _params, _context, _many):
                nonlocal fault_injected
                if (
                    "INSERT INTO" in statement
                    and "telemetry_evaluation_candidate" in statement
                ):
                    fault_injected = True
                    raise OperationalError(
                        "synthetic", {}, RuntimeError("C write unavailable")
                    )

            event.listen(engine, "before_cursor_execute", fault)
            try:
                assert delivery.run_once().status == "retry_scheduled"
            finally:
                event.remove(engine, "before_cursor_execute", fault)
            assert fault_injected
            assert (
                count(observations) == 0
                and rows(streams)[0]["evidence_revision"] == before
            )
            delivered = []

            def retry():
                result = delivery.run_once()
                if result is not None:
                    assert result.status == "delivered"
                    delivered.append(result)
                return bool(delivered)

            wait_for(retry)
            first_id = delivered[0].observation_id
            assert count(inbox) == 0 and count(observations) == 1
            high_worker = GatedHighWorker(engine, frontier_epochs=epochs)
            high = high_worker.evaluate(first_id, now=datetime.now(UTC))
            assert (
                high.status == "candidate_staged" and high.source_event_id is not None
            )
            frozen = rows(outbox)
            assert len(frozen) == 1
            assert frozen[0]["body"]["rule_snapshot_id"] == rule.json()["id"]
            assert frozen[0]["body"]["batch_id"] == str(batch_id)

            class ActualRootTransport:
                def request(self, method, path, body=None):
                    # The coordinator must finish its local C claim before B I/O.
                    with engine.begin() as connection:
                        connection.execute(
                            select(streams).with_for_update(nowait=True)
                        ).all()
                    reply = api.request(
                        method,
                        path,
                        content=body,
                        headers={**headers, "Content-Type": "application/json"},
                    )
                    if reply.status_code == 422:
                        value = reply.json()
                        details = value.get("detail")
                        if isinstance(details, list):
                            reasons = [
                                {
                                    "loc": item.get("loc"),
                                    "type": item.get("type"),
                                    "msg": item.get("msg"),
                                }
                                for item in details
                            ]
                        else:
                            reasons = {
                                "status": value.get("status"),
                                "reason_code": value.get("reason_code"),
                            }
                        raise AssertionError(
                            "Actual B rejected candidate: " + json.dumps(reasons)
                        )
                    return HttpCandidateReply(reply.status_code, reply.content)

            port = BHttpCandidatePort(ActualRootTransport())
            assert (
                deliver_once_candidate(
                    engine,
                    source_event_id=high.source_event_id,
                    now=datetime.now(UTC),
                    port=port,
                )
                == "delivered"
            )
            actual = port.lookup(high.source_event_id)
            assert actual is not None and actual.alert_status == "open"
            assert rows(outbox)[0]["state"] == "delivered"
            assert count(AlertEvent.__table__) == 1
            assert (
                high_worker.evaluate(first_id, now=datetime.now(UTC)).status
                == "not_due"
            )
            assert (
                deliver_once_candidate(
                    engine,
                    source_event_id=high.source_event_id,
                    now=datetime.now(UTC),
                    port=port,
                )
                == "not_due"
            )
            assert count(AlertEvent.__table__) == 1
            with engine.begin() as connection:
                assert (
                    read_complete_frontier(
                        connection,
                        device_id=SENSOR,
                        warehouse_id=warehouse_id,
                        epoch=epoch,
                    )
                    is not None
                )
            send(raw)
            assert delivery.run_once().status == "delivered"
            assert count(observations) == 1 and count(receptions) == 2
            send(packet(complete=False))
            assert delivery.run_once().status == "delivered"
            assert count(observations) == 2 and count(receipts) == 3
            assert rows(streams)[0]["gap_reason"] == "unsettled_bad_evidence"
            with engine.begin() as connection:
                assert (
                    read_complete_frontier(
                        connection,
                        device_id=SENSOR,
                        warehouse_id=warehouse_id,
                        epoch=epoch,
                    )
                    is None
                )
            route = "/api/v1/telemetry/observations"
            assert api.get(route + "/").status_code == 401
            page = api.get(
                route + "/", headers=headers, params={"device_id": str(SENSOR)}
            )
            assert page.status_code == 200
            view = page.json()
            assert view["total"] == 2
            exported = api.get(
                route + "/export.csv",
                headers=headers,
                params={"device_id": str(SENSOR), "snapshot_id": view["snapshot_id"]},
            )
            assert exported.status_code == 200
            csv_ids = [
                row["observation_id"]
                for row in csv.DictReader(
                    StringIO(exported.content.decode("utf-8-sig"))
                )
            ]
            assert csv_ids == [item["observation_id"] for item in view["items"]]
            detail = api.get(route + f"/{first_id}", headers=headers)
            assert (
                detail.status_code == 200 and detail.json()["raw_text"] == raw.decode()
            )
            control_proof = prove_control_to_stored_data(
                api,
                headers,
                warehouse_id,
                batch_id,
                rule.json()["id"],
                delivery,
                rows,
                wait_for,
            )
            # Explicit rule v2 is a demonstration of historical normal-rule
            # evaluation, NOT proof of recovery under the original 8C rule.
            normal_result = high_worker.evaluate(
                UUID(control_proof["after_observation_id"]), now=datetime.now(UTC)
            )
            assert normal_result.status == "within_rule"
            assert (
                high_worker.evaluate(
                    UUID(control_proof["after_observation_id"]), now=datetime.now(UTC)
                ).status
                == "not_due"
            )
            assert port.lookup(high.source_event_id).alert_status == "open"
            assert count(AlertEvent.__table__) == 1
            # The old fixed snapshot must not change after actual new samples.
            old_export = api.get(
                route + "/export.csv",
                headers=headers,
                params={"device_id": str(SENSOR), "snapshot_id": view["snapshot_id"]},
            )
            assert (
                old_export.status_code == 200 and old_export.content == exported.content
            )
            fresh = api.get(
                route + "/", headers=headers, params={"device_id": str(SENSOR)}
            )
            assert fresh.status_code == 200 and fresh.json()["total"] > 2
            after_detail = api.get(
                route + f"/{control_proof['after_observation_id']}", headers=headers
            )
            assert after_detail.status_code == 200
            assert json.loads(after_detail.json()["raw_text"])["device_id"] == str(
                SENSOR
            )
            fresh_export = api.get(
                route + "/export.csv",
                headers=headers,
                params={
                    "device_id": str(SENSOR),
                    "snapshot_id": fresh.json()["snapshot_id"],
                },
            )
            assert fresh_export.status_code == 200
            fresh_csv_ids = [
                row["observation_id"]
                for row in csv.DictReader(
                    StringIO(fresh_export.content.decode("utf-8-sig"))
                )
            ]
            assert control_proof["after_observation_id"] in fresh_csv_ids
            assert set(fresh_csv_ids) == {str(row["id"]) for row in rows(observations)}
            assert not failures
            target.write_text(
                json.dumps(
                    {
                        "phase": "actual_mqtt_gated_c_http_csv_passed",
                        "checked_at": datetime.now(UTC).isoformat(),
                        "source_revision": os.environ["GATED_SOURCE_REVISION"],
                        "source_dirty": os.environ["GATED_SOURCE_DIRTY"] == "1",
                        "c_revision": "09823df9b7ab5c884813aa46fa7dc1a05bc3cc7f",
                        "actual_a05_migration": True,
                        "nonempty_database_refused_before_fixtures": True,
                        "actual_c01_candidate_alembic_migration": True,
                        "c01_release_status": "separate GitHub release record; not established by runtime proof",
                        "explicit_fresh_origin": True,
                        "registered_sensor_is_fixture": True,
                        "actual_b_http_warehouse_binding": True,
                        "a_ack_after_commit_before_c": True,
                        "receiver_restart_preserves_raw": True,
                        "c_fault_rolls_back_revision_and_raw": True,
                        "same_message_duplicate_one_observation": True,
                        "incomplete_quality_retained_recovery_held": True,
                        "jwt_http_raw_same_snapshot_csv": True,
                        "actual_b_batch_and_historical_rule": True,
                        "actual_gated_high_worker_call": True,
                        "actual_c_outbox_b_http_digest_and_source_readback": True,
                        "high_replay_one_b_event": True,
                        "actual_control_and_subsequent_c_data": control_proof,
                        "old_snapshot_unchanged_after_control": True,
                        "post_control_raw_http_and_same_snapshot_csv": True,
                        "actual_historical_v2_normal_gate_and_replay": True,
                        "original_high_alert_not_auto_resolved": True,
                        "mqtt_receptions": count(receipts),
                        "observations": count(observations),
                        "notifications_sent": 0,
                        "complete_e1": False,
                        "not_tested": [
                            "release approval not established by runtime proof",
                            "all C writer gate coverage",
                            "multi_batch_evaluation",
                            "normal_and_recovery_dispatch",
                            "two notification receipts",
                            "14-device full business",
                            "browser",
                            "second machine",
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    finally:
        stop_receiver()
        publisher.disconnect()
        publisher.loop_stop()


if __name__ == "__main__":
    run()
