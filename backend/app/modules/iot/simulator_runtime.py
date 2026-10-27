"""Continuous software-device MQTT runtime; no application API or C/B wiring."""

import argparse
import json
import logging
import os
import queue
import signal
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode
from paho.mqtt.subscribeoptions import SubscribeOptions
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.iot.simulator import PlantSample, SimulatorRejected, SoftwarePlant

logger = logging.getLogger(__name__)


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=8883, ge=1, le=65535)
    tls: bool = True
    ca_file: Path | None = None
    isolated_test_plaintext: bool = False
    sensor_username: str = Field(min_length=1, max_length=128)
    actuator_username: str = Field(min_length=1, max_length=128)
    sensor_id: UUID
    actuator_id: UUID
    warehouse_id: UUID
    state_file: Path
    sample_period_seconds: float = Field(default=30, ge=0.5, le=30)
    heartbeat_period_seconds: float = Field(default=30, ge=0.5, le=30)
    resend_period_seconds: float = Field(default=10, ge=1, le=30)

    @model_validator(mode="after")
    def secure_transport(self) -> RuntimeSettings:
        if not self.tls and (
            not self.isolated_test_plaintext
            or self.host not in {"127.0.0.1", "localhost", "broker"}
        ):
            raise ValueError(
                "plaintext is allowed only for explicitly isolated local tests"
            )
        if self.sensor_id == self.actuator_id:
            raise ValueError("sensor and actuator identities must differ")
        return self


@dataclass(frozen=True)
class CommandDelivery:
    topic: str
    payload: bytes
    retained: bool
    mid: int
    qos: int


class DeviceRuntime:
    """Network callbacks enqueue bounded work; one owner executes device state.

    Incoming command PUBACK is sent only after persistence (or a durably audited
    permanent rejection). Original execution ACKs survive a crash before sending.
    Outgoing PUBACK is ONLY transport evidence, never C storage/server execution.
    """

    def __init__(
        self, settings: RuntimeSettings, *, sensor_password: str, actuator_password: str
    ) -> None:
        if not sensor_password or not actuator_password:
            raise ValueError("MQTT secrets must be supplied by the account owner")
        self.settings = settings
        self.stop_requested = threading.Event()
        self.ready_sensor = threading.Event()
        self.ready_actuator = threading.Event()
        self.fatal_reason: str | None = None
        self.commands: queue.Queue[CommandDelivery] = queue.Queue(maxsize=32)
        self.plant = SoftwarePlant(
            settings.state_file,
            actuator_id=settings.actuator_id,
            sensor_id=settings.sensor_id,
            warehouse_id=settings.warehouse_id,
            started_at=datetime.now(UTC),
        )
        self.plant.resume_after_gap(datetime.now(UTC))
        self.sensor = self._client(
            settings.sensor_id,
            settings.sensor_username,
            sensor_password,
            actuator=False,
        )
        self.actuator = self._client(
            settings.actuator_id,
            settings.actuator_username,
            actuator_password,
            actuator=True,
        )
        self._sample_due = 0.0
        self._heartbeat_due = 0.0
        self._telemetry: dict[UUID, tuple[mqtt.MQTTMessageInfo, float]] = {}
        self._acknowledgements: dict[UUID, mqtt.MQTTMessageInfo] = {}
        self._sample_publish_window = float("-inf")
        self._sample_publish_attempts = 0
        self._sample_retry_at = float("-inf")

    def _fail(self, reason: str) -> None:
        self.fatal_reason = reason
        self.stop_requested.set()
        logger.error("Simulator stopped: %s", reason)

    def _client(
        self, identifier: UUID, username: str, password: str, *, actuator: bool
    ) -> mqtt.Client:
        client = mqtt.Client(
            CallbackAPIVersion.VERSION2,
            client_id=f"freshlink-device-{identifier}",
            protocol=mqtt.MQTTv5,
            manual_ack=actuator,
        )
        client.username_pw_set(username, password)
        client.max_queued_messages_set(32)
        client.max_inflight_messages_set(16)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.connect_timeout = 5
        if self.settings.tls:
            context = ssl.create_default_context(cafile=self.settings.ca_file)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            client.tls_set_context(context)  # Certificate/hostname checks retained.
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_publish = self._on_publish
        if actuator:
            client.on_subscribe = self._on_subscribe
            client.on_message = self._on_message
        return client

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason: ReasonCode,
        properties: Properties | None,
    ) -> None:
        if reason.is_failure:
            self._fail("mqtt_authentication_rejected")
            return
        if client is self.actuator:
            result, _mid = client.subscribe(
                f"freshlink/v1/devices/{self.settings.actuator_id}/commands",
                options=SubscribeOptions(qos=1, retainAsPublished=True),
            )
            if result != mqtt.MQTT_ERR_SUCCESS:
                self._fail("mqtt_subscribe_failed")
        else:
            self.ready_sensor.set()

    def _on_subscribe(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: int,
        reasons: list[ReasonCode],
        properties: Properties | None,
    ) -> None:
        if not reasons or any(reason.is_failure for reason in reasons):
            self._fail("mqtt_subscription_rejected")
        else:
            self.ready_actuator.set()

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.DisconnectFlags,
        reason: ReasonCode,
        properties: Properties | None,
    ) -> None:
        (self.ready_actuator if client is self.actuator else self.ready_sensor).clear()

    def _on_publish(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: int,
        reason: ReasonCode,
        properties: Properties | None,
    ) -> None:
        if reason.is_failure:
            self._fail("mqtt_publish_rejected")

    def _on_message(
        self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage
    ) -> None:
        try:
            self.commands.put_nowait(
                CommandDelivery(
                    message.topic,
                    message.payload,
                    message.retain,
                    message.mid,
                    message.qos,
                )
            )
        except queue.Full:
            # No automatic PUBACK; persistent broker session may redeliver later.
            self._fail("command_queue_full")

    def start(self) -> None:
        for client in (self.sensor, self.actuator):
            properties = Properties(PacketTypes.CONNECT)  # type: ignore[no-untyped-call]  # Paho 2.1 constructor lacks annotations.
            properties.SessionExpiryInterval = 300
            properties.ReceiveMaximum = 32
            properties.MaximumPacketSize = 8192
            client.connect_async(
                self.settings.host,
                self.settings.port,
                keepalive=30,
                clean_start=False,
                properties=properties,
            )
            client.loop_start()

    def stop(self) -> None:
        self.stop_requested.set()
        for client in (self.sensor, self.actuator):
            client.disconnect()
            client.loop_stop()

    def wait_ready(self, timeout: float = 10) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.stop_requested.is_set():
            if self.ready_sensor.is_set() and self.ready_actuator.is_set():
                return True
            self.stop_requested.wait(0.05)
        return False

    def _execute_commands(self, now: datetime) -> None:
        for _ in range(32):
            try:
                delivery = self.commands.get_nowait()
            except queue.Empty:
                break
            try:
                if delivery.qos != 1:
                    raise SimulatorRejected("invalid_command_qos")
                ack = self.plant.execute(
                    delivery.payload,
                    topic=delivery.topic,
                    now=now,
                    retained=delivery.retained,
                )
                # A duplicate requests a new transport attempt of the ORIGINAL
                # ACK. Do not let an old in-flight publish clear this new request.
                self._acknowledgements.pop(UUID(ack["command_id"]), None)
            except SimulatorRejected as error:
                self.plant.audit_rejection(
                    delivery.payload, now=now, reason_code=str(error)
                )
            # Disk/transaction failures propagate BEFORE this transport PUBACK.
            if delivery.qos >= 1:
                self.actuator.ack(delivery.mid, delivery.qos)

    def _publish_acks(self) -> None:
        if not self.ready_actuator.is_set():
            return
        topic = f"freshlink/v1/devices/{self.settings.actuator_id}/acks"
        for identifier, text in self.plant.pending_acknowledgements():
            prior = self._acknowledgements.get(identifier)
            if prior is not None:
                if prior.is_published():
                    self.plant.confirm_ack_transport(identifier)
                    del self._acknowledgements[identifier]
                continue
            info = self.actuator.publish(topic, text.encode(), qos=1, retain=False)
            if info.rc == mqtt.MQTT_ERR_SUCCESS:
                self._acknowledgements[identifier] = info

    def _publish_samples(self, monotonic: float) -> None:
        if not self.ready_sensor.is_set():
            return
        pending = self.plant.pending()
        if not pending:
            return
        pending_ids = {UUID(sample.payload["message_id"]) for sample in pending}
        self._telemetry = {
            identifier: state
            for identifier, state in self._telemetry.items()
            if identifier in pending_ids
        }
        # The journal has no C storage confirmation channel yet. Therefore its
        # replay rate must not grow with the number of unconfirmed samples.
        # Count failed publish calls too; frequent step() calls cannot bypass it.
        if monotonic - self._sample_publish_window >= 1:
            self._sample_publish_window = monotonic
            self._sample_publish_attempts = 0
        if self._sample_publish_attempts >= 4:
            return
        ordered = list(enumerate(pending))

        # Latest never-sent data precedes the older journal tail, including after
        # a restart. Every remaining tail entry retains its bytes and a turn.
        # Least-recently-attempted retry ordering cannot skip old entries
        # forever as new samples extend the journal during an active round.
        def priority(entry: tuple[int, PlantSample]) -> tuple[int, float]:
            index, sample = entry
            previous = self._telemetry.get(UUID(sample.payload["message_id"]))
            return (1, previous[1]) if previous is not None else (0, -float(index))

        ordered.sort(key=priority)
        for _index, sample in ordered:
            identifier = UUID(sample.payload["message_id"])
            previous = self._telemetry.get(identifier)
            if previous is not None:
                info, last_sent = previous
                if (
                    not info.is_published()
                    or monotonic - last_sent < self.settings.resend_period_seconds
                    or monotonic - self._sample_retry_at
                    < self.settings.resend_period_seconds
                ):
                    continue
            self._sample_publish_attempts += 1
            if previous is not None:
                # At most one journal retry per configured resend interval per
                # device, not one retry per pending row per interval.
                self._sample_retry_at = monotonic
            info = self.sensor.publish(
                f"freshlink/v1/devices/{self.settings.sensor_id}/telemetry",
                sample.payload_text.encode(),
                qos=1,
                retain=False,
            )
            if info.rc == mqtt.MQTT_ERR_SUCCESS:
                self._telemetry[identifier] = (info, monotonic)
            elif previous is not None:
                # Even a failed retry yields a turn to other durable entries;
                # retain its prior transport state and all journal bytes.
                self._telemetry[identifier] = (previous[0], monotonic)
            if self._sample_publish_attempts >= 4:
                break
        # Deliberately NO confirm_storage: server/C persistence is not connected.

    def _heartbeat(self, now: datetime) -> None:
        for client, ready, identifier in (
            (self.sensor, self.ready_sensor, self.settings.sensor_id),
            (self.actuator, self.ready_actuator, self.settings.actuator_id),
        ):
            if ready.is_set():
                body = {
                    "schema_version": "1.0",
                    "device_id": str(identifier),
                    "message_id": str(uuid4()),
                    "sent_at": now.isoformat(),
                }
                properties = Properties(PacketTypes.PUBLISH)  # type: ignore[no-untyped-call]  # Paho 2.1 constructor lacks annotations.
                properties.MessageExpiryInterval = (
                    int(self.settings.heartbeat_period_seconds) + 1
                )
                # QoS0 heartbeat is fresh/best-effort, not a durable replay queue.
                client.publish(
                    f"freshlink/v1/devices/{identifier}/heartbeat",
                    json.dumps(body),
                    qos=0,
                    retain=False,
                    properties=properties,
                )

    def step(self, *, now: datetime, monotonic: float) -> None:
        if self.stop_requested.is_set():
            return
        if monotonic >= self._sample_due:
            self.plant.sample(now)
            self._sample_due = monotonic + self.settings.sample_period_seconds
        self._execute_commands(now)
        self._publish_acks()
        self._publish_samples(monotonic)
        if monotonic >= self._heartbeat_due:
            self._heartbeat(now)
            self._heartbeat_due = monotonic + self.settings.heartbeat_period_seconds

    def run(self, *, max_seconds: float | None = None) -> None:
        deadline = time.monotonic() + max_seconds if max_seconds is not None else None
        try:
            self.start()
            while not self.stop_requested.is_set() and (
                deadline is None or time.monotonic() < deadline
            ):
                self.step(now=datetime.now(UTC), monotonic=time.monotonic())
                self.stop_requested.wait(0.1)
            if self.fatal_reason:
                raise RuntimeError(self.fatal_reason)
        finally:
            self.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a configured FreshLink software device; does not start the server"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float)
    arguments = parser.parse_args()
    if arguments.max_seconds is not None and not 0 < arguments.max_seconds <= 3600:
        parser.error("max-seconds must be positive and <=3600")
    settings = RuntimeSettings.model_validate_json(arguments.config.read_bytes())
    runtime = DeviceRuntime(
        settings,
        sensor_password=os.environ.get("FRESHLINK_SENSOR_MQTT_PASSWORD", ""),
        actuator_password=os.environ.get("FRESHLINK_ACTUATOR_MQTT_PASSWORD", ""),
    )

    def request_stop(_signal: int, _frame: Any) -> None:
        runtime.stop_requested.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    runtime.run(max_seconds=arguments.max_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
