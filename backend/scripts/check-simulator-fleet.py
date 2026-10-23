"""Actual private-network 14-device producer/control proof, not shared E1."""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from app.modules.iot.simulator_fleet import FleetSettings, SimulatorFleet


def identity(prefix, index):
    return UUID(f"{prefix}0000000-0000-4000-8000-{index:012d}")


def run():
    if not Path("/.dockerenv").is_file() or os.getenv("FRESHLINK_FLEET_PROOF") != "1":
        raise RuntimeError("refusing non-disposable fleet proof")
    result = Path("/results/fleet.json")
    if result.exists():
        raise RuntimeError("refusing fleet evidence overwrite")
    zones, secrets = [], {}
    for index in range(1, 8):
        sensor, actuator = identity(1, index), identity(2, index)
        secrets[sensor] = f"fixture-sensor-{index}-only"
        secrets[actuator] = f"fixture-actuator-{index}-only"
        zones.append(
            {
                "host": "broker",
                "port": 1883,
                "tls": False,
                "isolated_test_plaintext": True,
                "sensor_username": f"fixture-sensor-{index}",
                "actuator_username": f"fixture-actuator-{index}",
                "sensor_id": sensor,
                "actuator_id": actuator,
                "warehouse_id": identity(3, 1 if index <= 4 else 2),
                "state_file": f"/results/fleet-state/zone-{index}.db",
                "sample_period_seconds": 0.5,
                "heartbeat_period_seconds": 0.5,
                "resend_period_seconds": 1,
            }
        )
    configuration = FleetSettings.model_validate({"zones": zones})
    fleet = SimulatorFleet(configuration, password_for=secrets.__getitem__)
    sensors = {zone.sensor_id for zone in configuration.zones}
    actuators = {zone.actuator_id for zone in configuration.zones}
    lock = threading.Lock()
    heartbeats, samples, receipts, failures = set(), {}, {}, []
    connected, subscribed = threading.Event(), threading.Event()
    controller = mqtt.Client(
        CallbackAPIVersion.VERSION2,
        client_id="fleet-proof-controller",
        protocol=mqtt.MQTTv5,
    )
    controller.username_pw_set("fixture-controller", "fixture-controller-only")

    def on_connect(client, _data, _flags, reason, _properties):
        if reason.is_failure:
            failures.append("controller_auth_rejected")
            return
        connected.set()
        client.subscribe(
            [
                ("freshlink/v1/devices/+/heartbeat", 1),
                ("freshlink/v1/devices/+/telemetry", 1),
                ("freshlink/v1/devices/+/acks", 1),
            ]
        )

    def on_subscribe(_client, _data, _mid, reasons, _properties):
        if len(reasons) == 3 and all(not reason.is_failure for reason in reasons):
            subscribed.set()
        else:
            failures.append("controller_subscription_rejected")

    def on_message(_client, _data, message):
        try:
            parts = message.topic.split("/")
            device = UUID(parts[3])
            body = json.loads(message.payload)
            with lock:
                if body.get("forged_probe"):
                    failures.append("cross_device_acl_bypass")
                elif parts[4] == "heartbeat" and device in sensors | actuators:
                    assert UUID(body["device_id"]) == device
                    heartbeats.add(device)
                elif parts[4] == "telemetry" and device in sensors:
                    assert UUID(body["device_id"]) == device
                    values = {item["key"]: item["value"] for item in body["metrics"]}
                    samples[device] = values["temperature"]
                elif parts[4] == "acks" and device in actuators:
                    receipts.setdefault(device, []).append(body)
                else:
                    failures.append("unexpected_fleet_topic")
        except Exception:
            failures.append("invalid_fleet_packet")

    controller.on_connect = on_connect
    controller.on_subscribe = on_subscribe
    controller.on_message = on_message

    def wait_until(condition, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if failures:
                raise RuntimeError(failures[0])
            if condition():
                return
            time.sleep(0.05)
        raise RuntimeError("fleet_checkpoint_timed_out")

    probe = None
    future = None
    try:
        deadline = time.monotonic() + 12
        while True:
            try:
                controller.connect("broker", 1883, keepalive=15)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("fleet_broker_not_ready") from None
                time.sleep(0.1)
        controller.loop_start()
        wait_until(lambda: connected.is_set() and subscribed.is_set())
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                future = pool.submit(fleet.run, max_seconds=60)
                wait_until(lambda: len(heartbeats) == 14 and len(samples) == 7)
                before = dict(samples)
                commands = {}
                now = datetime.now(UTC)
                for zone in configuration.zones:
                    commands[zone.actuator_id] = json.dumps(
                        {
                            "schema_version": "1.0",
                            "command_id": str(uuid4()),
                            "device_id": str(zone.actuator_id),
                            "warehouse_id": str(zone.warehouse_id),
                            "action": "set_cooling",
                            "parameters": {"enabled": True},
                            "issued_at": now.isoformat(),
                            "expires_at": (now + timedelta(seconds=30)).isoformat(),
                        }
                    )
                    controller.publish(
                        f"freshlink/v1/devices/{zone.actuator_id}/commands",
                        commands[zone.actuator_id],
                        qos=1,
                    )
                wait_until(lambda: len(receipts) == 7)
                original = {device: values[0] for device, values in receipts.items()}
                for zone in configuration.zones:
                    ack = original[zone.actuator_id]
                    assert ack["schema_version"] == "1.0"
                    assert ack["result"] == "executed" and ack["reason"] is None
                    assert UUID(ack["device_id"]) == zone.actuator_id
                    assert (
                        ack["executed_at"] and ack["state"]["cooling_enabled"] is True
                    )
                    assert (
                        ack["command_id"]
                        == json.loads(commands[zone.actuator_id])["command_id"]
                    )
                    controller.publish(
                        f"freshlink/v1/devices/{zone.actuator_id}/commands",
                        commands[zone.actuator_id],
                        qos=1,
                    )
                wait_until(
                    lambda: all(
                        len(receipts.get(device, [])) >= 2 for device in actuators
                    )
                )
                for device in actuators:
                    assert all(item == original[device] for item in receipts[device])
                wait_until(
                    lambda: all(samples[device] < before[device] for device in sensors)
                )
                after = dict(samples)
                # Authenticated sensor-1 must not be allowed to forge sensor-2.
                denied = threading.Event()
                probe_connected = threading.Event()
                probe = mqtt.Client(
                    CallbackAPIVersion.VERSION2,
                    client_id="fleet-acl-denial-proof",
                    protocol=mqtt.MQTTv5,
                )
                probe.username_pw_set("fixture-sensor-1", secrets[identity(1, 1)])
                probe.on_connect = lambda _c, _d, _f, reason, _p: (
                    probe_connected.set() if not reason.is_failure else None
                )
                probe.on_publish = lambda _c, _d, _m, reason, _p: (
                    denied.set() if reason.is_failure else None
                )
                probe.connect("broker", 1883)
                probe.loop_start()
                wait_until(probe_connected.is_set)
                probe.publish(
                    f"freshlink/v1/devices/{identity(1, 2)}/telemetry",
                    json.dumps({"forged_probe": True}),
                    qos=1,
                )
                wait_until(denied.is_set)
                assert not failures
            finally:
                fleet.stop_requested.set()
                if future is not None:
                    future.result(timeout=15)
        execution_counts = [
            runtime.plant.state()["execution_count"] for runtime in fleet.runtimes
        ]
        assert execution_counts == [1] * 7
        result.write_text(
            json.dumps(
                {
                    "phase": "actual_fourteen_device_mqtt_fleet_passed_not_E1",
                    "source": os.getenv("FLEET_SOURCE", "unknown"),
                    "source_dirty": os.getenv("FLEET_SOURCE_DIRTY", "1") == "1",
                    "warehouses": 2,
                    "zones": 7,
                    "heartbeat_device_count": len(heartbeats),
                    "telemetry_device_count": len(samples),
                    "executed_actuators": len(receipts),
                    "execution_counts_after_same_id_replay": execution_counts,
                    "same_command_original_ack_replayed": True,
                    "all_seven_temperature_decreased": all(
                        after[device] < before[device] for device in sensors
                    ),
                    "cross_device_publish_rejected": denied.is_set(),
                    "not_verified": [
                        "A registry/persistent server workers",
                        "B three batches/rules",
                        "C storage/history/CSV",
                        "two real notifications",
                        "second computer",
                        "full E1 or full 14-device application scenario",
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(  # noqa: T201 - explicit opt-in proof result, no recipient or secret
            "PASS actual 14 MQTT identities, 7 independent executions/original ACK replay/temperature decrease; cross-device publish denied. NOT E1."
        )
    finally:
        fleet.stop_requested.set()
        controller.disconnect()
        controller.loop_stop()
        if probe is not None:
            probe.disconnect()
            probe.loop_stop()


if __name__ == "__main__":
    run()
