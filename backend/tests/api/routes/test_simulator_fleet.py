"""Supervisor scope/lifecycle/file-protection units, not real MQTT acceptance."""

import hashlib
import json
import sqlite3
import sys
import threading
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.iot.simulator import SoftwarePlant
from app.modules.iot.simulator_fleet import (
    FleetRuntimeError,
    FleetSettings,
    SimulatorFleet,
    main,
    password_environment_name,
)


def settings(tmp_path, count=7, changes=None):
    zones = [
        {
            "host": "localhost",
            "port": 1883,
            "tls": False,
            "isolated_test_plaintext": True,
            "sensor_username": f"sensor-{index}",
            "actuator_username": f"actuator-{index}",
            "sensor_id": UUID(int=100 + 2 * index),
            "actuator_id": UUID(int=101 + 2 * index),
            "warehouse_id": UUID(int=300 + index // 4),
            "state_file": tmp_path / "states" / f"zone-{index}.db",
        }
        for index in range(count)
    ]
    if changes is not None:
        changes(zones)
    return FleetSettings.model_validate({"zones": zones})


class FakeRuntime:
    def __init__(self, configuration, **credentials):
        self.configuration = configuration
        self.credentials = credentials
        self.stop_requested = threading.Event()
        self.fatal_reason = None
        self.started = False
        self.stopped = False
        self.steps = 0
        self.start_failure = False
        self.step_failure = False
        self.stop_failure = False

    def start(self):
        self.started = True
        if self.start_failure:
            raise RuntimeError("private-test-detail")

    def step(self, **_):
        self.steps += 1
        if self.step_failure:
            raise RuntimeError("private-test-detail")

    def stop(self):
        self.stopped = True
        self.stop_requested.set()
        if self.stop_failure:
            raise RuntimeError("private-test-detail")


def fleet(tmp_path, count=7):
    return SimulatorFleet(
        settings(tmp_path, count),
        password_for=lambda _: "fixture-only",
        factory=FakeRuntime,
    )


def test_seven_zones_are_fourteen_disjoint_devices_in_two_warehouses(tmp_path):
    supervisor = fleet(tmp_path)
    supervisor.run(max_seconds=0.06)
    assert len(supervisor.runtimes) == 7
    assert all(
        item.started and item.stopped and item.steps for item in supervisor.runtimes
    )
    config = settings(tmp_path)
    assert len({zone.warehouse_id for zone in config.zones}) == 2
    assert (
        len(
            {
                identity
                for zone in config.zones
                for identity in (zone.sensor_id, zone.actuator_id)
            }
        )
        == 14
    )


@pytest.mark.parametrize(
    "mode", ["startup", "step", "fatal", "external_stop", "cleanup"]
)
def test_partial_failure_always_stops_all_siblings_and_redacts_errors(tmp_path, mode):
    supervisor = fleet(tmp_path)
    affected = supervisor.runtimes[3]
    if mode == "startup":
        affected.start_failure = True
    elif mode == "step":
        affected.step_failure = True
    elif mode == "fatal":
        affected.fatal_reason = "private-test-detail"
    elif mode == "external_stop":
        affected.stop_requested.set()
    else:
        affected.stop_failure = True
    with pytest.raises(FleetRuntimeError) as caught:
        supervisor.run(max_seconds=0.06)
    assert "private-test-detail" not in str(caught.value)
    assert all(item.stopped for item in supervisor.runtimes)


def test_user_stop_before_loop_does_not_step_and_still_cleans_up(tmp_path):
    supervisor = fleet(tmp_path, 1)
    supervisor.stop_requested.set()
    supervisor.run(max_seconds=0.1)
    assert not supervisor.runtimes[0].started
    assert supervisor.runtimes[0].steps == 0 and supervisor.runtimes[0].stopped


@pytest.mark.parametrize("limit", [0, -1, float("nan"), float("inf"), 3601])
def test_invalid_run_limit_never_starts_network(tmp_path, limit):
    supervisor = fleet(tmp_path, 1)
    with pytest.raises(ValueError):
        supervisor.run(max_seconds=limit)
    assert not supervisor.runtimes[0].started


def test_missing_last_credential_is_found_before_any_plant_is_created(tmp_path):
    config = settings(tmp_path)
    missing = config.zones[-1].actuator_id
    calls = []
    with pytest.raises(FleetRuntimeError, match="credentials_missing"):
        SimulatorFleet(
            config,
            password_for=lambda identity: "" if identity == missing else "fixture-only",
            factory=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
    assert calls == [] and not (tmp_path / "states").exists()


@pytest.mark.parametrize(
    "change",
    [
        lambda zones: zones[1].update(sensor_id=zones[0]["actuator_id"]),
        lambda zones: zones[1].update(sensor_username=zones[0]["sensor_username"]),
        lambda zones: zones[1].update(state_file=zones[0]["state_file"]),
        lambda zones: zones[1].update(
            state_file=zones[0]["state_file"].parent.parent / "else" / "zone.db"
        ),
        lambda zones: zones[1].update(
            state_file=zones[1]["state_file"].with_suffix(".sqlite")
        ),
        lambda zones: zones[1].update(host="broker"),
        lambda zones: zones[1].update(warehouse_id=UUID(int=500)),
    ],
)
def test_overlap_unportable_paths_or_broker_scope_are_rejected(tmp_path, change):
    with pytest.raises(ValidationError):
        settings(tmp_path, changes=change)


@pytest.mark.parametrize("count", [0, 8])
def test_outside_v4_zone_capacity_is_rejected(tmp_path, count):
    with pytest.raises(ValidationError):
        settings(tmp_path, count)


def test_existing_unrelated_database_is_read_only_and_never_migrated(tmp_path):
    config = settings(tmp_path, 1)
    path = config.zones[0].state_file
    path.parent.mkdir()
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE coursework (value TEXT)")
        connection.execute("INSERT INTO coursework VALUES ('preserve')")
    before = path.read_bytes()
    with pytest.raises(FleetRuntimeError, match="state_not_owned"):
        SimulatorFleet(
            config, password_for=lambda _: "fixture-only", factory=FakeRuntime
        )
    assert path.read_bytes() == before


@pytest.mark.parametrize("wrong_identity", [False, True])
def test_owned_restart_state_is_checked_before_writes(tmp_path, wrong_identity):
    config = settings(tmp_path, 1)
    zone = config.zones[0]
    zone.state_file.parent.mkdir()
    SoftwarePlant(
        zone.state_file,
        actuator_id=zone.actuator_id,
        sensor_id=zone.sensor_id,
        warehouse_id=zone.warehouse_id,
        started_at=datetime.now(UTC),
    )
    before = hashlib.sha256(zone.state_file.read_bytes()).hexdigest()
    if wrong_identity:
        zone.sensor_id = UUID(int=999)
        with pytest.raises(FleetRuntimeError, match="identity_mismatch"):
            SimulatorFleet(
                config, password_for=lambda _: "fixture-only", factory=FakeRuntime
            )
    else:
        SimulatorFleet(
            config, password_for=lambda _: "fixture-only", factory=FakeRuntime
        )
    assert hashlib.sha256(zone.state_file.read_bytes()).hexdigest() == before


def test_environment_keys_are_per_device_not_shared_role_passwords(tmp_path):
    config = settings(tmp_path)
    keys = {
        password_environment_name(identity)
        for zone in config.zones
        for identity in (zone.sensor_id, zone.actuator_id)
    }
    assert len(keys) == 14
    assert all(
        key.startswith("FRESHLINK_SIM_") and key.endswith("_PASSWORD") for key in keys
    )


def test_credential_provider_failure_is_redacted_before_state_creation(tmp_path):
    def fail(_):
        raise RuntimeError("private-test-detail")

    with pytest.raises(FleetRuntimeError, match="credential_provider_failed") as caught:
        SimulatorFleet(settings(tmp_path), password_for=fail, factory=FakeRuntime)
    assert "private-test-detail" not in str(caught.value)
    assert not (tmp_path / "states").exists()


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_partial_constructor_failure_attempts_cleanup_and_reports_failures(
    tmp_path, cleanup_failure
):
    created = []

    def create(configuration, **credentials):
        if created:
            raise RuntimeError("private-test-detail")
        item = FakeRuntime(configuration, **credentials)
        item.stop_failure = cleanup_failure
        created.append(item)
        return item

    with pytest.raises(FleetRuntimeError) as caught:
        SimulatorFleet(
            settings(tmp_path), password_for=lambda _: "fixture-only", factory=create
        )
    assert created[0].stopped and not created[0].started
    expected = (
        "fleet_initialization_cleanup_failed"
        if cleanup_failure
        else "fleet_initialization_failed"
    )
    assert str(caught.value) == expected


def test_cli_validation_never_echoes_a_misplaced_password(
    tmp_path, monkeypatch, capsys
):
    configuration = settings(tmp_path).model_dump(mode="json")
    configuration["zones"][0]["password"] = "private-test-detail"
    path = tmp_path / "bad-fleet.json"
    path.write_text(json.dumps(configuration), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["fleet", "--config", str(path)])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    assert "private-test-detail" not in capsys.readouterr().err
    assert not (tmp_path / "states").exists()
