"""Owned-container proof of actual MQTT -> gated C -> authenticated CSV.

C01 is explicitly applied as an INACTIVE TEST PROPOSAL, not a released chain.
No notifications, recovery dispatch, original data or personal configuration.
"""

import csv
import importlib.util
import json
import os
import threading
import time
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from paho.mqtt.enums import CallbackAPIVersion
from sqlalchemy import event, func, inspect, select
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.main import app
from app.modules.iot.frontier_bootstrap import initialize_fresh_streams
from app.modules.iot.heartbeat_worker import HeartbeatWorkerSettings
from app.modules.iot.models import Device
from app.modules.iot.proof_schema_guard import require_a04_candidate_schema
from app.modules.iot.stream_frontier import read_complete_frontier
from app.modules.iot.stream_frontier_models import streams
from app.modules.iot.telemetry_inbox_models import inbox, receipts
from app.modules.iot.telemetry_storage_bridge import GatedTelemetrySink
from app.modules.iot.telemetry_worker import TelemetryDeliveryWorker, TelemetryReceiver
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session
from app.modules.telemetry.storage_candidate import observations, receptions

SENSOR = UUID("11111111-1111-4111-8111-111111111111")
TOPIC = f"freshlink/v1/devices/{SENSOR}/telemetry"


def require_empty_test_business(connection):
    """Read-only refusal BEFORE any proposal DDL or test fixture writes."""
    tables = set(inspect(connection).get_table_names())
    if any(name.startswith("telemetry_") for name in tables):
        raise RuntimeError("Preexisting C tables are not this fresh owned proof")
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
    # A real PG negative check, rolled back before any C DDL. Do not weaken the
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
    path = Path("migration_candidates/c01_telemetry_storage.py")
    spec = importlib.util.spec_from_file_location("gated_c01_test_proposal", path)
    assert spec is not None and spec.loader is not None
    proposal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proposal)
    # Only this guarded, owned, empty container DB. No activity registration.
    with engine.begin() as connection:
        require_empty_test_business(connection)
        assert (
            connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            == "a05_stream_frontier"
        )
        with patch.object(
            proposal, "op", Operations(MigrationContext.configure(connection))
        ):
            proposal.upgrade()

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
            assert not failures
            target.write_text(
                json.dumps(
                    {
                        "phase": "actual_mqtt_gated_c_http_csv_passed",
                        "checked_at": datetime.now(UTC).isoformat(),
                        "source_revision": os.environ["GATED_SOURCE_REVISION"],
                        "source_dirty": os.environ["GATED_SOURCE_DIRTY"] == "1",
                        "c_revision": "5603552718d64c36bf369d162b515dea9ce24150",
                        "actual_a05_migration": True,
                        "nonempty_database_refused_before_ddl": True,
                        "c01_inactive_test_proposal_only": True,
                        "explicit_fresh_origin": True,
                        "registered_sensor_is_fixture": True,
                        "actual_b_http_warehouse_binding": True,
                        "a_ack_after_commit_before_c": True,
                        "receiver_restart_preserves_raw": True,
                        "c_fault_rolls_back_revision_and_raw": True,
                        "same_message_duplicate_one_observation": True,
                        "incomplete_quality_retained_recovery_held": True,
                        "jwt_http_raw_same_snapshot_csv": True,
                        "mqtt_receptions": 3,
                        "observations": 2,
                        "notifications_sent": 0,
                        "complete_e1": False,
                        "not_tested": [
                            "released C01 migration",
                            "all C writer gate coverage",
                            "recovery dispatch",
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
