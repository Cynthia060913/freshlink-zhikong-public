"""Worker security/ack/stop branches. Live broker checks use a separate script."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode
from pydantic import ValidationError

from app.modules.iot.heartbeat_worker import HeartbeatWorker, HeartbeatWorkerSettings
from app.modules.iot.ingress import MAX_PAYLOAD_BYTES, IngressRejected


def config(**changes):
    return HeartbeatWorkerSettings.model_validate(
        {
            "host": "broker",
            "port": 1883,
            "tls": False,
            "isolated_test_plaintext": True,
            "acl_confirmed": True,
            "username": "fixture-readonly",
            "client_id": "fixture-worker",
            "device_ids": [str(uuid4())],
            **changes,
        }
    )


@pytest.fixture
def worker():
    instance = HeartbeatWorker(config(), password="test-only-password")
    instance.client = MagicMock()
    instance.client.ack.return_value = mqtt.MQTT_ERR_SUCCESS
    return instance


@pytest.mark.parametrize(
    "changes",
    [
        {"host": "example.com"},
        {"isolated_test_plaintext": False},
        {"acl_confirmed": False},
        {"device_ids": []},
        {"device_ids": [str(uuid4()) for _ in range(15)]},
    ],
)
def test_configuration_fails_closed(changes):
    with pytest.raises(ValidationError):
        config(**changes)


def test_duplicate_scope_and_empty_password_rejected():
    identity = uuid4()
    with pytest.raises(ValidationError):
        config(device_ids=[identity, identity])
    with pytest.raises(ValueError, match="password"):
        HeartbeatWorker(config(), password="")


def test_tls_keeps_certificate_and_hostname_checks():
    with patch("app.modules.iot.heartbeat_worker.mqtt.Client") as client:
        HeartbeatWorker(config(host="localhost", tls=True), password="test-only")
        context = client.return_value.tls_set_context.call_args.args[0]
        assert context.check_hostname is True
        assert context.verify_mode.name == "CERT_REQUIRED"
        client.assert_called_once()
        assert client.call_args.kwargs["manual_ack"] is True


def message(worker, **changes):
    return SimpleNamespace(
        topic=next(iter(worker.topics)),
        payload=b"{}",
        retain=False,
        qos=1,
        mid=7,
        **changes,
    )


def test_ack_only_after_storage_commit(worker):
    order = []

    def persisted(*_a, **kwargs):
        assert kwargs["received_at"].tzinfo == UTC
        assert kwargs["received_at"] <= datetime.now(UTC)
        assert kwargs["authenticated_device_id"] in worker.settings.device_ids
        order.append("persisted")

    worker.client.ack.side_effect = lambda *_: (
        order.append("ack") or mqtt.MQTT_ERR_SUCCESS
    )
    with (
        patch("app.modules.iot.heartbeat_worker.Session"),
        patch(
            "app.modules.iot.heartbeat_worker.persist_registered_heartbeat",
            side_effect=persisted,
        ),
    ):
        worker._on_message(worker.client, None, message(worker))
    assert order == ["persisted", "ack"] and worker.committed == 1


def test_qos0_is_best_effort_without_fake_ack(worker):
    incoming = message(worker)
    incoming.qos = 0
    with (
        patch("app.modules.iot.heartbeat_worker.Session"),
        patch("app.modules.iot.heartbeat_worker.persist_registered_heartbeat"),
    ):
        worker._on_message(worker.client, None, incoming)
    worker.client.ack.assert_not_called()


@pytest.mark.parametrize("invalid", ["topic", "oversize", "retained"])
def test_terminal_rejection_cannot_renew_state(worker, invalid):
    incoming = message(worker)
    if invalid == "topic":
        incoming.topic = "freshlink/v1/devices/forged/heartbeat"
    if invalid == "oversize":
        incoming.payload = b"x" * (MAX_PAYLOAD_BYTES + 1)
    with (
        patch("app.modules.iot.heartbeat_worker.Session"),
        patch(
            "app.modules.iot.heartbeat_worker.persist_registered_heartbeat",
            side_effect=IngressRejected("retained_heartbeat", b"{}"),
        ) as storage,
    ):
        worker._on_message(worker.client, None, incoming)
    if invalid != "retained":
        storage.assert_not_called()
    assert worker.committed == 0 and worker.rejections == 1
    worker.client.ack.assert_called_once_with(7, 1)


def test_storage_failure_stops_and_never_acknowledges(worker, caplog):
    with (
        patch("app.modules.iot.heartbeat_worker.Session"),
        patch(
            "app.modules.iot.heartbeat_worker.persist_registered_heartbeat",
            side_effect=RuntimeError("private-db-value"),
        ),
    ):
        worker._on_message(worker.client, None, message(worker))
    assert worker.fatal_reason == "heartbeat_storage_failed"
    assert worker.stop_requested.is_set()
    worker.client.ack.assert_not_called()
    assert "private-db-value" not in caplog.text
    worker._on_message(worker.client, None, message(worker))
    worker.client.ack.assert_not_called()


def test_ack_failure_is_visible_not_false_success(worker):
    worker.client.ack.return_value = mqtt.MQTT_ERR_NO_CONN
    with (
        patch("app.modules.iot.heartbeat_worker.Session"),
        patch("app.modules.iot.heartbeat_worker.persist_registered_heartbeat"),
    ):
        worker._on_message(worker.client, None, message(worker))
    assert worker.fatal_reason == "broker_ack_failed"


def test_subscription_scope_and_retained_flags(worker):
    worker.client.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 3)
    worker._on_connect(
        worker.client, None, None, ReasonCode(PacketTypes.CONNACK, "Success"), None
    )
    subscriptions = worker.client.subscribe.call_args.args[0]
    assert {row[0] for row in subscriptions} == set(worker.topics)
    assert all(row[1].retainAsPublished is True for row in subscriptions)
    worker._on_subscribe(
        worker.client, None, 3, [ReasonCode(PacketTypes.SUBACK, "Granted QoS 1")], None
    )
    assert worker.ready.is_set()
    worker._on_disconnect(worker.client, None, None, None, None)
    assert not worker.ready.is_set()


@pytest.mark.parametrize("failure", ["auth", "subscribe", "suback"])
def test_broker_rejections_stop(worker, failure):
    worker.client.subscribe.return_value = (mqtt.MQTT_ERR_NO_CONN, 1)
    if failure == "auth":
        worker._on_connect(
            worker.client,
            None,
            None,
            ReasonCode(PacketTypes.CONNACK, "Not authorized"),
            None,
        )
    elif failure == "subscribe":
        worker._on_connect(
            worker.client, None, None, ReasonCode(PacketTypes.CONNACK, "Success"), None
        )
    else:
        worker._on_subscribe(worker.client, None, 1, [], None)
    assert worker.stop_requested.is_set()


def test_scan_clean_session_and_stop(worker):
    with (
        patch("app.modules.iot.heartbeat_worker.Session"),
        patch("app.modules.iot.heartbeat_worker.scan_registered_device") as scan,
    ):
        worker.scan()
        assert scan.call_count == len(worker.settings.device_ids)
        worker.stop_requested.set()
        worker.scan()
        assert scan.call_count == len(worker.settings.device_ids)


def test_run_stops_cleanly_on_scan_failure(worker):
    worker.client.connect.return_value = mqtt.MQTT_ERR_SUCCESS
    worker.client.loop.return_value = mqtt.MQTT_ERR_SUCCESS
    with patch.object(worker, "scan", side_effect=RuntimeError("private DB error")):
        with pytest.raises(RuntimeError, match="heartbeat_scan_failed"):
            worker.run(max_seconds=1)
    worker.client.disconnect.assert_called_once()


def test_run_network_failure_remains_bounded(worker):
    worker.client.connect.side_effect = OSError("network unavailable")
    with patch.object(worker, "scan") as scan:
        worker.run(max_seconds=0.2)
    assert scan.called and worker.client.connect.call_count == 1
    worker.client.disconnect.assert_called_once()


def test_lost_network_still_scans_and_disconnects(worker):
    worker.client.connect.return_value = mqtt.MQTT_ERR_SUCCESS
    worker.client.loop.return_value = mqtt.MQTT_ERR_NO_CONN
    with patch.object(worker, "scan") as scan:
        worker.run(max_seconds=0.2)
    assert scan.called
    assert not worker.ready.is_set()


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_invalid_duration_rejected_before_network(worker, duration):
    with pytest.raises(ValueError, match="finite and positive"):
        worker.run(max_seconds=duration)
    worker.client.connect.assert_not_called()
