"""No-database loop/config tests; all PG tests live in test_event_dispatch."""

import json
import sys
from datetime import datetime
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.modules.iot.event_dispatch import DispatchResult, _scope, _time
from app.modules.iot.event_worker import EventDeliveryWorker, EventWorkerSettings, main


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"enabled": False},
        {"enabled": True, "device_ids": []},
        {"enabled": True, "device_ids": [str(uuid4())], "extra": True},
        {"enabled": True, "device_ids": [str(uuid4())], "poll_seconds": float("nan")},
        {"enabled": True, "device_ids": [str(uuid4())] * 2},
        {"enabled": True, "device_ids": [str(uuid4()) for _ in range(15)]},
    ],
)
def test_explicit_finite_unique_config(data):
    with pytest.raises(ValueError):
        EventWorkerSettings.model_validate(data)


def make_worker():
    return EventDeliveryWorker(
        Mock(),
        Mock(),
        EventWorkerSettings(enabled=True, device_ids=[uuid4()], poll_seconds=0.1),
    )


def test_idle_success_and_stop():
    worker = make_worker()
    result = DispatchResult(uuid4(), "delivered")
    with patch("app.modules.iot.event_worker.claim_event", return_value=None):
        assert worker.run_once() is None and worker.last_status == "idle"
    with (
        patch("app.modules.iot.event_worker.claim_event", return_value=Mock()),
        patch("app.modules.iot.event_worker.deliver_event_claim", return_value=result),
    ):
        assert worker.run_once() == result and worker.completed == 1
    worker.stop_requested.set()
    worker.run()


@pytest.mark.parametrize("duration", [-1, 0, float("inf"), float("nan")])
def test_bounded_runtime_requires_positive_finite_duration(duration):
    with pytest.raises(ValueError):
        make_worker().run(max_seconds=duration)


def test_loop_deadline_and_database_backoff_are_bounded(caplog):
    worker = make_worker()
    worker.stop_requested = Mock()
    worker.stop_requested.is_set.side_effect = [False, False, True]
    with patch.object(
        worker,
        "run_once",
        side_effect=OperationalError("secret error", {}, RuntimeError("private")),
    ):
        worker.run()
    assert worker.database_failures == 2
    assert [c.args[0] for c in worker.stop_requested.wait.call_args_list] == [1, 2]
    assert "secret" not in caplog.text and "private" not in caplog.text
    with patch("app.modules.iot.event_worker.time.monotonic", side_effect=[0, 2]):
        make_worker().run(max_seconds=1)


def test_unknown_error_stops_without_private_detail():
    worker = make_worker()
    with (
        patch.object(worker, "run_once", side_effect=RuntimeError("private detail")),
        pytest.raises(RuntimeError, match="^event_dispatch_worker_failed$"),
    ):
        worker.run()
    assert worker.stop_requested.is_set() and worker.last_status == "worker_failed"


def test_clock_and_scope_refuse_unsafe_input():
    with pytest.raises(ValueError):
        _time(datetime(2026, 10, 18))
    assert _time(None).tzinfo is not None
    identity = uuid4()
    for scope in [
        (),
        (identity, identity),
        ("bad",),
        tuple(uuid4() for _ in range(15)),
    ]:
        with pytest.raises(ValueError):
            _scope(scope)


def test_cli_uses_finite_db_timeouts_and_disposes_engine(tmp_path):
    config = tmp_path / "worker.json"
    config.write_text(
        json.dumps({"enabled": True, "device_ids": [str(uuid4())]}), encoding="utf-8"
    )
    with (
        patch.object(
            sys, "argv", ["worker", "--config", str(config), "--max-seconds", "1"]
        ),
        patch("app.modules.iot.event_worker.create_engine") as factory,
        patch("app.modules.iot.event_worker.EventDeliveryWorker") as worker,
        patch("app.modules.iot.event_worker.signal.signal"),
    ):
        main()
        assert factory.call_args.kwargs["connect_args"] == {
            "connect_timeout": 5,
            "options": "-c statement_timeout=5000",
        }
        worker.return_value.run.assert_called_once_with(max_seconds=1)
        factory.return_value.dispose.assert_called_once()


def test_cli_rejects_oversized_config_before_db_setup(tmp_path):
    config = tmp_path / "large.json"
    config.write_text(" " * 65537, encoding="utf-8")
    with (
        patch.object(sys, "argv", ["worker", "--config", str(config)]),
        patch("app.modules.iot.event_worker.create_engine") as factory,
        pytest.raises(ValueError, match="worker config too large"),
    ):
        main()
    factory.assert_not_called()


def test_runtime_deadline_limits_final_wait():
    worker = make_worker()
    worker.stop_requested = Mock()
    worker.stop_requested.is_set.side_effect = [False, False]
    with (
        patch.object(worker, "run_once"),
        patch(
            "app.modules.iot.event_worker.time.monotonic",
            side_effect=[0, 0, 0.98, 1.01],
        ),
    ):
        worker.run(max_seconds=1)
    assert worker.stop_requested.wait.call_args.args[0] == pytest.approx(0.02)
