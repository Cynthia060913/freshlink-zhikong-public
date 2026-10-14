import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.modules.iot.simulator import SimulatorRejected, SoftwarePlant

ACTUATOR = UUID("66666666-6666-4666-8666-666666666666")
SENSOR = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
START = datetime(2026, 10, 12, 2, tzinfo=UTC)
TOPIC = f"freshlink/v1/devices/{ACTUATOR}/commands"


def plant(path: Path, **options: object) -> SoftwarePlant:
    return SoftwarePlant(
        path,
        actuator_id=ACTUATOR,
        sensor_id=SENSOR,
        warehouse_id=WAREHOUSE,
        started_at=START,
        **options,
    )


def command(**updates: object) -> bytes:
    data = {
        "schema_version": "1.0",
        "command_id": str(uuid4()),
        "device_id": str(ACTUATOR),
        "warehouse_id": str(WAREHOUSE),
        "action": "set_cooling",
        "parameters": {"enabled": True},
        "issued_at": START.isoformat(),
        "expires_at": (START + timedelta(seconds=60)).isoformat(),
    }
    data.update(updates)
    return json.dumps(data).encode()


def execute(instance: SoftwarePlant, payload: bytes, **options: object) -> dict:
    return instance.execute(
        payload, topic=TOPIC, now=START + timedelta(seconds=5), **options
    )


def test_control_changes_later_temperature_power_and_energy(tmp_path: Path) -> None:
    active = plant(tmp_path / "active.db")
    baseline = plant(tmp_path / "baseline.db")
    first = active.sample(START + timedelta(seconds=1))
    baseline.sample(START + timedelta(seconds=1))
    ack = execute(active, command())
    later = active.sample(START + timedelta(seconds=30))
    uncontrolled = baseline.sample(START + timedelta(seconds=30))
    assert first.power_w == uncontrolled.power_w == 200
    assert ack["result"] == "executed" and ack["state"]["cooling_enabled"]
    assert later.power_w == 1000 and later.energy_kwh > uncontrolled.energy_kwh
    assert later.payload["metrics"][0]["value"] < first.payload["metrics"][0]["value"]
    assert (
        later.payload["metrics"][0]["value"]
        < uncontrolled.payload["metrics"][0]["value"]
    )
    assert later.payload["message_id"] != first.payload["message_id"]
    assert later.payload["device_id"] == str(SENSOR)
    assert [m["unit"] for m in later.payload["metrics"]] == ["°C", "%RH"]
    snapshot = later.payload
    snapshot["metrics"].clear()
    assert len(later.payload["metrics"]) == 2


def test_switch_off_changes_future_power(tmp_path: Path) -> None:
    instance = plant(tmp_path / "plant.db")
    execute(instance, command())
    instance.sample(START + timedelta(seconds=10))
    off = command(parameters={"enabled": False})
    ack = instance.execute(off, topic=TOPIC, now=START + timedelta(seconds=15))
    assert ack["state"]["cooling_enabled"] is False
    assert instance.sample(START + timedelta(seconds=20)).power_w == 200
    assert instance.state()["execution_count"] == 2


def test_duplicate_ack_and_pending_bytes_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "plant.db"
    original = plant(path)
    payload = command()
    ack = execute(original, payload)
    sample = original.sample(START + timedelta(seconds=10))
    restarted = plant(path)
    assert (
        restarted.execute(payload, topic=TOPIC, now=START + timedelta(seconds=100))
        == ack
    )
    assert restarted.state()["execution_count"] == 1
    assert restarted.pending()[0] == sample
    assert restarted.pending()[0].payload_text.encode() == sample.payload_text.encode()
    assert restarted.state()["cooling_enabled"]


def test_same_id_changed_content_is_conflict(tmp_path: Path) -> None:
    instance = plant(tmp_path / "plant.db")
    payload = command()
    execute(instance, payload)
    changed = json.loads(payload)
    changed["parameters"]["enabled"] = False
    with pytest.raises(SimulatorRejected, match="command_content_conflict"):
        execute(instance, json.dumps(changed).encode())
    assert instance.state()["execution_count"] == 1


def test_equivalent_utc_offsets_are_same_command(tmp_path: Path) -> None:
    instance = plant(tmp_path / "plant.db")
    payload = command()
    ack = execute(instance, payload)
    raw = json.loads(payload)
    raw["issued_at"] = "2026-10-12T10:00:00+08:00"
    raw["expires_at"] = "2026-10-12T10:01:00+08:00"
    assert execute(instance, json.dumps(raw).encode()) == ack


@pytest.mark.parametrize("failure", ["expired", "switch"])
def test_failure_is_persistent_and_never_executes(tmp_path: Path, failure: str) -> None:
    path = tmp_path / "plant.db"
    instance = plant(path)
    payload = (
        command(expires_at=(START + timedelta(seconds=5)).isoformat())
        if failure == "expired"
        else command()
    )
    ack = execute(instance, payload, fail_switch=failure == "switch")
    assert ack["result"] == "failed" and ack["executed_at"] is None
    assert ack["reason"] == (
        "expired" if failure == "expired" else "simulated_switch_failure"
    )
    assert (
        plant(path).execute(payload, topic=TOPIC, now=START + timedelta(seconds=100))
        == ack
    )
    assert instance.state()["execution_count"] == 0
    assert instance.state()["cooling_enabled"] is False


@pytest.mark.parametrize(
    "updates",
    [
        {"device_id": str(SENSOR)},
        {"warehouse_id": str(uuid4())},
        {"schema_version": "draft-1"},
        {"action": "shell"},
        {"parameters": {"enabled": "true"}},
        {"parameters": {"enabled": 1}},
        {"parameters": {}},
        {"parameters": {"enabled": True, "extra": False}},
        {"issued_at": "2026-10-12T02:00:00"},
        {"command_id": "not-a-uuid"},
        {"expires_at": START.isoformat()},
        {"expires_at": (START + timedelta(seconds=301)).isoformat()},
        {"extra": "unexpected"},
        {"issued_at": (START + timedelta(seconds=10)).isoformat()},
    ],
)
def test_invalid_commands_leave_state_unchanged(tmp_path: Path, updates: dict) -> None:
    instance = plant(tmp_path / "plant.db")
    prior = instance.state()
    with pytest.raises(SimulatorRejected):
        execute(instance, command(**updates))
    assert instance.state() == prior


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        b"[]",
        b"\xff",
        b"x" * 4097,
        b'{"schema_version":"1.0","schema_version":"1.0"}',
    ],
)
def test_invalid_bytes_rejected(tmp_path: Path, payload: bytes) -> None:
    instance = plant(tmp_path / "plant.db")
    with pytest.raises(SimulatorRejected):
        execute(instance, payload)
    assert instance.state()["execution_count"] == 0


def test_retained_wrong_topic_and_naive_clock_rejected(tmp_path: Path) -> None:
    instance = plant(tmp_path / "plant.db")
    with pytest.raises(SimulatorRejected, match="invalid_command_transport"):
        execute(instance, command(), retained=True)
    with pytest.raises(SimulatorRejected, match="invalid_command_transport"):
        instance.execute(
            command(), topic=TOPIC.replace(str(ACTUATOR), str(SENSOR)), now=START
        )
    with pytest.raises(SimulatorRejected, match="clock_requires_timezone"):
        instance.execute(command(), topic=TOPIC, now=START.replace(tzinfo=None))


def test_outbox_full_is_atomic_and_broker_receipt_cannot_clear(tmp_path: Path) -> None:
    instance = plant(tmp_path / "plant.db", max_pending=1)
    sample = instance.sample(START + timedelta(seconds=1))
    prior = instance.state()
    with pytest.raises(SimulatorRejected, match="observation_queue_full"):
        instance.sample(START + timedelta(seconds=2))
    assert instance.state() == prior
    identifier = UUID(sample.payload["message_id"])
    for outcome in ["PUBACK", "retryable_failure", "conflict", "invalid"]:
        with pytest.raises(SimulatorRejected, match="storage_not_confirmed"):
            instance.confirm_storage(identifier, outcome=outcome)
    assert instance.pending() == [sample]
    instance.confirm_storage(identifier, outcome="accepted_new")
    instance.confirm_storage(identifier, outcome="accepted_duplicate")
    assert instance.pending() == []
    assert instance.sample(START + timedelta(seconds=2)).payload["message_id"] != str(
        identifier
    )
    with pytest.raises(SimulatorRejected, match="observation_not_found"):
        instance.confirm_storage(uuid4(), outcome="accepted_new")


def test_capacity_and_clock_limits(tmp_path: Path) -> None:
    instance = plant(tmp_path / "plant.db", max_commands=1)
    execute(instance, command())
    with pytest.raises(SimulatorRejected, match="command_capacity_reached"):
        execute(instance, command())
    with pytest.raises(SimulatorRejected, match="invalid_step_interval"):
        instance.sample(START + timedelta(seconds=100))
    with pytest.raises(SimulatorRejected, match="sample_time_must_advance"):
        instance.sample(START + timedelta(seconds=5))
    fresh = plant(tmp_path / "clock.db")
    with pytest.raises(SimulatorRejected, match="invalid_step_interval"):
        fresh.execute(
            command(issued_at=(START - timedelta(seconds=2)).isoformat()),
            topic=TOPIC,
            now=START - timedelta(seconds=1),
        )


def test_concurrent_redelivery_executes_once(tmp_path: Path) -> None:
    path = tmp_path / "plant.db"
    first, second = plant(path), plant(path)
    payload = command()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda instance: execute(instance, payload), [first, second])
        )
    assert results[0] == results[1]
    assert plant(path).state()["execution_count"] == 1


@pytest.mark.parametrize(
    "options",
    [
        {"initial_temperature_c": float("nan")},
        {"initial_temperature_c": 81},
        {"max_pending": 0},
        {"max_commands": 10001},
    ],
)
def test_invalid_configuration(tmp_path: Path, options: dict) -> None:
    with pytest.raises(SimulatorRejected):
        plant(tmp_path / "plant.db", **options)


def test_state_identity_cannot_be_repurposed(tmp_path: Path) -> None:
    path = tmp_path / "plant.db"
    plant(path)
    with pytest.raises(SimulatorRejected, match="state_identity_mismatch"):
        SoftwarePlant(
            path,
            actuator_id=uuid4(),
            sensor_id=SENSOR,
            warehouse_id=WAREHOUSE,
            started_at=START,
        )
    with pytest.raises(SimulatorRejected, match="sensor_and_actuator_must_differ"):
        SoftwarePlant(
            tmp_path / "bad.db",
            actuator_id=SENSOR,
            sensor_id=SENSOR,
            warehouse_id=WAREHOUSE,
            started_at=START,
        )


def test_sql_failure_rolls_back_execution(tmp_path: Path) -> None:
    path = tmp_path / "plant.db"
    instance = plant(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_insert BEFORE INSERT ON commands BEGIN SELECT RAISE(ABORT,'test failure'); END"
        )
    prior = instance.state()
    with pytest.raises(sqlite3.IntegrityError):
        execute(instance, command())
    assert instance.state() == prior


def test_accepted_history_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "plant.db"
    instance = plant(path)
    # Seed accepted fixture rows to exercise retention without running 1000 steps.
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO observations VALUES (?,?,200,0,1,?)",
            [(str(uuid4()), "{}", n) for n in range(1, 1002)],
        )
    sample = instance.sample(START + timedelta(seconds=1))
    instance.confirm_storage(UUID(sample.payload["message_id"]), outcome="accepted_new")
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM observations WHERE stored=1"
            ).fetchone()[0]
            == 1000
        )
