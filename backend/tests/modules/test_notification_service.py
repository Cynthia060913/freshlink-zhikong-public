"""A entry wiring only: synthetic channels, no provider or DB connection."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.modules.alerts.notifications.runner import RunnerCycleReport
from app.modules.alerts.notifications.transport import NotificationKind
from app.modules.iot import notification_service as service


@pytest.fixture
def harness(tmp_path, monkeypatch):
    path = tmp_path / "synthetic-profile.json"
    path.write_text(json.dumps({"recipients": ["operator@example.com"]}))
    transport = SimpleNamespace(kind=NotificationKind.SMTP_EMAIL)
    monkeypatch.setattr(service, "build_transports", lambda: [transport])
    connection = Mock()
    create = Mock(return_value=connection)
    monkeypatch.setattr(service, "create_engine", create)
    run = Mock(return_value=[RunnerCycleReport(dry_run=True)])
    monkeypatch.setattr(service, "run_dispatch_worker", run)
    return path, create, connection, run


def test_default_is_bounded_dry_run_and_private_recipients_are_forwarded(
    harness, capsys
):
    path, create, connection, run = harness
    assert service.main(["--config", str(path)]) == 0
    options = run.call_args.kwargs
    assert options["dry_run"] is True and options["max_cycles"] == 1
    assert options["recipients"] == ("operator@example.com",)
    assert options["limit"] == options["max_attempts"] == 1
    assert create.call_count == connection.dispose.call_count == 1
    assert "operator@example.com" not in capsys.readouterr().out


def test_send_requires_explicit_flag_and_configured_channel(harness, monkeypatch):
    path, _, connection, run = harness
    assert service.main(["--config", str(path), "--send", "--max-cycles", "2"]) == 0
    assert run.call_args.kwargs["dry_run"] is False
    assert run.call_args.kwargs["max_cycles"] == 2
    assert connection.dispose.call_count == 1
    run.reset_mock()
    monkeypatch.setattr(service, "build_transports", lambda: [])
    assert service.main(["--config", str(path), "--send"]) == 2
    run.assert_not_called()


@pytest.mark.parametrize("send", [[], ["--send"]])
def test_empty_smtp_recipients_refused_before_worker_or_connection(harness, send):
    path, create, _, run = harness
    path.write_text('{"recipients": []}')
    assert service.main(["--config", str(path), *send]) == 2
    create.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize(
    "bad",
    [
        {"recipients": ["synthetic-secret-invalid"]},
        {"recipients": [], "send": True},
        {"recipients": [], "poll_seconds": float("inf")},
        {"recipients": [], "max_events_per_cycle": 51},
    ],
)
def test_invalid_private_config_is_rejected_without_value_leak(harness, bad, capsys):
    path, create, _, run = harness
    path.write_text(json.dumps(bad))
    assert service.main(["--config", str(path)]) == 2
    create.assert_not_called()
    run.assert_not_called()
    output = capsys.readouterr()
    assert "synthetic-secret-invalid" not in output.out + output.err


def test_interruption_is_nonzero_and_engine_disposed(harness):
    path, _, connection, run = harness
    run.return_value = [RunnerCycleReport(interrupted="lease_busy")]
    assert service.main(["--config", str(path)]) == 1
    assert connection.dispose.call_count == 1
