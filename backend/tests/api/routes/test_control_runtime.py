"""Explicit entrypoint tests; PG runs only in the disposable acceptance runner."""

from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlmodel import Session, delete

from app.core.db import engine
from app.modules.alerts.models import Warehouse
from app.modules.iot.control_runtime import (
    build_control_worker,
    main,
    read_control_settings,
    verify_control_scope,
)
from app.modules.iot.control_worker import ControlWorkerSettings
from app.modules.iot.models import Device
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session

MODULE = "app.modules.iot.control_runtime."


def settings() -> ControlWorkerSettings:
    return ControlWorkerSettings(
        host="broker",
        port=1883,
        tls=False,
        isolated_test_plaintext=True,
        username="synthetic-controller",
        client_id="synthetic-control-cli",
        acl_confirmed=True,
        device_ids=[uuid4()],
    )


@pytest.mark.parametrize(
    "payload", [b"{", b"x" * 65537, b'{"password":"synthetic-secret"}']
)
def test_bad_file_never_echoes_content(tmp_path, payload):
    path = tmp_path / "config.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="^control_config_invalid$"):
        read_control_settings(path)


def test_missing_file_and_valid_config(tmp_path):
    with pytest.raises(ValueError, match="^control_config_invalid$"):
        read_control_settings(tmp_path / "missing.json")
    path = tmp_path / "config.json"
    expected = settings()
    path.write_text(expected.model_dump_json(), encoding="utf-8")
    assert read_control_settings(path) == expected


@pytest.mark.parametrize("duration", ["nan", "inf", "0", "-1"])
def test_invalid_duration_before_config_or_worker(duration):
    with patch(MODULE + "read_control_settings") as read:
        with pytest.raises(SystemExit, match="^control_duration_invalid$"):
            main(["--config", "unused.json", "--max-seconds", duration])
        read.assert_not_called()


def test_build_uses_real_bridge_and_cleans_failed_preflight():
    fake = SimpleNamespace(engine=MagicMock())
    with patch(MODULE + "ControlWorker", return_value=fake) as factory:
        with patch(
            MODULE + "verify_control_scope",
            side_effect=RuntimeError("synthetic-secret"),
        ):
            with pytest.raises(
                RuntimeError, match="^control_startup_preflight_failed$"
            ):
                build_control_worker(settings(), "synthetic-password")
        assert (
            factory.call_args.kwargs["warehouse_lookup_factory"]
            is warehouse_lookup_for_session
        )
        fake.engine.dispose.assert_called_once()
        factory.reset_mock()
        with pytest.raises(ValueError, match="control_mqtt_credential_required"):
            build_control_worker(settings(), "")
        factory.assert_not_called()


def test_main_fixed_failure_and_shutdown_cleanup():
    worker = SimpleNamespace(
        engine=MagicMock(), stop_requested=Event(), run=MagicMock()
    )
    handlers = {}

    def capture(sig, callback):
        handlers[sig] = callback

    with patch(MODULE + "read_control_settings", return_value=settings()):
        with patch(MODULE + "build_control_worker", return_value=worker):
            with patch(MODULE + "signal.signal", side_effect=capture):
                main(["--config", "unused.json", "--max-seconds", "1"])
                assert len(handlers) == 2
                next(iter(handlers.values()))(None, None)
                assert worker.stop_requested.is_set()
                worker.run.assert_called_once_with(max_seconds=1.0)
                worker.engine.dispose.assert_called_once()
                worker.run.side_effect = RuntimeError("synthetic-secret")
                with pytest.raises(SystemExit, match="^control_runtime_failed$"):
                    main(["--config", "unused.json"])


@pytest.mark.parametrize(
    "kind,binding",
    [
        ("cooling_actuator", "real"),
        ("temperature_sensor", "real"),
        ("cooling_actuator", "missing"),
        ("cooling_actuator", "none"),
    ],
)
def test_real_pg_scope_is_read_only(db, kind, binding):
    _ = db
    warehouse = Warehouse(code="cli-" + uuid4().hex, name="Synthetic CLI warehouse")
    device = Device(code="cli-" + uuid4().hex, name="Synthetic CLI actuator", kind=kind)
    device.warehouse_id = (
        warehouse.id if binding == "real" else uuid4() if binding == "missing" else None
    )
    with Session(engine) as session:
        session.add(warehouse)
        session.add(device)
        session.commit()
        identity, warehouse_id = device.id, warehouse.id
    try:
        if kind == "cooling_actuator" and binding == "real":
            verify_control_scope(engine, [identity])
        else:
            with pytest.raises(
                ValueError,
                match="control_actuator_binding_required|control_warehouse_unknown",
            ):
                verify_control_scope(engine, [identity])
        with pytest.raises(ValueError, match="control_device_unknown"):
            verify_control_scope(engine, [uuid4()])
        with Session(engine) as session:
            current = session.get(Device, identity)
            assert current is not None
            assert current.connectivity == "unknown" and current.last_seen_at is None
            assert current.warehouse_id == device.warehouse_id
    finally:
        with Session(engine) as session:
            session.exec(delete(Device).where(Device.id == identity))
            session.exec(delete(Warehouse).where(Warehouse.id == warehouse_id))
            session.commit()


def test_scope_input_before_database_and_wrong_backend():
    fake = MagicMock()
    for identities in ([], [uuid4()] * 2, [uuid4() for _ in range(15)]):
        with pytest.raises(ValueError, match="control_scope_invalid"):
            verify_control_scope(fake, identities)
    fake.connect.assert_not_called()
    fake.dialect.name = "sqlite"
    with pytest.raises(ValueError, match="control_postgresql_required"):
        verify_control_scope(fake, [uuid4()])
    fake.connect.assert_not_called()
