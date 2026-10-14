"""Real continuous device process / broker test; no application DB or C/B call."""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from app.modules.iot.simulator import SoftwarePlant
from app.modules.iot.simulator_runtime import RuntimeSettings

SENSOR = "11111111-1111-4111-8111-111111111111"
ACTUATOR = "66666666-6666-4666-8666-666666666666"
WAREHOUSE = "22222222-2222-4222-8222-222222222222"


def run(directory: Path) -> None:
    if (directory / "runtime-state.db").exists():
        raise RuntimeError("Refusing to overwrite previous simulator evidence")
    configuration = RuntimeSettings.model_validate(
        {
            "host": "broker",
            "port": 1883,
            "tls": False,
            "isolated_test_plaintext": True,
            "sensor_username": "test-sensor",
            "actuator_username": "test-actuator",
            "sensor_id": SENSOR,
            "actuator_id": ACTUATOR,
            "warehouse_id": WAREHOUSE,
            "state_file": str(directory / "runtime-state.db"),
            "sample_period_seconds": 0.5,
            "heartbeat_period_seconds": 0.5,
            "resend_period_seconds": 1,
        }
    )
    config_file = directory / "runtime-config.json"
    config_file.write_text(configuration.model_dump_json(indent=2), encoding="utf-8")
    messages: list[dict] = []
    denied: list[int] = []
    ready = threading.Event()
    lock = threading.Lock()
    client = mqtt.Client(
        CallbackAPIVersion.VERSION2,
        client_id="isolated-runtime-observer",
        protocol=mqtt.MQTTv5,
    )
    client.username_pw_set("test-controller", "fixture-controller-only")

    def on_connect(observer, _data, _flags, reason, _properties):
        if reason.is_failure:
            raise RuntimeError("Disposable observer authentication failed")
        observer.subscribe(
            [
                (f"freshlink/v1/devices/{ACTUATOR}/acks", 1),
                (f"freshlink/v1/devices/{SENSOR}/telemetry", 1),
                (f"freshlink/v1/devices/{SENSOR}/heartbeat", 0),
                (f"freshlink/v1/devices/{ACTUATOR}/heartbeat", 0),
            ]
        )

    def on_subscribe(_observer, _data, _mid, reasons, _properties):
        if any(reason.is_failure for reason in reasons):
            raise RuntimeError("Disposable observer subscription denied")
        ready.set()

    def on_message(_observer, _data, message):
        with lock:
            if len(messages) >= 500:
                raise RuntimeError("Test evidence message limit reached")
            messages.append(
                {
                    "topic": message.topic,
                    "payload_text": message.payload.decode(),
                    "received_at": datetime.now(UTC).isoformat(),
                }
            )

    def on_publish(_observer, _data, _mid, reason, _properties):
        if reason.is_failure:
            denied.append(reason.value)

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.on_publish = on_publish
    client.connect_async("broker", 1883, keepalive=30)
    client.loop_start()
    processes: list[subprocess.Popen] = []
    logs = []

    def launch():
        log = (directory / "runtime-process.log").open("ab")
        logs.append(log)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.modules.iot.simulator_runtime",
                "--config",
                str(config_file),
                "--max-seconds",
                "45",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        )
        processes.append(process)
        return process

    def records(suffix):
        with lock:
            return [
                json.loads(item["payload_text"])
                for item in messages
                if item["topic"].endswith(suffix)
            ]

    def wait_for(predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            if processes and processes[-1].poll() is not None:
                raise RuntimeError(
                    "Device process exited unexpectedly; inspect private test log"
                )
            time.sleep(0.05)
        raise RuntimeError("Timed out waiting for actual runtime evidence")

    def publish(body, *, retain=False, qos=1, identifier=ACTUATOR):
        result = client.publish(
            f"freshlink/v1/devices/{identifier}/commands",
            json.dumps(body),
            qos=qos,
            retain=retain,
        )
        result.wait_for_publish(timeout=5)
        if not result.is_published():
            raise RuntimeError("Actual command broker receipt missing")

    def command(enabled=True):
        now = datetime.now(UTC)
        return {
            "schema_version": "1.0",
            "command_id": str(uuid4()),
            "device_id": ACTUATOR,
            "warehouse_id": WAREHOUSE,
            "action": "set_cooling",
            "parameters": {"enabled": enabled},
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=300)).isoformat(),
        }

    try:
        if not ready.wait(8):
            raise RuntimeError("Actual observer did not subscribe")
        first = launch()
        wait_for(
            lambda: len(records("/telemetry")) >= 3 and len(records("/heartbeat")) >= 4
        )
        plant = SoftwarePlant(
            configuration.state_file,
            actuator_id=configuration.actuator_id,
            sensor_id=configuration.sensor_id,
            warehouse_id=configuration.warehouse_id,
            started_at=datetime.now(UTC),
        )
        before = records("/telemetry")[-1]["metrics"][0]["value"]
        original = command()
        publish(original)
        wait_for(
            lambda: any(
                item["command_id"] == original["command_id"]
                for item in records("/acks")
            )
        )
        original_ack = next(
            item
            for item in records("/acks")
            if item["command_id"] == original["command_id"]
        )
        assert (
            original_ack["result"] == "executed"
            and original_ack["state"]["cooling_enabled"]
        )
        wait_for(
            lambda: records("/telemetry")[-1]["metrics"][0]["value"] < before - 0.05
        )
        controlled = records("/telemetry")[-1]["metrics"][0]["value"]
        publish(original)
        wait_for(
            lambda: (
                sum(
                    item["command_id"] == original["command_id"]
                    for item in records("/acks")
                )
                >= 2
            )
        )
        assert plant.state()["execution_count"] == 1
        # Retain-as-published catches retained injections during live subscription.
        publish(command(False), retain=True)
        wait_for(
            lambda: any(
                item["detail"].get("reason_code") == "invalid_command_transport"
                for item in plant.audit_records()
            )
        )
        client.publish(
            f"freshlink/v1/devices/{ACTUATOR}/commands", b"", qos=1, retain=True
        ).wait_for_publish(timeout=5)
        publish(command(False), qos=0)
        wait_for(
            lambda: any(
                item["detail"].get("reason_code") == "invalid_command_qos"
                for item in plant.audit_records()
            )
        )
        expired = command(False)
        expired["issued_at"] = (datetime.now(UTC) - timedelta(seconds=3)).isoformat()
        expired["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        publish(expired)
        wait_for(
            lambda: any(
                item["command_id"] == expired["command_id"] for item in records("/acks")
            )
        )
        expired_ack = next(
            item
            for item in records("/acks")
            if item["command_id"] == expired["command_id"]
        )
        assert (
            expired_ack["result"] == "failed"
            and expired_ack["reason"] == "expired"
            and expired_ack["executed_at"] is None
        )
        publish(command(False), identifier="44444444-4444-4444-8444-444444444444")
        wait_for(lambda: 135 in denied)
        first.terminate()
        first.wait(timeout=8)
        time.sleep(0.5)  # drain already received network packets
        stopped_heartbeats = len(records("/heartbeat"))
        time.sleep(2)
        assert len(records("/heartbeat")) == stopped_heartbeats
        # Queue same command while device offline; persisted broker session and
        # a genuinely new OS process deliver the original ACK, not a new execution.
        count_before = sum(
            item["command_id"] == original["command_id"] for item in records("/acks")
        )
        publish(original)
        launch()
        wait_for(
            lambda: (
                len(records("/heartbeat")) > stopped_heartbeats
                and sum(
                    item["command_id"] == original["command_id"]
                    for item in records("/acks")
                )
                > count_before
            )
        )
        matching = [
            item
            for item in records("/acks")
            if item["command_id"] == original["command_id"]
        ]
        assert all(item == original_ack for item in matching)
        assert plant.state()["execution_count"] == 1 and len(plant.pending()) >= 3
        processes[-1].terminate()
        processes[-1].wait(timeout=8)
        client.disconnect()
        client.loop_stop()
        # Freeze reception evidence before counting; no late callbacks can make
        # the exported record totals disagree with this result snapshot.
        heartbeats = records("/heartbeat")
        assert {item["device_id"] for item in heartbeats} == {SENSOR, ACTUATOR}
        assert len({item["message_id"] for item in heartbeats}) == len(heartbeats)
        telemetry = records("/telemetry")
        original_bytes = {}
        with lock:
            for reception in messages:
                body = json.loads(reception["payload_text"])
                if reception["topic"].endswith("/heartbeat"):
                    assert (
                        abs(
                            (
                                datetime.fromisoformat(reception["received_at"])
                                - datetime.fromisoformat(body["sent_at"])
                            ).total_seconds()
                        )
                        < 5
                    )
                if reception["topic"].endswith("/telemetry"):
                    identifier = body["message_id"]
                    assert (
                        original_bytes.get(identifier, reception["payload_text"])
                        == reception["payload_text"]
                    )
                    original_bytes[identifier] = reception["payload_text"]
        assert len({item["message_id"] for item in telemetry}) >= 3
        result = {
            "status": "passed",
            "evidence_type": "live_continuous_mqtt_device_process",
            "command_id": original["command_id"],
            "before_temperature_c": before,
            "controlled_temperature_c": controlled,
            "execution_count": 1,
            "heartbeat_count": len(heartbeats),
            "heartbeat_stopped_then_resumed": True,
            "identical_ACK_after_OS_process_restart": True,
            "retained_and_qos0_commands_rejected": True,
            "expired_command_failed": True,
            "cross_device_PUBACK": 135,
            "unique_observations_received": len(
                {item["message_id"] for item in telemetry}
            ),
            "observations_still_pending_C_storage": len(plant.pending()),
            "not_checked": [
                "server_online_offline_events",
                "production_TLS_credentials",
                "real_device_registration_binding",
                "C_storage",
                "B_alerts_notifications",
                "public_control_API",
                "E1",
            ],
        }
        (directory / "result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        with lock:
            (directory / "mqtt-receptions.jsonl").write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in messages)
                + "\n",
                encoding="utf-8",
            )
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=8)
        client.disconnect()
        client.loop_stop()
        for log in logs:
            log.close()


if __name__ == "__main__":
    run(Path(sys.argv[1]))
