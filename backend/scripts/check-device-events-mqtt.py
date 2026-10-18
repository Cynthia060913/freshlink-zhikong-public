"""Isolated authenticated heartbeat -> A outbox -> actual B ingestion proof.

Uses test-only B DDL, not migrations or external notification delivery.
"""

import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from platform import python_version
from urllib.parse import urlparse
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt
from fastapi.testclient import TestClient
from paho.mqtt.enums import CallbackAPIVersion
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, select

from app.core.config import settings
from app.core.db import engine
from app.main import app
from app.modules.alerts.models import AlertEvent
from app.modules.alerts.services.events import receive_device_event
from app.modules.iot.event_delivery import (
    deliver_connectivity_event,
    deliver_to_local_alerts,
)
from app.modules.iot.event_dispatch_models import checkpoints
from app.modules.iot.event_dispatch_models import metadata as dispatch_metadata
from app.modules.iot.event_worker import (
    EventDeliveryWorker,
    EventWorkerSettings,
    local_receiver,
)
from app.modules.iot.heartbeat import HeartbeatPolicy
from app.modules.iot.heartbeat_worker import HeartbeatWorker, HeartbeatWorkerSettings
from app.modules.iot.models import Device, DeviceConnectivityEvent

SENSOR = UUID("11111111-1111-4111-8111-111111111111")
UNBOUND = UUID("66666666-6666-4666-8666-666666666666")


def wait_until(predicate, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Device-event checkpoint timed out")


def continuous_phase(worker, publisher, topic, heartbeat, state, events, held_id):
    """Automatic runtime proof in the already guarded, exclusively owned DB."""
    target = Path("/results/mqtt-event-worker.json")
    if target.exists():
        raise RuntimeError("Refusing event-worker evidence overwrite")
    dispatch_metadata.create_all(engine, checkfirst=False)
    # A second real timeout, not a synthetic event inserted by this proof.
    previous = {
        row["source_event_id"]
        for row in events()
        if row["delivery_state"] == "delivered"
    }
    wait_until(lambda: state() == "offline")
    offline = next(row for row in events() if row["source_event_id"] not in previous)
    offline_id = UUID(offline["source_event_id"])
    config = EventWorkerSettings(enabled=True, device_ids=[SENSOR], poll_seconds=0.1)
    attempted = []
    failures = []

    def unavailable(payload):
        attempted.append(payload.source_event_id)
        raise OperationalError(
            "synthetic temporary B outage", {}, RuntimeError("fixture")
        )

    first = EventDeliveryWorker(engine, unavailable, config)

    def background(runtime):
        try:
            runtime.run(max_seconds=15)
        except Exception as error:
            failures.append(type(error).__name__)

    thread = threading.Thread(target=background, args=(first,))
    thread.start()
    resumed = None
    resumed_thread = None
    fresh = None
    try:

        def scheduled():
            with engine.connect() as connection:
                return (
                    connection.execute(
                        select(checkpoints).where(
                            checkpoints.c.source_event_id == offline_id
                        )
                    )
                    .mappings()
                    .first()
                )

        wait_until(
            lambda: (row := scheduled()) is not None and row["state"] == "queued"
        )
        publisher.publish(topic, heartbeat(), qos=1).wait_for_publish(5)
        wait_until(lambda: state() == "online")
        recovery = next(
            row
            for row in events()
            if row["kind"] == "recovered"
            and row["related_source_event_id"] == str(offline_id)
        )
        recovery_id = UUID(recovery["source_event_id"])
        # Stop scanning, so this proof has exactly the new pair, not another
        # timeout while the dispatcher is intentionally held unavailable.
        worker.stop_requested.set()
        first.stop_requested.set()
        thread.join(timeout=6)
        assert (
            not thread.is_alive() and attempted and set(attempted) == {str(offline_id)}
        )
        before = scheduled()
        assert before["state"] == "queued" and before["attempt_count"] >= 1
        with Session(engine) as session:
            assert (
                session.get(DeviceConnectivityEvent, recovery_id).delivery_state
                == "pending"
            )
            assert (
                session.exec(
                    select(AlertEvent).where(
                        AlertEvent.source_event_id.in_(
                            [str(offline_id), str(recovery_id)]
                        )
                    )
                ).all()
                == []
            )
        # New connection pool + new runtime object; the persisted budget, due
        # time and source body are reused. This is not an OS process restart.
        fresh = create_engine(engine.url, pool_pre_ping=True)
        accepted = []
        receive = local_receiver(fresh)

        def real_b(payload):
            accepted.append(payload.source_event_id)
            return receive(payload)

        resumed = EventDeliveryWorker(fresh, real_b, config)
        resumed_thread = threading.Thread(target=background, args=(resumed,))
        resumed_thread.start()

        def delivered_pair():
            with Session(fresh) as session:
                return all(
                    session.get(DeviceConnectivityEvent, identity).delivery_state
                    == "delivered"
                    for identity in (offline_id, recovery_id)
                )

        wait_until(delivered_pair)
        resumed.stop_requested.set()
        resumed_thread.join(timeout=6)
        assert not resumed_thread.is_alive() and not failures
        assert (
            accepted == [str(offline_id), str(recovery_id)] and resumed.completed == 2
        )
        with fresh.connect() as connection:
            pg_version = connection.exec_driver_sql("SHOW server_version").scalar_one()
            saved = (
                connection.execute(
                    select(checkpoints).where(
                        checkpoints.c.source_event_id == offline_id
                    )
                )
                .mappings()
                .one()
            )
            assert saved["attempt_count"] == before["attempt_count"] + 1
        with Session(fresh) as session:
            original = session.get(DeviceConnectivityEvent, offline_id)
            assert original.reason == offline["reason"]
            assert original.occurred_at.isoformat() == offline["occurred_at"].replace(
                "Z", "+00:00"
            )
            assert session.get(DeviceConnectivityEvent, held_id).warehouse_id is None
            stored = session.exec(
                select(AlertEvent).where(
                    AlertEvent.source_event_id.in_([str(offline_id), str(recovery_id)])
                )
            ).all()
            assert len(stored) == 2
            assert next(
                row for row in stored if row.source_event_id == str(recovery_id)
            ).related_source_event_id == str(offline_id)
        target.write_text(
            json.dumps(
                {
                    "phase": "live_mqtt_continuous_event_worker_passed",
                    "python_version": python_version(),
                    "postgresql_version": pg_version,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "offline_source": str(offline_id),
                    "recovery_source": str(recovery_id),
                    "b_new_events": 2,
                    "automatic_delivery": True,
                    "transient_failure_persisted": True,
                    "recovery_waited_for_original_a_checkpoint": True,
                    "new_worker_and_pool_kept_original_budget": True,
                    "offline_attempts_before_resume": before["attempt_count"],
                    "offline_attempts_after_resume": saved["attempt_count"],
                    "original_time_reason_relation_retained": True,
                    "unbound_not_backfilled": True,
                    "test_offline_seconds": 3,
                    "production_default_offline_seconds": 90,
                    "not_tested": [
                        "formal migration",
                        "OS process restart",
                        "notification terminal receipts",
                        "complete E1",
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        first.stop_requested.set()
        thread.join(timeout=6)
        if resumed is not None:
            resumed.stop_requested.set()
        if resumed_thread is not None:
            resumed_thread.join(timeout=6)
        if fresh is not None:
            fresh.dispose()
        assert not thread.is_alive() and (
            resumed_thread is None or not resumed_thread.is_alive()
        )


def run():
    logging.getLogger("httpx").setLevel(logging.WARNING)
    database = urlparse(str(settings.DATABASE_URL))
    if (
        database.hostname != "test-db"
        or database.path != "/freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
    ):
        raise RuntimeError("Refusing non-disposable database")
    target = Path("/results/mqtt-device-events.json")
    if target.exists():
        raise RuntimeError("Refusing evidence overwrite")
    # Exact candidate tables, solely in this disposable DB after the guards.
    for name in ("alerts_warehouse", "alerts_batch", "alerts_event"):
        SQLModel.metadata.tables[name].create(engine, checkfirst=False)
    with Session(engine) as session:
        for identity in (SENSOR, UNBOUND):
            assert session.get(Device, identity) is None
            session.add(
                Device(
                    id=identity,
                    code="event-proof-" + identity.hex,
                    name="Isolated event proof",
                    kind="temperature_sensor",
                )
            )
        session.commit()
        held_id = uuid4()
        session.add(
            DeviceConnectivityEvent(
                source_event_id=held_id,
                device_id=UNBOUND,
                warehouse_id=None,
                kind="offline",
                occurred_at=datetime.now(UTC),
                reason="synthetic_held_fixture",
                delivery_state="held_unbound",
            )
        )
        session.commit()
    assert deliver_to_local_alerts(engine, held_id).status == "held_unbound"
    worker = HeartbeatWorker(
        HeartbeatWorkerSettings.model_validate(
            {
                "host": "broker",
                "port": 1883,
                "tls": False,
                "isolated_test_plaintext": True,
                "username": "test-controller",
                "client_id": "event-proof-consumer",
                "acl_confirmed": True,
                "device_ids": [SENSOR],
                "scan_period_seconds": 0.1,
            }
        ),
        password="fixture-controller-only",
        policy=HeartbeatPolicy(offline_after_seconds=3),
    )
    failures = []

    def consume():
        try:
            worker.run(max_seconds=40)
        except Exception as error:
            failures.append(type(error).__name__)

    thread = threading.Thread(target=consume)
    publisher = mqtt.Client(
        CallbackAPIVersion.VERSION2,
        client_id="event-proof-publisher",
        protocol=mqtt.MQTTv5,
    )
    publisher.username_pw_set("test-sensor", "fixture-sensor-only")
    connected = threading.Event()
    publisher.on_connect = lambda _c, _d, _f, reason, _p: (
        connected.set() if not reason.is_failure else None
    )
    topic = f"freshlink/v1/devices/{SENSOR}/heartbeat"

    def heartbeat():
        return json.dumps(
            {
                "schema_version": "1.0",
                "device_id": str(SENSOR),
                "message_id": str(uuid4()),
                "sent_at": datetime.now(UTC).isoformat(),
            }
        ).encode()

    thread.start()
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
            warehouse = api.post(
                "/api/v1/warehouses/",
                headers=headers,
                json={"code": "event-proof", "name": "Isolated real B warehouse"},
            )
            assert warehouse.status_code == 201
            bound = api.post(
                f"/api/v1/devices/{SENSOR}/binding",
                headers=headers,
                json={"warehouse_id": warehouse.json()["id"]},
            )
            assert (
                bound.status_code == 200 and bound.json()["connectivity"] == "unknown"
            )

            def state():
                response = api.get(f"/api/v1/devices/{SENSOR}", headers=headers)
                assert response.status_code == 200
                return response.json()["connectivity"]

            def events():
                response = api.get(
                    f"/api/v1/devices/{SENSOR}/connectivity-events", headers=headers
                )
                assert response.status_code == 200
                return response.json()["data"]

            wait_until(worker.ready.is_set)
            publisher.connect("broker", 1883)
            publisher.loop_start()
            wait_until(connected.is_set)
            publisher.publish(topic, heartbeat(), qos=1).wait_for_publish(5)
            wait_until(lambda: state() == "online")
            # No synthetic offline insert: the real worker times out the real
            # heartbeat. Test policy 3s, production default remains 90s.
            wait_until(lambda: state() == "offline")
            offline = events()[0]
            assert (
                offline["kind"] == "offline" and offline["delivery_state"] == "pending"
            )
            offline_id = UUID(offline["source_event_id"])
            calls = 0

            class LostACheckpoint(Session):
                def commit(self):
                    raise OperationalError(
                        "synthetic A checkpoint", {}, RuntimeError("fixture")
                    )

            def factory():
                nonlocal calls
                calls += 1
                return LostACheckpoint(engine) if calls == 2 else Session(engine)

            def receive(payload):
                with Session(engine) as session:
                    return receive_device_event(session, payload)

            result = deliver_connectivity_event(factory, receive, offline_id)
            assert result.status == "retryable_failure"
            with Session(engine) as session:
                assert (
                    session.get(DeviceConnectivityEvent, offline_id).delivery_state
                    == "pending"
                )
                first_target = session.exec(
                    select(AlertEvent).where(
                        AlertEvent.source_event_id == str(offline_id)
                    )
                ).one()
                target_id = first_target.id
                original_target_body = first_target.payload_json
            # Real B has committed; replay must deduplicate and then checkpoint A.
            completed = deliver_to_local_alerts(engine, offline_id)
            assert (
                completed.status == "delivered"
                and completed.target_event_id == target_id
            )
            assert (
                deliver_to_local_alerts(engine, offline_id).status
                == "already_delivered"
            )
            publisher.publish(topic, heartbeat(), qos=1).wait_for_publish(5)
            wait_until(lambda: state() == "online")
            recovered = next(
                event for event in events() if event["kind"] == "recovered"
            )
            recovery_id = UUID(recovered["source_event_id"])
            assert recovery_id != offline_id and recovered[
                "related_source_event_id"
            ] == str(offline_id)

            def unavailable(_payload):
                raise OperationalError(
                    "synthetic B outage", {}, RuntimeError("fixture")
                )

            assert (
                deliver_connectivity_event(
                    lambda: Session(engine), unavailable, recovery_id
                ).status
                == "retryable_failure"
            )
            assert deliver_to_local_alerts(engine, recovery_id).status == "delivered"
            lookup = api.get(
                f"/api/v1/candidates/by-source/{recovery_id}", headers=headers
            )
            assert lookup.status_code == 200 and lookup.json()[
                "related_source_event_id"
            ] == str(offline_id)
            with Session(engine) as session:
                rows = session.exec(select(AlertEvent)).all()
                assert len(rows) == 2
                assert (
                    session.get(AlertEvent, target_id).payload_json
                    == original_target_body
                )
                saved = session.get(DeviceConnectivityEvent, offline_id)
                assert (
                    saved.reason == offline["reason"]
                    and saved.delivery_state == "delivered"
                )
                assert (
                    session.get(DeviceConnectivityEvent, held_id).warehouse_id is None
                )
            continuous_phase(
                worker, publisher, topic, heartbeat, state, events, held_id
            )
            assert not failures and worker.fatal_reason is None
            target.write_text(
                json.dumps(
                    {
                        "phase": "live_mqtt_a_outbox_b_ingestion_passed",
                        "checked_at": datetime.now(UTC).isoformat(),
                        "offline_source": str(offline_id),
                        "recovery_source": str(recovery_id),
                        "b_event_count": 2,
                        "a_mark_failed_after_b_commit_replayed_duplicate": True,
                        "b_transient_failure_kept_pending": True,
                        "recovery_references_original": True,
                        "original_reason_retained_in_a": True,
                        "unbound_fixture_not_sent_or_rewritten": True,
                        "test_offline_seconds": 3,
                        "production_default_offline_seconds": 90,
                        "b_tables": "test-only Warehouse/Batch/AlertEvent DDL; NOT formal migration",
                        "not_tested": [
                            "automatic retry scheduler/backoff",
                            "formal migration",
                            "notification dispatch/terminal receipts",
                            "C ingestion/CSV",
                            "browser",
                            "E1",
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
    finally:
        publisher.disconnect()
        publisher.loop_stop()
        worker.stop_requested.set()
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert not failures


if __name__ == "__main__":
    run()
