import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode
from pydantic import ValidationError

from app.modules.iot.simulator import SimulatorRejected, SoftwarePlant
from app.modules.iot.simulator_runtime import (
    CommandDelivery,
    DeviceRuntime,
    RuntimeSettings,
    main,
)

SENSOR = UUID("11111111-1111-4111-8111-111111111111")
ACTUATOR = UUID("66666666-6666-4666-8666-666666666666")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")


def settings(path: Path, **changes: object) -> RuntimeSettings:
    values = {
        "host": "localhost",
        "port": 1883,
        "tls": False,
        "isolated_test_plaintext": True,
        "sensor_username": "sensor",
        "actuator_username": "actuator",
        "sensor_id": SENSOR,
        "actuator_id": ACTUATOR,
        "warehouse_id": WAREHOUSE,
        "state_file": path,
    }
    values.update(changes)
    return RuntimeSettings.model_validate(values)


def runtime(path: Path) -> DeviceRuntime:
    return DeviceRuntime(
        settings(path), sensor_password="disposable", actuator_password="disposable"
    )


def command(now: datetime) -> bytes:
    return json.dumps(
        {
            "schema_version": "1.0",
            "command_id": str(uuid4()),
            "device_id": str(ACTUATOR),
            "warehouse_id": str(WAREHOUSE),
            "action": "set_cooling",
            "parameters": {"enabled": True},
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=30)).isoformat(),
        }
    ).encode()


def info(*, published: bool, code: int = 0) -> MagicMock:
    result = MagicMock()
    result.rc = code
    result.is_published.return_value = published
    return result


@pytest.mark.parametrize(
    "changes",
    [
        {"isolated_test_plaintext": False},
        {"host": "external.example.com"},
        {"actuator_id": SENSOR},
        {"port": 0},
        {"sample_period_seconds": 31},
        {"heartbeat_period_seconds": 0},
        {"extra": "unexpected"},
    ],
)
def test_runtime_configuration_is_bounded_and_secure(
    tmp_path: Path, changes: dict
) -> None:
    with pytest.raises(ValidationError):
        settings(tmp_path / "state.db", **changes)


def test_runtime_tls_default_and_missing_secret(tmp_path: Path) -> None:
    cfg = settings(tmp_path / "state.db", tls=True, isolated_test_plaintext=False)
    with pytest.raises(ValueError, match="secrets"):
        DeviceRuntime(cfg, sensor_password="", actuator_password="x")
    instance = DeviceRuntime(cfg, sensor_password="hidden", actuator_password="hidden")
    assert instance.sensor is not instance.actuator
    assert "hidden" not in repr(instance.settings)


def test_callbacks_connect_subscribe_and_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = runtime(tmp_path / "state.db")
    subscribe = MagicMock(return_value=(mqtt.MQTT_ERR_SUCCESS, 1))
    monkeypatch.setattr(instance.actuator, "subscribe", subscribe)
    success = ReasonCode(PacketTypes.CONNACK, identifier=0)
    instance._on_connect(instance.sensor, None, mqtt.ConnectFlags(False), success, None)
    assert instance.ready_sensor.is_set()
    instance._on_connect(
        instance.actuator, None, mqtt.ConnectFlags(False), success, None
    )
    options = subscribe.call_args.kwargs["options"]
    assert options.QoS == 1 and options.retainAsPublished
    instance._on_subscribe(
        instance.actuator, None, 1, [ReasonCode(PacketTypes.SUBACK, identifier=1)], None
    )
    assert instance.ready_actuator.is_set()
    instance._on_disconnect(
        instance.actuator,
        None,
        mqtt.DisconnectFlags(False),
        ReasonCode(PacketTypes.DISCONNECT, identifier=0),
        None,
    )
    assert not instance.ready_actuator.is_set()
    instance._on_disconnect(
        instance.sensor,
        None,
        mqtt.DisconnectFlags(False),
        ReasonCode(PacketTypes.DISCONNECT, identifier=0),
        None,
    )
    assert not instance.ready_sensor.is_set()
    instance._on_subscribe(instance.actuator, None, 1, [], None)
    assert instance.fatal_reason == "mqtt_subscription_rejected"


def test_auth_publish_and_subscribe_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = runtime(tmp_path / "auth.db")
    first._on_connect(
        first.sensor,
        None,
        mqtt.ConnectFlags(False),
        ReasonCode(PacketTypes.CONNACK, identifier=135),
        None,
    )
    assert first.fatal_reason == "mqtt_authentication_rejected"
    second = runtime(tmp_path / "pub.db")
    second._on_publish(
        second.sensor, None, 1, ReasonCode(PacketTypes.PUBACK, identifier=135), None
    )
    assert second.fatal_reason == "mqtt_publish_rejected"
    third = runtime(tmp_path / "sub.db")
    monkeypatch.setattr(
        third.actuator, "subscribe", MagicMock(return_value=(mqtt.MQTT_ERR_NO_CONN, 1))
    )
    third._on_connect(
        third.actuator,
        None,
        mqtt.ConnectFlags(False),
        ReasonCode(PacketTypes.CONNACK, identifier=0),
        None,
    )
    assert third.fatal_reason == "mqtt_subscribe_failed"
    fourth = runtime(tmp_path / "denied.db")
    fourth._on_subscribe(
        fourth.actuator, None, 1, [ReasonCode(PacketTypes.SUBACK, identifier=135)], None
    )
    assert fourth.fatal_reason == "mqtt_subscription_rejected"


def test_bounded_callback_queue_and_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = runtime(tmp_path / "state.db")
    message = mqtt.MQTTMessage(1)
    message.topic = f"freshlink/v1/devices/{ACTUATOR}/commands".encode()
    message.payload = b"{}"
    message.qos = 1
    for _ in range(33):
        instance._on_message(instance.actuator, None, message)
    assert instance.commands.qsize() == 32
    assert instance.fatal_reason == "command_queue_full"
    assert not instance.wait_ready(timeout=0.1)
    disconnects = []
    for client in (instance.sensor, instance.actuator):
        monkeypatch.setattr(client, "disconnect", lambda: disconnects.append(True))
        monkeypatch.setattr(client, "loop_stop", lambda: None)
    instance.stop()
    assert len(disconnects) == 2


def test_command_transaction_then_manual_transport_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = runtime(tmp_path / "state.db")
    now = datetime.fromisoformat(instance.plant.state()["stepped_at"]) + timedelta(
        seconds=1
    )
    acknowledger = MagicMock()
    monkeypatch.setattr(instance.actuator, "ack", acknowledger)
    payload = command(now)
    instance.commands.put(
        CommandDelivery(
            f"freshlink/v1/devices/{ACTUATOR}/commands", payload, False, 1, 1
        )
    )
    instance._execute_commands(now)
    acknowledger.assert_called_once_with(1, 1)
    assert instance.plant.state()["execution_count"] == 1
    identifier, original = instance.plant.pending_acknowledgements()[0]
    instance.plant.confirm_ack_transport(identifier)
    assert instance.plant.pending_acknowledgements() == []
    # Same command reopens ONLY ACK transport; execution stays once.
    instance._acknowledgements[identifier] = info(published=True)
    instance.commands.put(
        CommandDelivery(
            f"freshlink/v1/devices/{ACTUATOR}/commands", payload, False, 2, 1
        )
    )
    instance._execute_commands(now)
    assert instance.plant.pending_acknowledgements() == [(identifier, original)]
    assert identifier not in instance._acknowledgements
    assert instance.plant.state()["execution_count"] == 1
    with pytest.raises(SimulatorRejected, match="command_not_found"):
        instance.plant.confirm_ack_transport(uuid4())


@pytest.mark.parametrize(
    "retained,qos,payload",
    [(True, 1, b"{}"), (False, 0, b"{}"), (False, 1, b"invalid")],
)
def test_permanent_rejection_audited_without_payload(
    tmp_path: Path, retained: bool, qos: int, payload: bytes
) -> None:
    instance = runtime(tmp_path / "state.db")
    instance.commands.put(
        CommandDelivery(
            f"freshlink/v1/devices/{ACTUATOR}/commands", payload, retained, 1, qos
        )
    )
    instance._execute_commands(datetime.now(UTC))
    records = instance.plant.audit_records()
    assert len(records) == 1 and records[0]["kind"] == "command_rejected"
    assert set(records[0]["detail"]) == {
        "reason_code",
        "payload_bytes",
        "payload_sha256",
    }
    assert instance.plant.state()["execution_count"] == 0
    with pytest.raises(SimulatorRejected, match="invalid_audit_reason"):
        instance.plant.audit_rejection(
            payload, now=datetime.now(UTC), reason_code="contains untrusted content!"
        )


def test_disk_failure_never_acknowledged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = runtime(tmp_path / "state.db")
    ack = MagicMock()
    monkeypatch.setattr(instance.actuator, "ack", ack)
    now = datetime.now(UTC)
    instance.commands.put(
        CommandDelivery(
            f"freshlink/v1/devices/{ACTUATOR}/commands", command(now), False, 1, 1
        )
    )

    def broken(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("disposable disk failure")

    monkeypatch.setattr(instance.plant, "execute", broken)
    with pytest.raises(sqlite3.OperationalError):
        instance._execute_commands(now)
    ack.assert_not_called()


def test_ack_transport_retry_not_server_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = runtime(tmp_path / "state.db")
    now = datetime.now(UTC)
    instance.plant.execute(
        command(now), topic=f"freshlink/v1/devices/{ACTUATOR}/commands", now=now
    )
    instance._publish_acks()  # disconnected: no transport confirmation
    assert len(instance.plant.pending_acknowledgements()) == 1
    instance.ready_actuator.set()
    pending = info(published=False)
    publisher = MagicMock(return_value=pending)
    monkeypatch.setattr(instance.actuator, "publish", publisher)
    instance._publish_acks()
    instance._publish_acks()
    assert publisher.call_count == 1
    assert len(instance.plant.pending_acknowledgements()) == 1
    pending.is_published.return_value = True
    instance._publish_acks()
    assert instance.plant.pending_acknowledgements() == []
    assert instance.plant.state()["execution_count"] == 1


def test_samples_replay_heartbeats_and_runtime_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = runtime(tmp_path / "state.db")
    now = datetime.fromisoformat(instance.plant.state()["stepped_at"]) + timedelta(
        seconds=1
    )
    instance.step(now=now, monotonic=1)
    assert len(instance.plant.pending()) == 1  # no connected broker, still durable
    instance.ready_sensor.set()
    instance.ready_actuator.set()
    pending = info(published=False)
    published = MagicMock(return_value=pending)
    monkeypatch.setattr(instance.sensor, "publish", published)
    monkeypatch.setattr(instance.actuator, "publish", MagicMock(return_value=pending))
    instance._publish_samples(2)
    instance._publish_samples(3)
    assert published.call_count == 1
    pending.is_published.return_value = True
    instance._publish_samples(4)
    assert published.call_count == 1  # resend backoff
    instance._publish_samples(13)
    assert published.call_count == 2
    assert len(instance.plant.pending()) == 1  # PUBACK is not C acceptance
    instance._heartbeat(now)
    heartbeat = json.loads(published.call_args.args[1])
    assert heartbeat["device_id"] == str(SENSOR)
    assert (
        published.call_args.kwargs["retain"] is False
        and published.call_args.kwargs["qos"] == 0
    )
    instance.step(now=now + timedelta(seconds=31), monotonic=32)
    assert len(instance.plant.pending()) == 2
    instance.stop_requested.set()
    instance.step(now=now, monotonic=0)


def test_retry_publish_queue_and_empty_outbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = runtime(tmp_path / "state.db")
    instance.ready_sensor.set()
    instance._publish_samples(1)
    now = datetime.now(UTC)
    instance.plant.sample(now)
    monkeypatch.setattr(
        instance.sensor,
        "publish",
        MagicMock(return_value=info(published=False, code=mqtt.MQTT_ERR_QUEUE_SIZE)),
    )
    instance._publish_samples(1)
    assert len(instance.plant.pending()) == 1 and not instance._telemetry
    instance.plant.execute(
        command(now), topic=f"freshlink/v1/devices/{ACTUATOR}/commands", now=now
    )
    instance.ready_actuator.set()
    monkeypatch.setattr(
        instance.actuator,
        "publish",
        MagicMock(return_value=info(published=False, code=mqtt.MQTT_ERR_QUEUE_SIZE)),
    )
    instance._publish_acks()
    assert (
        len(instance.plant.pending_acknowledgements()) == 1
        and not instance._acknowledgements
    )


def test_pending_round_robin_transport_batch_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = runtime(tmp_path / "state.db")
    start = datetime.now(UTC)
    for index in range(40):
        instance.plant.sample(start + timedelta(seconds=index + 1))
    instance.ready_sensor.set()
    publisher = MagicMock(side_effect=lambda *args, **kwargs: info(published=True))
    monkeypatch.setattr(instance.sensor, "publish", publisher)
    for tick in range(10):
        instance._publish_samples(tick)
    messages = [
        json.loads(call.args[1])["message_id"] for call in publisher.call_args_list
    ]
    assert len(set(messages)) == 40
    assert len(messages) == 40 and len(instance.plant.pending()) == 40


@pytest.mark.parametrize("success", [True, False])
def test_sample_replay_budget_is_shared_by_all_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, success: bool
) -> None:
    instance = runtime(tmp_path / "state.db")
    start = datetime.now(UTC)
    for index in range(100):
        instance.plant.sample(start + timedelta(seconds=index + 1))
    instance.ready_sensor.set()
    publisher = MagicMock(
        side_effect=lambda *args, **kwargs: info(
            published=True,
            code=mqtt.MQTT_ERR_SUCCESS if success else mqtt.MQTT_ERR_QUEUE_SIZE,
        )
    )
    monkeypatch.setattr(instance.sensor, "publish", publisher)
    for tick in range(200):
        instance._publish_samples(10 + tick / 1000)
    assert publisher.call_count == 4
    instance._publish_samples(11)
    assert publisher.call_count == 8
    assert len(instance.plant.pending()) == 100  # no false storage ACK


def test_fresh_samples_precede_fair_bounded_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = runtime(tmp_path / "state.db")
    start = datetime.now(UTC)
    for index in range(12):
        instance.plant.sample(start + timedelta(seconds=index + 1))
    original = {sample.payload["message_id"] for sample in instance.plant.pending()}
    instance.ready_sensor.set()
    publisher = MagicMock(side_effect=lambda *args, **kwargs: info(published=True))
    monkeypatch.setattr(instance.sensor, "publish", publisher)
    for tick in range(3):
        instance._publish_samples(tick)
    assert publisher.call_count == 12
    fresh = instance.plant.sample(start + timedelta(seconds=20))
    publisher.reset_mock()
    instance._publish_samples(20)
    assert (
        json.loads(publisher.call_args_list[0].args[1])["message_id"]
        == (fresh.payload["message_id"])
    )
    for tick in range(30, 160, 10):
        instance._publish_samples(tick)
    replayed = {
        json.loads(call.args[1])["message_id"] for call in publisher.call_args_list
    }
    assert original <= replayed
    assert len(instance.plant.pending()) == 13


def test_journal_retries_are_bounded_and_fair_while_new_samples_arrive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = runtime(tmp_path / "state.db")
    now = datetime.now(UTC)
    for tick in range(8):
        instance.plant.sample(now + timedelta(seconds=tick))
    original = {item.payload["message_id"] for item in instance.plant.pending()}
    counts: dict[str, int] = {}

    def publish(_topic, payload, **_kwargs):
        identifier = json.loads(payload)["message_id"]
        counts[identifier] = counts.get(identifier, 0) + 1
        return info(published=True)

    instance.ready_sensor.set()
    monkeypatch.setattr(instance.sensor, "publish", publish)
    for tick in range(2):
        instance._publish_samples(tick)
    for tick in range(2, 100):
        fresh = instance.plant.sample(now + timedelta(seconds=10 + tick))
        for substep in range(10):
            instance._publish_samples(tick + substep / 100)
        assert counts[fresh.payload["message_id"]] == 1
    retries = sum(count - 1 for count in counts.values())
    assert retries <= 10
    assert all(counts[identifier] >= 2 for identifier in original)
    assert len(instance.plant.pending()) == 106


def test_restart_sends_latest_sample_before_large_unconfirmed_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    first = runtime(path)
    now = datetime.now(UTC)
    for tick in range(100):
        first.plant.sample(now + timedelta(seconds=tick))
    restarted = runtime(path)
    fresh = restarted.plant.sample(now + timedelta(seconds=101))
    restarted.ready_sensor.set()
    publisher = MagicMock(side_effect=lambda *args, **kwargs: info(published=True))
    monkeypatch.setattr(restarted.sensor, "publish", publisher)
    restarted._publish_samples(200)
    assert (
        json.loads(publisher.call_args_list[0].args[1])["message_id"]
        == fresh.payload["message_id"]
    )
    assert publisher.call_count == 4
    assert len(restarted.plant.pending()) == 101


def test_start_and_run_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    instance = runtime(tmp_path / "state.db")
    for client in (instance.sensor, instance.actuator):
        monkeypatch.setattr(client, "connect_async", MagicMock())
        monkeypatch.setattr(client, "loop_start", MagicMock())
        monkeypatch.setattr(client, "disconnect", MagicMock())
        monkeypatch.setattr(client, "loop_stop", MagicMock())
    instance.ready_sensor.set()
    instance.ready_actuator.set()
    assert instance.wait_ready(0.1)
    # Avoid network while exercising the product loop/controlled shutdown.
    monkeypatch.setattr(
        instance, "step", lambda **kwargs: instance.stop_requested.set()
    )
    instance.run(max_seconds=1)
    assert instance.sensor.connect_async.call_args.kwargs["clean_start"] is False
    instance.sensor.loop_stop.assert_called_once()
    instance.fatal_reason = "mqtt_authentication_rejected"
    with pytest.raises(RuntimeError, match="mqtt_authentication_rejected"):
        instance.run(max_seconds=0.01)


def test_restart_gap_preserves_evidence_and_requires_new_cooling_command(
    tmp_path: Path,
) -> None:
    instance = runtime(tmp_path / "state.db")
    now = datetime.now(UTC)
    payload = command(now)
    ack = instance.plant.execute(
        payload, topic=f"freshlink/v1/devices/{ACTUATOR}/commands", now=now
    )
    sample = instance.plant.sample(now + timedelta(seconds=1))
    prior = instance.plant.state()
    assert not instance.plant.resume_after_gap(now + timedelta(seconds=30))
    assert instance.plant.resume_after_gap(now + timedelta(seconds=100))
    later = instance.plant.state()
    assert (
        later["temperature_c"] == prior["temperature_c"]
        and later["energy_kwh"] == prior["energy_kwh"]
    )
    assert later["cooling_enabled"] is False and instance.plant.pending() == [sample]
    assert (
        instance.plant.execute(
            payload,
            topic=f"freshlink/v1/devices/{ACTUATOR}/commands",
            now=now + timedelta(seconds=101),
        )
        == ack
    )
    assert instance.plant.audit_records()[0]["kind"] == "restart_gap"
    with pytest.raises(SimulatorRejected, match="clock_moved_backwards"):
        instance.plant.resume_after_gap(now)


def test_old_simulator_state_upgrade_preserves_commands(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE commands (id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,ack TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO commands VALUES (?,?,?)",
            (str(uuid4()), "old", '{"result":"failed"}'),
        )
    SoftwarePlant(
        path,
        actuator_id=ACTUATOR,
        sensor_id=SENSOR,
        warehouse_id=WAREHOUSE,
        started_at=datetime.now(UTC),
    )
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 1
        assert (
            connection.execute("SELECT ack_transported FROM commands").fetchone()[0]
            == 0
        )


def test_partial_start_failure_always_stops_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = runtime(tmp_path / "state.db")
    stopper = MagicMock()
    monkeypatch.setattr(instance, "stop", stopper)

    def broken_start() -> None:
        raise RuntimeError("disposable startup failure")

    monkeypatch.setattr(instance, "start", broken_start)
    with pytest.raises(RuntimeError, match="disposable startup failure"):
        instance.run()
    stopper.assert_called_once()


def test_runtime_cli_and_signal_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = settings(tmp_path / "state.db")
    filename = tmp_path / "config.json"
    filename.write_text(cfg.model_dump_json(), encoding="utf-8")
    fake = SimpleNamespace(stop_requested=MagicMock(), run=MagicMock())
    monkeypatch.setattr(
        "app.modules.iot.simulator_runtime.DeviceRuntime", lambda *args, **kwargs: fake
    )
    callbacks = []
    monkeypatch.setattr(
        "app.modules.iot.simulator_runtime.signal.signal",
        lambda signal, callback: callbacks.append(callback),
    )
    monkeypatch.setattr(
        "sys.argv", ["simulator", "--config", str(filename), "--max-seconds", "1"]
    )
    main()
    callbacks[0](2, None)
    fake.stop_requested.set.assert_called_once()
    fake.run.assert_called_once_with(max_seconds=1)
    monkeypatch.setattr(
        "sys.argv", ["simulator", "--config", str(filename), "--max-seconds", "-1"]
    )
    with pytest.raises(SystemExit):
        main()
