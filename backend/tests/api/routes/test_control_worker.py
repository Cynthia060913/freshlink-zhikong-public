"""Adapter boundary tests; real broker/PG proof is a separate disposable check."""

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode
from pydantic import ValidationError

from app.modules.iot.control_storage import ControlError, Dispatch
from app.modules.iot.control_worker import ControlWorker, ControlWorkerSettings

MODULE = "app.modules.iot.control_worker."


def config(**changes):
    return ControlWorkerSettings.model_validate(
        {
            "host": "broker",
            "port": 1883,
            "tls": False,
            "isolated_test_plaintext": True,
            "acl_confirmed": True,
            "username": "fixture-controller",
            "client_id": "fixture-control",
            "device_ids": [str(uuid4())],
            "scan_period_seconds": 0.1,
            **changes,
        }
    )


@pytest.fixture
def worker():
    instance = ControlWorker(
        config(),
        password="test-only",
        warehouse_lookup_factory=lambda _s: lambda _id: False,
    )
    instance.engine.dispose()
    instance.engine = MagicMock()
    instance.client = MagicMock()
    instance.client.ack.return_value = mqtt.MQTT_ERR_SUCCESS
    instance.client.connect.return_value = mqtt.MQTT_ERR_SUCCESS
    instance.client.loop.return_value = mqtt.MQTT_ERR_SUCCESS
    yield instance
    instance.engine.dispose()


def dispatch():
    return Dispatch(uuid4(), uuid4(), "fixture-topic", b"immutable-test-payload")


def message(worker, **changes):
    return SimpleNamespace(
        **{
            "topic": next(iter(worker.topics)),
            "payload": b"{}",
            "qos": 1,
            "retain": False,
            "mid": 17,
            **changes,
        }
    )


def reason(packet, name):
    return ReasonCode(packet, name)


@pytest.mark.parametrize(
    "changes",
    [
        {"host": "example.com"},
        {"acl_confirmed": False},
        {"device_ids": []},
        {"publish_timeout_seconds": 0},
        {"publish_timeout_seconds": float("nan")},
    ],
)
def test_scope_is_fail_closed(changes):
    with pytest.raises(ValidationError):
        config(**changes)


def test_password_required():
    with pytest.raises(ValueError, match="password"):
        ControlWorker(config(), password="", warehouse_lookup_factory=MagicMock())


def test_tls_and_bounded_volatile_queue():
    with patch(MODULE + "mqtt.Client") as constructor:
        worker = ControlWorker(
            config(tls=True), password="fixture", warehouse_lookup_factory=MagicMock()
        )
        context = constructor.return_value.tls_set_context.call_args.args[0]
        assert context.check_hostname and context.verify_mode.name == "CERT_REQUIRED"
        assert constructor.call_args.kwargs["manual_ack"] is True
        assert constructor.call_args.kwargs["reconnect_on_failure"] is False
        constructor.return_value.max_queued_messages_set.assert_called_once_with(14)
        worker.engine.dispose()


def test_exact_subscription_and_qos1_only(worker):
    worker.client.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 9)
    worker._on_connect(
        worker.client, None, None, reason(PacketTypes.CONNACK, "Success"), None
    )
    subscriptions = worker.client.subscribe.call_args.args[0]
    assert {s[0] for s in subscriptions} == set(worker.topics)
    assert all(s[1].retainAsPublished and s[1].QoS == 1 for s in subscriptions)
    worker._on_subscribe(
        worker.client, None, 9, [reason(PacketTypes.SUBACK, "Granted QoS 1")], None
    )
    assert worker.ready.is_set()
    worker._on_disconnect(worker.client, None, None, None, None)
    assert not worker.ready.is_set() and worker._reset_requested


@pytest.mark.parametrize("failure", ["auth", "subscribe", "suback", "downgrade"])
def test_subscription_failures_stop(worker, failure):
    worker.client.subscribe.return_value = (mqtt.MQTT_ERR_NO_CONN, 1)
    if failure in {"auth", "subscribe"}:
        worker._on_connect(
            worker.client,
            None,
            None,
            reason(
                PacketTypes.CONNACK,
                "Not authorized" if failure == "auth" else "Success",
            ),
            None,
        )
    else:
        reasons = (
            [] if failure == "suback" else [reason(PacketTypes.SUBACK, "Granted QoS 0")]
        )
        worker._on_subscribe(worker.client, None, 1, reasons, None)
    assert worker.stop_requested.is_set()


def test_ack_commit_precedes_puback(worker):
    order = []

    def persist(*_args, **kwargs):
        assert kwargs["received_at"].tzinfo == UTC
        assert kwargs["authenticated_device_id"] in worker.settings.device_ids
        order.append("committed")

    worker.client.ack.side_effect = lambda *_: (
        order.append("puback") or mqtt.MQTT_ERR_SUCCESS
    )
    with (
        patch(MODULE + "Session"),
        patch(MODULE + "receive_command_ack", side_effect=persist),
    ):
        worker._on_message(worker.client, None, message(worker))
    assert order == ["committed", "puback"] and worker.committed == 1


@pytest.mark.parametrize("invalid", ["qos0", "topic", "payload"])
def test_invalid_ack_never_becomes_execution(worker, invalid):
    incoming = message(worker, qos=0 if invalid == "qos0" else 1)
    if invalid == "topic":
        incoming.topic = "foreign/acks"
    with (
        patch(MODULE + "Session"),
        patch(
            MODULE + "receive_command_ack",
            side_effect=ControlError("invalid_ack_payload"),
        ) as store,
    ):
        worker._on_message(worker.client, None, incoming)
    assert worker.rejections == 1 and worker.committed == 0
    if invalid != "payload":
        store.assert_not_called()
    if invalid == "qos0":
        worker.client.ack.assert_not_called()
    else:
        worker.client.ack.assert_called_once()


def test_storage_failure_does_not_ack_or_leak(worker, caplog):
    with (
        patch(MODULE + "Session"),
        patch(
            MODULE + "receive_command_ack", side_effect=RuntimeError("private-value")
        ),
    ):
        worker._on_message(worker.client, None, message(worker))
    assert worker.fatal_reason == "control_ack_storage_failed"
    assert "private-value" not in caplog.text
    worker._on_message(worker.client, None, message(worker))
    worker.client.ack.assert_not_called()


def test_puback_failure_stops(worker):
    worker.client.ack.return_value = mqtt.MQTT_ERR_NO_CONN
    with patch(MODULE + "Session"), patch(MODULE + "receive_command_ack"):
        worker._on_message(worker.client, None, message(worker))
    assert worker.fatal_reason == "broker_ack_failed"


@pytest.mark.parametrize(
    "name,outcome",
    [
        ("Success", "accepted"),
        ("Not authorized", "permanent"),
        ("Quota exceeded", "transient"),
        ("Unspecified error", "transient"),
    ],
)
def test_transport_outcome_not_execution(worker, name, outcome):
    original = dispatch()
    worker._pending[7] = (original, time.monotonic())
    with patch.object(worker, "_finish") as finish:
        worker._on_publish(
            worker.client, None, 7, reason(PacketTypes.PUBACK, name), None
        )
        finish.assert_called_once_with(original, outcome)
        worker._on_publish(
            worker.client, None, 7, reason(PacketTypes.PUBACK, name), None
        )
        assert finish.call_count == 1
    assert not worker._pending


def test_transport_db_failure_stops(worker):
    worker._pending[1] = (dispatch(), time.monotonic())
    with patch.object(worker, "_finish", side_effect=RuntimeError("private")):
        worker._on_publish(
            worker.client, None, 1, reason(PacketTypes.PUBACK, "Success"), None
        )
    assert worker.fatal_reason == "control_dispatch_storage_failed"


def test_callbacks_from_discarded_clients_cannot_mutate_state(worker):
    old = MagicMock()
    worker._pending[7] = (dispatch(), time.monotonic())
    worker.ready.set()
    with patch.object(worker, "_finish") as finish:
        worker._on_publish(old, None, 7, reason(PacketTypes.PUBACK, "Success"), None)
        worker._on_connect(old, None, None, None, None)
        worker._on_subscribe(old, None, 7, [], None)
        worker._on_disconnect(old, None, None, None, None)
        worker._on_message(old, None, message(worker))
    assert worker.ready.is_set() and len(worker._pending) == 1
    finish.assert_not_called()


def test_scan_commits_claim_before_send_and_keeps_original_bytes(worker):
    original = dispatch()
    order = []
    worker.ready.set()
    worker.client.publish.side_effect = lambda *_a, **kw: (
        order.append(("send", kw)) or SimpleNamespace(rc=mqtt.MQTT_ERR_SUCCESS, mid=3)
    )
    with (
        patch(MODULE + "Session") as sessions,
        patch(
            MODULE + "claim_dispatch",
            side_effect=lambda *_a, **_kw: order.append(("claim", None)) or original,
        ),
    ):
        sessions.return_value.__enter__.return_value.exec.return_value.all.return_value = [
            (original.command_id, datetime.now(UTC) + timedelta(seconds=60))
        ]
        worker.scan()
    assert [r[0] for r in order] == ["claim", "send"]
    assert order[1][1] == {"qos": 1, "retain": False}
    worker.client.publish.assert_called_once_with(
        original.topic, original.payload, qos=1, retain=False
    )
    assert worker._pending[3][0] == original


@pytest.mark.parametrize(
    "state",
    ["offline", "pending", "full", "no_claim", "send_failure", "expired", "stopped"],
)
def test_scan_guards_and_offline_expiry(worker, state):
    original = dispatch()
    worker.ready.set()
    if state == "offline":
        worker.ready.clear()
    if state == "pending":
        worker._pending[1] = (original, time.monotonic())
    if state == "full":
        worker._pending = {i: (dispatch(), time.monotonic()) for i in range(14)}
    if state == "stopped":
        worker.stop_requested.set()
    worker.client.publish.return_value = SimpleNamespace(
        rc=mqtt.MQTT_ERR_NO_CONN, mid=3
    )
    with (
        patch(MODULE + "Session") as sessions,
        patch(
            MODULE + "claim_dispatch",
            return_value=None if state == "no_claim" else original,
        ) as claim,
        patch(MODULE + "expire_command") as expire,
        patch.object(worker, "_finish") as finish,
    ):
        sessions.return_value.__enter__.return_value.exec.return_value.all.return_value = [
            (
                original.command_id,
                datetime.now(UTC) + timedelta(seconds=-1 if state == "expired" else 60),
            )
        ]
        worker.scan()
    if state == "expired":
        expire.assert_called_once()
    if state == "send_failure":
        finish.assert_called_once_with(original, "transient")
        assert worker._reset_requested
    else:
        worker.client.publish.assert_not_called()
    if state in {"offline", "pending", "full", "stopped", "expired"}:
        claim.assert_not_called()


def test_finish_uses_bounded_fresh_session(worker):
    original = dispatch()
    with (
        patch(MODULE + "Session") as sessions,
        patch(MODULE + "finish_dispatch") as finish,
    ):
        worker._finish(original, "transient")
    session = sessions.return_value.__enter__.return_value
    session.connection.return_value.execute.assert_called_once()
    assert finish.call_args.kwargs["dispatch"] == original


def test_run_successful_socket_and_clean_stop(worker):
    worker.ready.set()
    with patch.object(worker, "scan") as scan:
        worker.run(max_seconds=0.01)
    assert scan.called
    worker.client.disconnect.assert_called_once()
    worker.engine.dispose.assert_called()


@pytest.mark.parametrize(
    "cause", ["connect", "loop", "puback_timeout", "handshake_timeout"]
)
def test_reconnect_discards_old_queue_and_preserves_db_retry(worker, cause):
    old = worker.client
    if cause == "connect":
        old.connect.side_effect = OSError("private-network")
    elif cause == "loop":
        old.loop.return_value = mqtt.MQTT_ERR_NO_CONN
    elif cause == "puback_timeout":
        worker._pending[1] = (dispatch(), time.monotonic() - 6)
    with (
        patch.object(worker, "scan"),
        patch.object(worker, "_new_client", return_value=old) as new,
        patch.object(worker, "_finish") as finish,
    ):
        # Handshake and reconnect clocks advance deterministically, no long sleep.
        if cause == "handshake_timeout":
            with patch(
                MODULE + "time.monotonic", side_effect=[0, 0, 0, 0, 0, 6, 6, 6, 8]
            ):
                worker.run(max_seconds=7)
        else:
            worker.run(max_seconds=0.02)
    if cause != "connect":
        assert new.called
    if cause == "puback_timeout":
        assert finish.call_args.args[1] == "transient"
    assert not worker._pending


@pytest.mark.parametrize("operation", ["scan", "retire"])
def test_run_db_failure_is_visible_and_sanitized(worker, operation, caplog):
    with (
        patch.object(
            worker,
            "scan",
            side_effect=RuntimeError("private-dsn") if operation == "scan" else None,
        ),
        patch.object(
            worker,
            "_retire_pending",
            side_effect=RuntimeError("private-dsn") if operation == "retire" else None,
        ),
    ):
        with pytest.raises(RuntimeError, match="control_"):
            worker.run(max_seconds=0.01)
    assert "private-dsn" not in caplog.text


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_invalid_duration_before_network(worker, duration):
    with pytest.raises(ValueError, match="finite and positive"):
        worker.run(max_seconds=duration)
    worker.client.connect.assert_not_called()
