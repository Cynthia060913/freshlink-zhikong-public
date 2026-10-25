"""Process config/lifecycle checks; real PG scope cases are separate."""

import logging
import threading
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.modules.iot.telemetry_service import (
    TelemetryServiceSettings,
    run_evaluation_loop,
)


def settings_for(device, epoch, **changes):
    return TelemetryServiceSettings(
        **{
            "host": "broker",
            "port": 1883,
            "tls": False,
            "isolated_test_plaintext": True,
            "username": "fixture-readonly",
            "client_id": "fixture-service-receiver",
            "acl_confirmed": True,
            "device_ids": [device],
            "frontier_epochs": {str(device): str(epoch)},
            **changes,
        }
    )


def test_exact_scope_and_no_secret_config():
    device, epoch = uuid4(), uuid4()
    assert settings_for(device, epoch).frontier_epochs == {device: epoch}
    with pytest.raises(ValidationError):
        settings_for(device, epoch, frontier_epochs={str(uuid4()): str(epoch)})
    with pytest.raises(ValidationError):
        settings_for(device, epoch, password="must-not-enter-config")


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan")])
def test_bad_duration_rejected(seconds):
    worker = MagicMock()
    with pytest.raises(ValueError):
        run_evaluation_loop(worker, threading.Event(), max_seconds=seconds)
    worker.run_once.assert_not_called()


def test_stop_does_not_process_an_extra_page():
    worker, stopped = MagicMock(), threading.Event()
    stopped.set()
    run_evaluation_loop(worker, stopped)
    worker.run_once.assert_not_called()


def test_storage_error_is_redacted_and_later_tick_retries(caplog, monkeypatch):
    from app.modules.iot import telemetry_service

    # In-process Alembic tests call fileConfig, which disables existing loggers.
    # Capture a real, test-owned logger explicitly instead of depending on
    # unrelated migration/root logging state. All message/redaction assertions
    # remain in place; production logging and migration config are unchanged.
    owned_logger = logging.Logger("freshlink.telemetry.regression", logging.WARNING)
    owned_logger.addHandler(caplog.handler)
    owned_logger.propagate = False
    monkeypatch.setattr(telemetry_service, "logger", owned_logger)
    worker, stopped = MagicMock(), threading.Event()

    def tick(**_kwargs):
        if worker.run_once.call_count == 1:
            raise OperationalError("private-sql", {}, RuntimeError("secret-value"))
        stopped.set()

    worker.run_once.side_effect = tick
    run_evaluation_loop(worker, stopped, poll_seconds=0.1, max_seconds=1)
    assert worker.run_once.call_count == 2
    assert worker.run_once.call_args.kwargs["limit"] == 14
    assert "temporarily unavailable" in caplog.text
    assert "secret-value" not in caplog.text
    assert "private-sql" not in caplog.text
