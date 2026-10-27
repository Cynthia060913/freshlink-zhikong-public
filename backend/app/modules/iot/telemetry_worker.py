"""Bounded MQTT admission and independent durable C delivery.

The receiver never calls C inside a broker callback. Its QoS1 ACK means only
that A committed the exact trusted envelope, not C storage/business success.
The integration owner must supply B's real lookup and C's actual durable port.
No schema creation, public endpoint, implicit adapter or application startup.
"""

import logging
import math
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.modules.iot.heartbeat_worker import HeartbeatWorker, HeartbeatWorkerSettings
from app.modules.iot.ingress import MAX_PAYLOAD_BYTES, IngressRejected
from app.modules.iot.registry import WarehouseExists, receive_registered_telemetry
from app.modules.iot.telemetry_inbox import (
    InboxResult,
    QueueLimits,
    StoreSink,
    claim_next,
    deliver_claim,
    enqueue,
    record_rejection,
)

logger = logging.getLogger(__name__)
WarehouseLookupFactory = Callable[[Session], WarehouseExists]


def _frontier_epochs(value: Mapping[UUID, UUID] | None) -> dict[UUID, UUID] | None:
    if value is None:
        return None
    copied = dict(value)
    if not 1 <= len(copied) <= 14 or not all(
        isinstance(device, UUID) and isinstance(epoch, UUID)
        for device, epoch in copied.items()
    ):
        raise ValueError("frontier scope requires 1-14 trusted device/epoch UUIDs")
    return copied


class TelemetryReceiver(HeartbeatWorker):
    def __init__(
        self,
        settings: HeartbeatWorkerSettings,
        *,
        password: str,
        warehouse_lookup_factory: WarehouseLookupFactory,
        limits: QueueLimits = QueueLimits(),
        frontier_epochs: Mapping[UUID, UUID] | None = None,
    ) -> None:
        selected_epochs = _frontier_epochs(frontier_epochs)
        if selected_epochs is not None and set(selected_epochs) != set(
            settings.device_ids
        ):
            raise ValueError("receiver frontier scope must match verified device scope")
        super().__init__(settings, password=password)
        self.frontier_epochs = selected_epochs
        self.warehouse_lookup_factory = warehouse_lookup_factory
        self.limits = limits
        self.topics = {
            f"freshlink/v1/devices/{identity}/telemetry": identity
            for identity in settings.device_ids
        }

    def _connect(self) -> int:
        properties = Properties(PacketTypes.CONNECT)  # type: ignore[no-untyped-call]
        # A stable, dedicated client_id is mandatory for broker-owned redelivery.
        # Broker limits/disk durability must be verified separately by operators.
        properties.SessionExpiryInterval = 86400
        properties.ReceiveMaximum = 14
        properties.MaximumPacketSize = MAX_PAYLOAD_BYTES + 1024
        return self.client.connect(
            self.settings.host,
            self.settings.port,
            keepalive=30,
            clean_start=False,
            properties=properties,
        )

    def _on_message(
        self, client: mqtt.Client, _data: Any, message: mqtt.MQTTMessage
    ) -> None:
        if self.stop_requested.is_set():
            return
        received_at = datetime.now(UTC)
        identity = self.topics.get(message.topic)
        epoch = (
            self.frontier_epochs.get(identity)
            if self.frontier_epochs is not None and identity is not None
            else None
        )
        try:
            if identity is None:
                raise IngressRejected("topic_outside_verified_scope", message.payload)
            if message.retain:
                raise IngressRejected("retained_telemetry", message.payload)
            if message.qos != 1:
                raise IngressRejected("unexpected_qos", message.payload)
            with Session(self.engine) as session:
                session.connection().execute(
                    text("SET LOCAL statement_timeout = '5000ms'")
                )
                envelope = receive_registered_telemetry(
                    session,
                    authenticated_device_id=identity,
                    credential_enabled=True,
                    warehouse_exists=self.warehouse_lookup_factory(session),
                    topic=message.topic,
                    payload=message.payload,
                    received_at=received_at,
                )
            # Close the registration read before admission starts its transaction.
            result = enqueue(
                self.engine,
                envelope,
                now=received_at,
                limits=self.limits,
                frontier_epoch=epoch,
            )
            if result.status not in {
                "queued",
                "already_queued",
                "already_processed",
                "invalid",
                "conflict",
            }:
                self._fail("telemetry_admission_unavailable")
                return
            if result.status in {"invalid", "conflict"}:
                self.rejections += 1
            else:
                self.committed += 1
        except IngressRejected as rejection:
            if not record_rejection(
                self.engine,
                rejection,
                device_id=identity,
                now=received_at,
                limits=self.limits,
                frontier_epoch=epoch,
            ):
                self._fail("telemetry_audit_unavailable")
                return
            self.rejections += 1
            logger.warning("Telemetry rejected: %s", rejection.reason_code)
        except Exception:
            self._fail("telemetry_admission_unavailable")
            return
        if (
            message.qos > 0
            and client.ack(message.mid, message.qos) != mqtt.MQTT_ERR_SUCCESS
        ):
            self._fail("telemetry_broker_ack_failed")

    def scan(self) -> None:
        # Deliberately no C processing or heartbeat writes on the MQTT loop.
        return


class TelemetryDeliveryWorker:
    """Bounded claim batches, no in-memory payload queue; caller owns the engine.

    The injected C adapter must impose its own finite query/network timeouts.
    It must return only after its durable commit; arbitrary callables are not
    cancellable safely. A lease expiry prevents stale A checkpoint writes but
    does not kill a hung C call. C's stable reception ID must be idempotent.
    """

    def __init__(
        self,
        engine: Engine,
        sink: StoreSink,
        *,
        poll_seconds: float = 0.5,
        limits: QueueLimits = QueueLimits(),
        frontier_epochs: Mapping[UUID, UUID] | None = None,
    ) -> None:
        if not math.isfinite(poll_seconds) or not 0.1 <= poll_seconds <= 10:
            raise ValueError("poll period must be between 0.1 and 10 seconds")
        self.engine, self.sink = engine, sink
        self.frontier_epochs = _frontier_epochs(frontier_epochs)
        self.poll_seconds, self.limits = poll_seconds, limits
        self.stop_requested = threading.Event()
        self.completed = 0
        self.failures = 0
        self.last_status = "idle"

    def run_once(self) -> InboxResult | None:
        claim = claim_next(self.engine)
        if claim is None:
            self.last_status = "idle"
            return None
        identity = claim.envelope.authenticated_device_id
        if self.frontier_epochs is not None and identity not in self.frontier_epochs:
            self.last_status = "blocked"
            return InboxResult(
                "blocked", claim.envelope.reception_id, "unverified_stream_scope"
            )
        epoch = (
            self.frontier_epochs.get(identity)
            if self.frontier_epochs is not None
            else None
        )
        result = deliver_claim(
            self.engine, claim, self.sink, limits=self.limits, frontier_epoch=epoch
        )
        self.last_status = result.status
        if result.status == "delivered":
            self.completed += 1
        return result

    def run(self, *, max_seconds: float | None = None) -> None:
        if max_seconds is not None and (
            not math.isfinite(max_seconds) or max_seconds <= 0
        ):
            raise ValueError("max_seconds must be finite and positive")
        start = time.monotonic()
        while not self.stop_requested.is_set():
            if max_seconds is not None and time.monotonic() - start >= max_seconds:
                break
            try:
                # Drain ready work rather than sleeping after every row. Every
                # claim retains its own transaction, lease and C receipt checks.
                # Fourteen is bounded by the supported logical device scope.
                for _ in range(14):
                    if self.stop_requested.is_set() or (
                        max_seconds is not None
                        and time.monotonic() - start >= max_seconds
                    ):
                        break
                    result = self.run_once()
                    if result is None or result.status != "delivered":
                        break
            except Exception:
                # DB failure retains durable leases/raw. Never print exception
                # text, bypass C, reset budgets or report a successful delivery.
                self.failures += 1
                self.last_status = "storage_unavailable"
                logger.warning("Telemetry delivery temporarily unavailable")
            if max_seconds is not None and time.monotonic() - start >= max_seconds:
                break
            self.stop_requested.wait(self.poll_seconds)
