"""Owned-network proof of four real telemetry processes and a TCP B API.

Not complete E1: no real notifications, browser or second machine. Recovery
HTTP is limited to an explicit owned-network continuation proof, never startup.
The caller must apply/check the unique candidate Alembic chain in an empty,
disposable container database before this script. No fallback DDL here.
"""

import csv
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from sqlalchemy import func, inspect, select
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.modules.alerts.models import (
    AlertEvent,
    AlertStatusTransition,
    NotificationAttempt,
)
from app.modules.iot.frontier_bootstrap import (
    C_EVIDENCE_TABLES,
    initialize_fresh_streams,
)
from app.modules.iot.models import Device
from app.modules.iot.proof_schema_guard import require_a04_candidate_schema
from app.modules.iot.stream_frontier_models import streams
from app.modules.iot.telemetry_service import TelemetryServiceSettings
from app.modules.telemetry.b_http_candidate import (
    BHttpCandidatePort,
    UrllibCandidateTransport,
)
from app.modules.telemetry.delivery_candidate import deliver_once_candidate
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.recovery_freshness_candidate import (
    check_recovery_freshness_candidate,
)
from app.modules.telemetry.storage_candidate import evaluation_jobs, observations

SENSOR = UUID("11111111-1111-4111-8111-111111111111")


def run():
    prepare_recovery = os.environ.get("FRESHLINK_RECOVERY_PREPARATION_PROOF") == "1"
    continue_recovery = os.environ.get("FRESHLINK_RECOVERY_CONTINUATION_PROOF") == "1"
    watch_recovery = os.environ.get("FRESHLINK_RECOVERY_WATCH_PROOF") == "1"
    linked_correction = os.environ.get("FRESHLINK_LINKED_CORRECTION_PROOF") == "1"
    if linked_correction and not watch_recovery:
        raise RuntimeError("Linked correction proof requires live recovery preparation")
    if continue_recovery and not prepare_recovery:
        raise RuntimeError("Continuation requires the explicit recovery proof")
    if watch_recovery and not continue_recovery:
        raise RuntimeError("Preparation watch requires the continuation proof")
    database = urlparse(str(settings.DATABASE_URL))
    if (
        database.hostname != "test-db"
        or database.path != "/freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
        or settings.FASTAPI_ENV != "development"
        or os.environ.get("FRESHLINK_GATED_PROOF") != "1"
    ):
        raise RuntimeError("Owned disposable test environment required")
    result_file = Path("/results/runtime-composition.json")
    if result_file.exists():
        raise RuntimeError("Refusing proof overwrite")
    require_a04_candidate_schema(engine)
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        assert {n for n in tables if n.startswith("telemetry_")} == C_EVIDENCE_TABLES
        quote = connection.dialect.identifier_preparer.quote
        for name in sorted(
            tables - {"alembic_version", "iot_telemetry_queue_guard", "user"}
        ):
            if connection.exec_driver_sql(
                "SELECT count(*) FROM " + quote(name)
            ).scalar_one():
                raise RuntimeError("Refusing preexisting business data")
        assert (
            connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            == "c01_telemetry_storage"
        )
        assert (
            connection.exec_driver_sql('SELECT count(*) FROM "user"').scalar_one() == 1
        )

    owned = []
    expected_finished = set()
    publisher = None
    result = {}

    def start(name, arguments, extra_env=None):
        log = Path(f"/results/runtime-{name}.log").open("x", encoding="utf-8")
        try:
            process = subprocess.Popen(
                [sys.executable, "-B", *arguments],
                env={**os.environ, **(extra_env or {})},
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        except BaseException:
            log.close()
            raise
        owned.append((name, process, log))
        return process

    def wait_for(predicate, seconds=25):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if predicate():
                return
            # Expected-failure scope probe is excluded, other early exits fail.
            for name, process, _ in owned:
                if (
                    name
                    not in {
                        "uninitialized",
                        "prepare-recovery",
                        "check-recovery",
                        "check-stale",
                        "refresh-recovery",
                        "watch-recovery",
                    }
                    and name not in expected_finished
                    and process.poll() is not None
                ):
                    raise AssertionError("Runtime child exited: " + name)
            time.sleep(0.1)
        raise AssertionError("Owned process checkpoint timed out")

    def rows(table):
        with engine.connect() as connection:
            return list(connection.execute(select(table)).mappings())

    try:
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
            base_url="http://127.0.0.1:18080",
            timeout=5,
            trust_env=False,
            follow_redirects=False,
        ) as api:

            def healthy():
                try:
                    return api.get("/api/v1/utils/health-check/").status_code == 200
                except httpx.ConnectError:
                    return False

            wait_for(healthy)
            login = api.post(
                "/api/v1/login/access-token",
                data={
                    "username": settings.FIRST_SUPERUSER,
                    "password": settings.FIRST_SUPERUSER_PASSWORD,
                },
            )
            assert login.status_code == 200
            bearer = login.json()["access_token"]
            api.headers["Authorization"] = "Bearer " + bearer
            actor = api.get("/api/v1/users/me")
            assert actor.status_code == 200
            warehouse = api.post(
                "/api/v1/warehouses/",
                json={"code": "runtime-proof", "name": "Owned process warehouse"},
            )
            assert warehouse.status_code == 201
            warehouse_id = UUID(warehouse.json()["id"])
            starts = (datetime.now(UTC) - timedelta(days=1)).isoformat()
            batch = api.post(
                f"/api/v1/warehouses/{warehouse_id}/batches",
                json={
                    "code": "runtime-apple",
                    "product": "apple",
                    "started_at": starts,
                },
            )
            assert batch.status_code == 201
            rule = api.post(
                f"/api/v1/batches/{batch.json()['id']}/rule-snapshots",
                json={
                    "version_id": str(uuid4()),
                    "rule_version": "runtime-v1",
                    "effective_start_at": starts,
                    "rules": [
                        {
                            "metric_code": "temperature",
                            "candidate_kind": "temperature_high",
                            "severity": "warning",
                            "threshold_high": 8,
                            "unit": "°C",
                            **(
                                {
                                    "recovery": {
                                        "comparison": "<=",
                                        "threshold": 7,
                                        "min_samples": 3,
                                    }
                                }
                                if prepare_recovery
                                else {}
                            ),
                        }
                    ],
                },
            )
            assert rule.status_code == 201
            # Explicit fixed-ID ACL fixture, not a registration-UI acceptance.
            with Session(engine) as session:
                session.add(
                    Device(
                        id=SENSOR,
                        code="runtime-sensor",
                        name="Owned runtime sensor",
                        kind="temperature_sensor",
                    )
                )
                session.commit()
            binding = api.post(
                f"/api/v1/devices/{SENSOR}/binding",
                json={"warehouse_id": str(warehouse_id)},
            )
            assert binding.status_code == 200
            epoch = uuid4()
            config = TelemetryServiceSettings(
                host="broker",
                port=1883,
                tls=False,
                isolated_test_plaintext=True,
                acl_confirmed=True,
                username="test-controller",
                client_id="runtime-proof-receiver",
                device_ids=[SENSOR],
                frontier_epochs={SENSOR: epoch},
                scan_period_seconds=0.1,
            )
            config_file = Path("/results/runtime-telemetry.json")
            with config_file.open("x", encoding="utf-8") as stream:
                stream.write(config.model_dump_json())

            def role_arguments(role):
                return [
                    "-m",
                    "app.modules.iot.telemetry_service",
                    "--role",
                    role,
                    "--config",
                    str(config_file),
                    "--max-seconds",
                    "90",
                ]

            negative = start("uninitialized", role_arguments("store"))
            assert negative.wait(timeout=15) == 1
            assert (
                "telemetry role failed; check private configuration and scope"
                in Path("/results/runtime-uninitialized.log").read_text(
                    encoding="utf-8"
                )
            )
            assert rows(streams) == [] and rows(observations) == []
            with engine.begin() as connection:
                initialize_fresh_streams(
                    connection,
                    actor_id=UUID(actor.json()["id"]),
                    bindings={SENSOR: warehouse_id},
                    epochs={SENSOR: epoch},
                )
            for role in ("receive", "store", "evaluate", "deliver-high"):
                # Each role receives ONLY its own credential, never argv/JSON.
                credentials = {}
                if role == "receive":
                    credentials["FRESHLINK_TELEMETRY_MQTT_PASSWORD"] = (
                        "fixture-controller-only"
                    )
                if role == "deliver-high":
                    credentials = {
                        "FRESHLINK_B_INTERNAL_ORIGIN": "http://127.0.0.1:18080",
                        "FRESHLINK_B_SERVICE_BEARER": bearer,
                    }
                start(role, role_arguments(role), credentials)
            connected = threading.Event()
            publisher = mqtt.Client(
                CallbackAPIVersion.VERSION2,
                client_id="runtime-proof-sensor",
                protocol=mqtt.MQTTv5,
            )
            publisher.username_pw_set("test-sensor", "fixture-sensor-only")
            publisher.on_connect = lambda _c, _d, _f, reason, _p: (
                connected.set() if not reason.is_failure else None
            )
            publisher.connect("broker", 1883)
            publisher.loop_start()
            wait_for(connected.is_set)
            message_id = uuid4()
            raw = json.dumps(
                {
                    "schema_version": "1.0",
                    "device_id": str(SENSOR),
                    "message_id": str(message_id),
                    "sampled_at": datetime.now(UTC).isoformat(),
                    "metrics": [
                        {"key": "temperature", "value": 11, "unit": "°C"},
                        {"key": "humidity", "value": 70, "unit": "%RH"},
                    ],
                },
                ensure_ascii=False,
            ).encode()
            topic = f"freshlink/v1/devices/{SENSOR}/telemetry"
            # Reuse the SAME packet if receiver subscription is still starting.
            # The real message-id deduplication remains exercised, not bypassed.
            last_publish = 0.0

            def persisted():
                nonlocal last_publish
                if time.monotonic() - last_publish >= 1:
                    publisher.publish(topic, raw, qos=1).wait_for_publish(5)
                    last_publish = time.monotonic()
                return bool(rows(observations))

            wait_for(persisted)
            wait_for(
                lambda: (
                    len(rows(outbox)) == 1 and rows(outbox)[0]["state"] == "delivered"
                )
            )
            saved = rows(observations)
            assert len(saved) == 1 and saved[0]["quality"] == ["ok"]
            assert UUID(str(rows(AlertEvent.__table__)[0]["source_event_id"])) == UUID(
                str(rows(outbox)[0]["source_event_id"])
            )
            with engine.connect() as connection:
                assert (
                    connection.scalar(
                        select(func.count()).select_from(AlertEvent.__table__)
                    )
                    == 1
                )
            route = "/api/v1/telemetry/observations"
            page = api.get(route + "/", params={"device_id": str(SENSOR)})
            assert page.status_code == 200 and page.json()["total"] == 1
            exported = api.get(
                route + "/export.csv",
                params={
                    "device_id": str(SENSOR),
                    "snapshot_id": page.json()["snapshot_id"],
                },
            )
            assert exported.status_code == 200
            csv_ids = [
                row["observation_id"]
                for row in csv.DictReader(
                    StringIO(exported.content.decode("utf-8-sig"))
                )
            ]
            assert csv_ids == [row["observation_id"] for row in page.json()["items"]]
            detail = api.get(route + f"/{saved[0]['id']}")
            assert (
                detail.status_code == 200 and detail.json()["raw_text"] == raw.decode()
            )
            prepared = False
            continued = False
            corrected = False
            control_result = None
            if prepare_recovery:
                original_id = rows(outbox)[0]["source_event_id"]
                # All three normal samples enter through authenticated MQTT and
                # the actual receive/store processes, not direct fixture rows.
                for index in range(3):
                    packet = json.loads(raw)
                    packet["message_id"] = str(uuid4())
                    packet["sampled_at"] = datetime.now(UTC).isoformat()
                    packet["metrics"][0]["value"] = 6
                    publisher.publish(
                        topic, json.dumps(packet).encode(), qos=1
                    ).wait_for_publish(5)
                    wait_for(
                        lambda expected=index + 2: len(rows(observations)) == expected
                    )
                # Default legacy proof quiesces workers. The opt-in extension
                # instead waits for persisted evaluation and keeps them alive.
                if continue_recovery:
                    wait_for(
                        lambda: (
                            len(rows(evaluation_jobs)) == 4
                            and all(
                                job["state"] in {"within_rule", "candidate_staged"}
                                for job in rows(evaluation_jobs)
                            )
                        )
                    )
                else:
                    for name, process, _ in owned:
                        if name in {"receive", "store", "evaluate", "deliver-high"}:
                            process.terminate()
                            assert process.wait(timeout=12) == 0
                cli = start(
                    "watch-recovery" if watch_recovery else "prepare-recovery",
                    [
                        "-m",
                        "app.modules.iot.recovery_observation_service",
                        "--config",
                        str(config_file),
                        *(
                            ["--watch-preparation", "--max-seconds", "120"]
                            if watch_recovery
                            else ["--original-source-event-id", str(original_id)]
                        ),
                    ],
                )
                if watch_recovery:
                    wait_for(
                        lambda: any(
                            row["related_source_event_id"] == original_id
                            and row["state"] == "pending"
                            for row in rows(outbox)
                        )
                    )
                    cli.terminate()
                assert cli.wait(timeout=20) == 0
                summary = json.loads(
                    Path(
                        "/results/runtime-watch-recovery.log"
                        if watch_recovery
                        else "/results/runtime-prepare-recovery.log"
                    ).read_text(encoding="utf-8")
                )
                if watch_recovery:
                    assert summary["results"]["observation_staged"] == 1
                else:
                    assert summary["results"] == {"progress": 2, "staged": 1}
                child = [
                    row
                    for row in rows(outbox)
                    if row["related_source_event_id"] == original_id
                ]
                assert len(child) == 1
                assert child[0]["state"] == "pending" and child[0]["attempt_count"] == 0
                assert child[0]["frozen_stream_epoch"] == epoch
                assert len(rows(AlertEvent.__table__)) == 1
                check_cli = start(
                    "check-recovery",
                    [
                        "-m",
                        "app.modules.iot.recovery_observation_service",
                        "--config",
                        str(config_file),
                        "--check-recovery-id",
                        str(child[0]["source_event_id"]),
                    ],
                )
                assert check_cli.wait(timeout=20) == 0
                checked = json.loads(
                    Path("/results/runtime-check-recovery.log").read_text(
                        encoding="utf-8"
                    )
                )
                assert checked["results"] == {"ready_at_check": 1}
                assert (
                    checked["scope"] == "readiness_at_check_only_not_send_authorization"
                )
                assert len(rows(AlertEvent.__table__)) == 1
                assert [
                    row
                    for row in rows(outbox)
                    if row["related_source_event_id"] == original_id
                ][0]["attempt_count"] == 0
                prepared = True
                if continue_recovery:
                    # Keep all four workers alive; a REAL new MQTT admission
                    # invalidates the older freeze, then C revalidates it.
                    packet = json.loads(raw)
                    packet["message_id"] = str(uuid4())
                    packet["sampled_at"] = datetime.now(UTC).isoformat()
                    packet["metrics"][0]["value"] = 6
                    publisher.publish(
                        topic, json.dumps(packet).encode(), qos=1
                    ).wait_for_publish(5)
                    wait_for(
                        lambda: (
                            len(rows(observations)) == 5
                            and len(rows(evaluation_jobs)) == 5
                            and all(
                                job["state"] in {"within_rule", "candidate_staged"}
                                for job in rows(evaluation_jobs)
                            )
                        )
                    )
                    recovery_id = child[0]["source_event_id"]
                    stale = start(
                        "check-stale",
                        [
                            "-m",
                            "app.modules.iot.recovery_observation_service",
                            "--config",
                            str(config_file),
                            "--check-recovery-id",
                            str(recovery_id),
                        ],
                    )
                    assert stale.wait(timeout=20) == 2
                    assert json.loads(
                        Path("/results/runtime-check-stale.log").read_text()
                    )["results"] == {"held": 1}
                    refresh = start(
                        "watch-restarted" if watch_recovery else "refresh-recovery",
                        [
                            "-m",
                            "app.modules.iot.recovery_observation_service",
                            "--config",
                            str(config_file),
                            *(
                                ["--watch-preparation", "--max-seconds", "120"]
                                if watch_recovery
                                else ["--refresh-recovery-id", str(recovery_id)]
                            ),
                        ],
                    )
                    if watch_recovery:
                        wait_for(
                            lambda: any(
                                row["source_event_id"] == recovery_id
                                and row["frozen_admitted_seq"]
                                > child[0]["frozen_admitted_seq"]
                                for row in rows(outbox)
                            )
                        )
                        assert refresh.poll() is None
                    else:
                        assert refresh.wait(timeout=20) == 0
                        assert json.loads(
                            Path("/results/runtime-refresh-recovery.log").read_text()
                        )["results"] == {"refrozen": 1}
                    assert (
                        check_recovery_freshness_candidate(
                            engine,
                            recovery_source_event_id=recovery_id,
                            device_id=SENSOR,
                            warehouse_id=warehouse_id,
                            stream_epoch=epoch,
                        ).status
                        == "ready_at_check"
                    )
                    port = BHttpCandidatePort(
                        UrllibCandidateTransport(
                            base_url="http://127.0.0.1:18080",
                            bearer=lambda: bearer,
                        )
                    )
                    assert (
                        deliver_once_candidate(
                            engine,
                            source_event_id=recovery_id,
                            now=datetime.now(UTC),
                            port=port,
                        )
                        == "delivered"
                    )
                    original_receipt = port.lookup(original_id)
                    assert (
                        original_receipt is not None
                        and original_receipt.alert_status == "resolved"
                    )
                    assert len(rows(AlertEvent.__table__)) == 2
                    assert len(rows(AlertStatusTransition.__table__)) == 1
                    assert rows(NotificationAttempt.__table__) == []
                    final_child = [
                        row
                        for row in rows(outbox)
                        if row["source_event_id"] == recovery_id
                    ][0]
                    assert (final_child["state"], final_child["attempt_count"]) == (
                        "delivered",
                        1,
                    )
                    assert final_child["body"] == child[0]["body"]
                    assert final_child["body_sha256"] == child[0]["body_sha256"]
                    assert all(
                        process.poll() is None
                        for name, process, _ in owned
                        if name in {"receive", "store", "evaluate", "deliver-high"}
                    )
                    continued = True
                    if linked_correction:
                        # A real conflicting replay reaches the C reception
                        # path after B has already resolved this anomaly.
                        damaged = json.loads(raw)
                        damaged["metrics"][0]["value"] = 99
                        publisher.publish(
                            topic, json.dumps(damaged).encode(), qos=1
                        ).wait_for_publish(5)

                        def corrections():
                            return [
                                row
                                for row in rows(outbox)
                                if row["body"].get("kind") == "data_quality"
                                and row["related_source_event_id"] == original_id
                            ]

                        wait_for(lambda: len(corrections()) == 1)
                        held_correction = corrections()[0]
                        assert (
                            held_correction["state"],
                            held_correction["attempt_count"],
                        ) == (
                            "held_contract",
                            0,
                        )
                        sender = start(
                            "deliver-correction",
                            [
                                "-m",
                                "app.modules.iot.telemetry_service",
                                "--role",
                                "deliver-correction",
                                "--config",
                                str(config_file),
                                "--confirm-correction-contract",
                                "--max-seconds",
                                "120",
                            ],
                            {
                                "FRESHLINK_B_INTERNAL_ORIGIN": "http://127.0.0.1:18080",
                                "FRESHLINK_B_SERVICE_BEARER": bearer,
                            },
                        )
                        wait_for(lambda: corrections()[0]["state"] == "delivered")
                        sender.terminate()
                        assert sender.wait(timeout=12) == 0
                        expected_finished.add("deliver-correction")
                        final_correction = corrections()[0]
                        assert final_correction["body"] == held_correction["body"]
                        assert (
                            final_correction["body_sha256"]
                            == held_correction["body_sha256"]
                        )
                        assert final_correction["attempt_count"] == 1
                        assert port.lookup(original_id).alert_status == "resolved"
                        readback = api.get(
                            "/api/v1/candidates/by-source/"
                            + str(final_correction["source_event_id"])
                        )
                        assert readback.status_code == 200
                        assert readback.json()["related_source_event_id"] == str(
                            original_id
                        )
                        assert readback.json()["kind"] == "data_quality"
                        assert len(rows(AlertEvent.__table__)) == 3
                        assert len(rows(AlertStatusTransition.__table__)) == 1
                        assert rows(NotificationAttempt.__table__) == []
                        corrected = True
                        # Reuse the existing actual control/device proof. C is
                        # drained by its real store process here, not a manual
                        # fixture driver or another in-process delivery loop.
                        spec = importlib.util.spec_from_file_location(
                            "freshlink_owned_control_proof",
                            Path(__file__).with_name("check-gated-telemetry.py"),
                        )
                        if spec is None or spec.loader is None:
                            raise RuntimeError("Existing control proof unavailable")
                        control_proof = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(control_proof)
                        os.environ["GATED_CONTROL_ENTRY"] = "1"
                        control_result = control_proof.prove_control_to_stored_data(
                            api,
                            {},
                            warehouse_id,
                            batch.json()["id"],
                            rule.json()["id"],
                            None,
                            rows,
                            wait_for,
                        )
                        assert control_result["actual_device_execution_ack"]
                        assert control_result["actual_control_process_entry"]
                        assert control_result["post_ack_sample_persisted_by_c"]
                        assert all(
                            process.poll() is None
                            for name, process, _ in owned
                            if name in {"receive", "store", "evaluate", "deliver-high"}
                        )
            result = {
                "phase": "actual_runtime_processes_tcp_b_csv_passed",
                "source_revision": os.environ["GATED_SOURCE_REVISION"],
                "source_dirty": os.environ["GATED_SOURCE_DIRTY"] == "1",
                "checked_at": datetime.now(UTC).isoformat(),
                "actual_runtime_processes": [
                    "receive",
                    "store",
                    "evaluate",
                    "deliver-high",
                ],
                "actual_tcp_http_api": True,
                "uninitialized_scope_refused": True,
                "actual_candidate_c01_migration": True,
                "one_observation_one_b_event": True,
                "raw_preserved": True,
                "same_snapshot_csv": True,
                "explicit_recovery_cli_staged_without_delivery": prepared
                and not continued,
                "new_mqtt_admission_refrozen_while_workers_live": continued,
                "actual_recovery_http_and_resolved_readback": continued,
                "actual_preparation_watch_restart": watch_recovery and continued,
                "actual_late_mqtt_correction_background_tcp": corrected,
                "actual_control_and_subsequent_c_data": control_result,
                "notifications_sent": 0,
                "complete_e1": False,
                "not_tested": [
                    "unattended recovery scheduling"
                    if corrected
                    else "unattended recovery scheduling and late-evidence correction"
                    if continued
                    else "recovery",
                    "notifications",
                    *([] if control_result else ["control in this run"]),
                    "browser",
                    "14 devices",
                    "second machine",
                    "release approval",
                ],
            }
    finally:
        if publisher is not None:
            publisher.disconnect()
            publisher.loop_stop()
        forced, exits, terminated = [], {}, set()
        # Clean every owned process even if an earlier child fails to stop.
        for name, process, log in reversed(owned):
            if process.poll() is None:
                terminated.add(name)
                process.terminate()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                forced.append(name)
            finally:
                exits[name] = process.returncode
                log.close()
        engine.dispose()
        with Path("/results/runtime-child-cleanup.json").open(
            "x", encoding="utf-8"
        ) as stream:
            json.dump(
                {
                    "child_exit_codes": exits,
                    "forced": forced,
                    "terminated_by_owner": sorted(terminated),
                },
                stream,
                indent=2,
            )
        assert not forced, "Owned runtime process forced cleanup required"
        assert all(
            code == 0
            or (name == "check-stale" and code == 2)
            # Uvicorn deliberately re-raises an owner-sent SIGTERM after its
            # graceful ASGI shutdown. Other runtime roles must return zero.
            or (name == "api" and name in terminated and code == -signal.SIGTERM)
            for name, code in exits.items()
            if name != "uninitialized"
        ), "Owned runtime process failed; inspect private logs"
        if result:
            result["child_exit_codes"] = exits
    with result_file.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    sys.stdout.write(
        "PASS actual runtime processes/TCP B/raw/history/CSV; NOT FULL E1\n"
    )


if __name__ == "__main__":
    run()
