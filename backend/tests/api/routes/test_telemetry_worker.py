"""Receiver order/security and independent dispatcher; PG tests are separate."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import paho.mqtt.client as mqtt
import pytest

from app.modules.iot.heartbeat_worker import HeartbeatWorkerSettings
from app.modules.iot.ingress import IngressRejected
from app.modules.iot.telemetry_inbox import InboxResult
from app.modules.iot.telemetry_worker import TelemetryDeliveryWorker, TelemetryReceiver

MODULE = "app.modules.iot.telemetry_worker."


@pytest.fixture
def receiver():
    settings = HeartbeatWorkerSettings(
        host="broker",
        port=1883,
        tls=False,
        isolated_test_plaintext=True,
        username="fixture-readonly",
        client_id="fixture-durable-receiver",
        acl_confirmed=True,
        device_ids=[uuid4()],
    )
    worker = TelemetryReceiver(
        settings, password="fixture-only", warehouse_lookup_factory=MagicMock()
    )
    worker.client = MagicMock()
    worker.client.ack.return_value = mqtt.MQTT_ERR_SUCCESS
    yield worker
    worker.engine.dispose()


def message(receiver, **changes):
    return SimpleNamespace(
        **{
            "topic": next(iter(receiver.topics)),
            "payload": b"{}",
            "retain": False,
            "qos": 1,
            "mid": 7,
            **changes,
        }
    )


def test_persistent_broker_session_and_bounded_receive_window(receiver):
    receiver._connect()
    arguments = receiver.client.connect.call_args.kwargs
    assert arguments["clean_start"] is False
    properties = arguments["properties"]
    assert properties.SessionExpiryInterval == 86400
    assert properties.ReceiveMaximum == 14
    assert properties.MaximumPacketSize == 33792
    assert all(topic.endswith("/telemetry") for topic in receiver.topics)
    receiver.scan()  # Not the parent's heartbeat scan, nor C delivery.


@pytest.mark.parametrize(
    "status", ["queued", "already_queued", "already_processed", "invalid", "conflict"]
)
def test_ack_only_after_closed_registration_and_durable_admission(receiver, status):
    order = []
    identity = uuid4()
    with (
        patch(MODULE + "Session") as session,
        patch(MODULE + "receive_registered_telemetry") as receive,
        patch(MODULE + "enqueue", return_value=InboxResult(status, identity)) as admit,
    ):
        session.return_value.__exit__.side_effect = lambda *_: order.append("closed")
        admit.side_effect = lambda *_a, **_k: (
            order.append("committed") or InboxResult(status, identity)
        )
        receiver.client.ack.side_effect = lambda *_: (
            order.append("ack") or mqtt.MQTT_ERR_SUCCESS
        )
        receiver._on_message(receiver.client, None, message(receiver))
        assert receive.call_args.kwargs["credential_enabled"] is True
        receiver.warehouse_lookup_factory.assert_called_once()
    assert order == ["closed", "committed", "ack"]
    assert receiver.rejections == int(status in {"invalid", "conflict"})


@pytest.mark.parametrize("status", ["queue_full", "retryable_failure", "unknown"])
def test_full_queue_or_db_failure_stops_without_ack(receiver, status):
    with (
        patch(MODULE + "Session"),
        patch(MODULE + "receive_registered_telemetry"),
        patch(MODULE + "enqueue", return_value=InboxResult(status, uuid4())),
    ):
        receiver._on_message(receiver.client, None, message(receiver))
    assert receiver.fatal_reason == "telemetry_admission_unavailable"
    receiver.client.ack.assert_not_called()


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"retain": True}, "retained_telemetry"),
        ({"qos": 0}, "unexpected_qos"),
        ({"topic": "forged/topic"}, "topic_outside_verified_scope"),
    ],
)
def test_invalid_transport_is_audited_before_ack(receiver, changes, reason):
    with patch(MODULE + "record_rejection", return_value=True) as audit:
        receiver._on_message(receiver.client, None, message(receiver, **changes))
        assert audit.call_args.args[1].reason_code == reason
    assert receiver.rejections == 1 and receiver.committed == 0
    assert receiver.client.ack.call_count == int(changes.get("qos") != 0)


def test_rejection_audit_failure_stops_without_ack(receiver):
    with patch(MODULE + "record_rejection", return_value=False):
        receiver._on_message(receiver.client, None, message(receiver, retain=True))
    assert receiver.fatal_reason == "telemetry_audit_unavailable"
    receiver.client.ack.assert_not_called()


def test_bad_registered_packet_is_audited_and_unknown_lookup_error_is_not_permanent(
    receiver,
):
    with (
        patch(MODULE + "Session"),
        patch(
            MODULE + "receive_registered_telemetry",
            side_effect=IngressRejected("invalid_json", b"x"),
        ),
        patch(MODULE + "record_rejection", return_value=True),
    ):
        receiver._on_message(receiver.client, None, message(receiver))
    assert receiver.rejections == 1
    receiver.client.ack.reset_mock()
    with (
        patch(MODULE + "Session"),
        patch(
            MODULE + "receive_registered_telemetry",
            side_effect=RuntimeError("synthetic private diagnostic"),
        ),
    ):
        receiver._on_message(receiver.client, None, message(receiver))
    receiver.client.ack.assert_not_called()
    assert receiver.fatal_reason == "telemetry_admission_unavailable"


def test_ack_failure_and_stopped_callback_visible(receiver):
    receiver.client.ack.return_value = mqtt.MQTT_ERR_NO_CONN
    with patch(MODULE + "record_rejection", return_value=True):
        receiver._on_message(receiver.client, None, message(receiver, retain=True))
        receiver._on_message(receiver.client, None, message(receiver, retain=True))
    assert receiver.fatal_reason == "telemetry_broker_ack_failed"
    assert receiver.client.ack.call_count == 1


def test_delivery_is_independent_single_claim_with_no_memory_queue():
    engine, sink = MagicMock(), MagicMock()
    worker = TelemetryDeliveryWorker(engine, sink)
    result = InboxResult("delivered", uuid4())
    with (
        patch(MODULE + "claim_next", return_value=None),
        patch(MODULE + "deliver_claim") as deliver,
    ):
        assert worker.run_once() is None and worker.last_status == "idle"
        deliver.assert_not_called()
    with (
        patch(
            MODULE + "claim_next",
            return_value=(
                claim := SimpleNamespace(
                    envelope=SimpleNamespace(authenticated_device_id=uuid4())
                )
            ),
        ),
        patch(MODULE + "deliver_claim", return_value=result) as deliver,
    ):
        assert worker.run_once() == result and worker.completed == 1
        deliver.assert_called_once_with(
            engine, claim, sink, limits=worker.limits, frontier_epoch=None
        )


def test_frontier_delivery_scope_is_explicit_and_unknown_claim_never_calls_c():
    identity, epoch = uuid4(), uuid4()
    mapping = {identity: epoch}
    engine, sink = MagicMock(), MagicMock()
    worker = TelemetryDeliveryWorker(engine, sink, frontier_epochs=mapping)
    mapping.clear()  # Constructor owns a copy, not mutable external policy.
    claim = SimpleNamespace(
        envelope=SimpleNamespace(authenticated_device_id=identity, reception_id=uuid4())
    )
    with (
        patch(MODULE + "claim_next", return_value=claim),
        patch(MODULE + "deliver_claim") as deliver,
    ):
        worker.run_once()
        assert deliver.call_args.kwargs["frontier_epoch"] == epoch
    claim.envelope.authenticated_device_id = uuid4()
    with (
        patch(MODULE + "claim_next", return_value=claim),
        patch(MODULE + "deliver_claim") as deliver,
    ):
        result = worker.run_once()
        assert result.status == "blocked" and result.reason == "unverified_stream_scope"
        deliver.assert_not_called()
    for invalid in ({}, {identity: "not-an-epoch"}, {str(identity): epoch}):
        with pytest.raises(ValueError):
            TelemetryDeliveryWorker(engine, sink, frontier_epochs=invalid)


def test_frontier_receiver_passes_same_epoch_to_admission_and_known_rejection(receiver):
    identity = next(iter(receiver.topics.values()))
    epoch = uuid4()
    receiver.frontier_epochs = {identity: epoch}
    with (
        patch(MODULE + "Session"),
        patch(MODULE + "receive_registered_telemetry"),
        patch(MODULE + "enqueue", return_value=InboxResult("queued", uuid4())) as admit,
    ):
        receiver._on_message(receiver.client, None, message(receiver))
        assert admit.call_args.kwargs["frontier_epoch"] == epoch
    with patch(MODULE + "record_rejection", return_value=True) as reject:
        receiver._on_message(receiver.client, None, message(receiver, retain=True))
        assert reject.call_args.kwargs["frontier_epoch"] == epoch


def test_frontier_receiver_rejects_partial_config_before_starting(receiver):
    with pytest.raises(ValueError):
        TelemetryReceiver(
            receiver.settings,
            password="fixture-only",
            warehouse_lookup_factory=MagicMock(),
            frontier_epochs={uuid4(): uuid4()},
        )


def test_dispatcher_transient_failure_is_sanitized_and_stoppable(caplog):
    worker = TelemetryDeliveryWorker(MagicMock(), MagicMock(), poll_seconds=0.1)
    with patch.object(
        worker, "run_once", side_effect=RuntimeError("synthetic secret detail")
    ):
        worker.run(max_seconds=0.15)
    assert worker.failures > 0 and worker.last_status == "storage_unavailable"
    assert "synthetic secret detail" not in caplog.text
    worker.stop_requested.set()
    with patch.object(worker, "run_once") as tick:
        worker.run()
        tick.assert_not_called()


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_run_or_poll_config_rejected(value):
    with pytest.raises(ValueError):
        TelemetryDeliveryWorker(MagicMock(), MagicMock(), poll_seconds=value)
    worker = TelemetryDeliveryWorker(MagicMock(), MagicMock())
    with pytest.raises(ValueError):
        worker.run(max_seconds=value)
