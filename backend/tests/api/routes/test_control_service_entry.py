"""Entry wiring/negative configuration; real process MQTT proof is separate."""

import signal
import sys
from unittest.mock import MagicMock, patch

import pytest

from app.modules.iot import control_service as service
from tests.api.routes.test_control_worker import config


def argv(monkeypatch, tmp_path, raw=None, duration="1"):
    path = tmp_path / "control.json"
    path.write_text(
        config().model_dump_json() if raw is None else raw, encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["control-service", "--config", str(path), "--max-seconds", duration],
    )


def test_entry_uses_real_bridge_restores_signals_and_disposes(monkeypatch, tmp_path):
    argv(monkeypatch, tmp_path)
    monkeypatch.setenv("FRESHLINK_CONTROL_MQTT_PASSWORD", "fixture-only")
    previous = {key: signal.getsignal(key) for key in (signal.SIGINT, signal.SIGTERM)}
    with (
        patch.object(service, "ControlWorker") as worker,
        patch.object(service, "verify_control_scope") as verify,
    ):
        service.main()
        assert (
            worker.call_args.kwargs["warehouse_lookup_factory"]
            is service.warehouse_lookup_for_session
        )
        verify.assert_called_once()
        worker.return_value.run.assert_called_once_with(max_seconds=1.0)
        worker.return_value.engine.dispose.assert_called_once()
    assert {key: signal.getsignal(key) for key in previous} == previous


@pytest.mark.parametrize("duration", ["0", "-1", "nan", "inf"])
def test_invalid_duration_never_constructs_worker(monkeypatch, tmp_path, duration):
    argv(monkeypatch, tmp_path, duration=duration)
    with patch.object(service, "ControlWorker") as worker, pytest.raises(SystemExit):
        service.main()
    worker.assert_not_called()


def test_missing_password_and_bad_config_are_redacted(monkeypatch, tmp_path):
    argv(monkeypatch, tmp_path)
    monkeypatch.delenv("FRESHLINK_CONTROL_MQTT_PASSWORD", raising=False)
    with (
        patch.object(service, "ControlWorker") as worker,
        pytest.raises(SystemExit) as failure,
    ):
        service.main()
    worker.assert_not_called()
    assert "fixture" not in str(failure.value)
    argv(monkeypatch, tmp_path, raw='{"password":"synthetic-sensitive-value"}')
    with pytest.raises(SystemExit) as failure:
        service.main()
    assert "synthetic-sensitive-value" not in str(failure.value)


def test_scope_failure_does_not_run_and_still_disposes(monkeypatch, tmp_path):
    argv(monkeypatch, tmp_path)
    monkeypatch.setenv("FRESHLINK_CONTROL_MQTT_PASSWORD", "fixture-only")
    with (
        patch.object(service, "ControlWorker") as worker,
        patch.object(
            service, "verify_control_scope", side_effect=ValueError("private-scope")
        ),
        pytest.raises(SystemExit),
    ):
        service.main()
    worker.return_value.run.assert_not_called()
    worker.return_value.engine.dispose.assert_called_once()


def test_read_only_scope_rejects_sensor_and_unknown_warehouse():
    for device in [
        MagicMock(kind="temperature_sensor"),
        MagicMock(kind="cooling_actuator", warehouse_id=None),
    ]:
        with (
            patch.object(service, "Session") as session,
            patch.object(
                service, "warehouse_lookup_for_session", return_value=lambda _id: False
            ),
        ):
            session.return_value.__enter__.return_value.exec.return_value.all.return_value = [
                device
            ]
            with pytest.raises(ValueError):
                service.verify_control_scope(MagicMock(), config())
