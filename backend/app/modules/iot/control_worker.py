"""Bounded MQTT adapter for the durable A03 queue, not a B integration bypass.

Only run against an ACL-verified broker: device credentials publish their own
ACKs; the controller can read ACKs but must never publish them. Single-owner
callbacks keep DB commits before MQTT acknowledgements. A fresh MQTT client on
connection loss discards Paho's volatile outbound queue; retries use DB leases
and the original command ID/payload, never an in-memory delivery claim.
"""

import logging
import math
import ssl
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode
from paho.mqtt.subscribeoptions import SubscribeOptions
from pydantic import Field
from sqlalchemy import text
from sqlmodel import Session, col, create_engine, select

from app.core.db import engine
from app.modules.iot.control_models import DeviceCommand
from app.modules.iot.control_storage import (
    ACTIVE,
    ControlError,
    Dispatch,
    claim_dispatch,
    expire_command,
    finish_dispatch,
    receive_command_ack,
)
from app.modules.iot.heartbeat_worker import HeartbeatWorkerSettings
from app.modules.iot.registry import WarehouseExists

logger = logging.getLogger(__name__)
WarehouseLookupFactory = Callable[[Session], WarehouseExists]
Outcome = Literal["accepted", "transient", "permanent"]


class ControlWorkerSettings(HeartbeatWorkerSettings):
    # device_ids is an explicit actuator allow-list, not a wildcard subscription.
    publish_timeout_seconds: float = Field(default=5, ge=1, le=10)


class ControlWorker:
    def __init__(
        self,
        settings: ControlWorkerSettings,
        *,
        password: str,
        warehouse_lookup_factory: WarehouseLookupFactory,
    ) -> None:
        if not password:
            raise ValueError("MQTT password required from account owner")
        self.settings = settings
        self._password = password
        # Required dependency: no permissive default or synthetic production B.
        self.warehouse_lookup_factory = warehouse_lookup_factory
        self.engine = create_engine(
            engine.url,
            pool_pre_ping=True,
            pool_timeout=5,
            connect_args={"connect_timeout": 5},
        )
        self.topics = {
            f"freshlink/v1/devices/{identity}/acks": identity
            for identity in settings.device_ids
        }
        self.ready = threading.Event()
        self.stop_requested = threading.Event()
        self.fatal_reason: str | None = None
        self.rejections = 0
        self.committed = 0
        self._pending: dict[int, tuple[Dispatch, float]] = {}
        self._reset_requested = False
        self.client = self._new_client()

    def _new_client(self) -> mqtt.Client:
        client = mqtt.Client(
            CallbackAPIVersion.VERSION2,
            client_id=self.settings.client_id,
            protocol=mqtt.MQTTv5,
            manual_ack=True,
            reconnect_on_failure=False,
        )
        client.username_pw_set(self.settings.username, self._password)
        client.connect_timeout = 5
        client.max_queued_messages_set(14)
        client.max_inflight_messages_set(14)
        if self.settings.tls:
            context = ssl.create_default_context(cafile=self.settings.ca_file)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            client.tls_set_context(context)
        client.on_connect = self._on_connect
        client.on_subscribe = self._on_subscribe
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_publish = self._on_publish
        return client

    def _fail(self, reason: str) -> None:
        self.fatal_reason = reason
        self.ready.clear()
        self.stop_requested.set()
        logger.error("Control worker stopped: %s", reason)

    @staticmethod
    def _bound(session: Session) -> None:
        session.connection().execute(text("SET LOCAL statement_timeout = '5000ms'"))

    def _on_connect(
        self,
        client: mqtt.Client,
        _data: Any,
        _flags: mqtt.ConnectFlags,
        reason: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        if client is not self.client:
            return
        self.ready.clear()
        if reason.is_failure:
            self._fail("broker_authentication_rejected")
            return
        result, _ = client.subscribe(
            [
                (topic, SubscribeOptions(qos=1, retainAsPublished=True))
                for topic in self.topics
            ]
        )
        if result != mqtt.MQTT_ERR_SUCCESS:
            self._fail("broker_subscription_failed")

    def _on_subscribe(
        self,
        client: mqtt.Client,
        _data: Any,
        _mid: int,
        reasons: list[ReasonCode],
        _properties: Properties | None,
    ) -> None:
        if client is not self.client:
            return
        if len(reasons) != len(self.topics) or any(r.value != 1 for r in reasons):
            self._fail("broker_subscription_rejected")
        else:
            self.ready.set()

    def _on_disconnect(
        self,
        client: mqtt.Client,
        _data: Any,
        _flags: mqtt.DisconnectFlags,
        _reason: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        if client is self.client:
            self.ready.clear()
            self._reset_requested = True

    def _finish(self, dispatch: Dispatch, outcome: Outcome) -> None:
        with Session(self.engine) as session:
            self._bound(session)
            finish_dispatch(
                session, dispatch=dispatch, outcome=outcome, now=datetime.now(UTC)
            )

    def _on_publish(
        self,
        client: mqtt.Client,
        _data: Any,
        mid: int,
        reason: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        if client is not self.client or self.stop_requested.is_set():
            return
        pending = self._pending.pop(mid, None)
        if pending is None:
            return
        try:
            # Authorization/topic/payload rejection is permanent; broker quota
            # or unspecified/server implementation failures allow bounded retry.
            outcome: Outcome = "accepted"
            if reason.is_failure:
                outcome = (
                    "permanent" if reason.value in {135, 144, 153} else "transient"
                )
            self._finish(pending[0], outcome)
        except Exception:
            self._fail("control_dispatch_storage_failed")

    def _on_message(
        self, client: mqtt.Client, _data: Any, message: mqtt.MQTTMessage
    ) -> None:
        if client is not self.client or self.stop_requested.is_set():
            return
        try:
            identity = self.topics.get(message.topic)
            if identity is None or message.qos != 1:
                raise ControlError("invalid_ack_transport")
            with Session(self.engine) as session:
                self._bound(session)
                receive_command_ack(
                    session,
                    authenticated_device_id=identity,
                    credential_enabled=True,
                    topic=message.topic,
                    payload=message.payload,
                    retained=message.retain,
                    received_at=datetime.now(UTC),
                )
            self.committed += 1
        except ControlError:
            # Permanent malformed/retained/conflicting evidence rejection only.
            self.rejections += 1
            logger.warning("Control ACK rejected")
        except Exception:
            self._fail("control_ack_storage_failed")
            return  # No PUBACK on an uncommitted write.
        if (
            message.qos > 0
            and client.ack(message.mid, message.qos) != mqtt.MQTT_ERR_SUCCESS
        ):
            self._fail("broker_ack_failed")

    def scan(self) -> None:
        """Expire independently of connectivity; one active command per device."""
        with Session(self.engine) as session:
            self._bound(session)
            rows = session.exec(
                select(DeviceCommand.command_id, DeviceCommand.expires_at)
                .where(
                    col(DeviceCommand.device_id).in_(self.settings.device_ids),
                    col(DeviceCommand.status).in_(ACTIVE),
                )
                .order_by(col(DeviceCommand.expires_at), col(DeviceCommand.command_id))
                .limit(14)
            ).all()
        for identity, expires_at in rows:
            if self.stop_requested.is_set():
                return
            with Session(self.engine) as session:
                self._bound(session)
                if datetime.now(UTC) >= expires_at:
                    expire_command(session, command_id=identity, now=datetime.now(UTC))
                    continue
                if (
                    not self.ready.is_set()
                    or len(self._pending) >= 14
                    or any(
                        item[0].command_id == identity
                        for item in self._pending.values()
                    )
                ):
                    continue
                dispatch = claim_dispatch(
                    session,
                    command_id=identity,
                    now=datetime.now(UTC),
                    warehouse_exists=self.warehouse_lookup_factory(session),
                )
            if dispatch is None:
                continue
            # Claim committed before any network side effect. loop() is owned by
            # this thread; QoS1 PUBACK callback cannot race this mid registration.
            result = self.client.publish(
                dispatch.topic, dispatch.payload, qos=1, retain=False
            )
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                self._finish(dispatch, "transient")
                self._reset_requested = True
                return
            self._pending[result.mid] = (dispatch, time.monotonic())

    def _retire_pending(self) -> None:
        # A delivery may already have executed. Mark only transport uncertainty;
        # service does not downgrade a device ACK, device ID dedup remains final.
        for dispatch, _ in list(self._pending.values()):
            self._finish(dispatch, "transient")
        self._pending.clear()

    def run(self, *, max_seconds: float | None = None) -> None:
        if max_seconds is not None and (
            not math.isfinite(max_seconds) or max_seconds <= 0
        ):
            raise ValueError("max_seconds must be finite and positive")
        start = time.monotonic()
        next_scan, retry_at, retry_delay = start, start, 1.0
        connected_socket = False
        connected_at = start
        try:
            while not self.stop_requested.is_set():
                now = time.monotonic()
                if max_seconds is not None and now - start >= max_seconds:
                    break
                if (
                    self._reset_requested
                    or any(
                        now - sent >= self.settings.publish_timeout_seconds
                        for _, sent in self._pending.values()
                    )
                    or (
                        connected_socket
                        and not self.ready.is_set()
                        and now - connected_at >= 5
                    )
                ):
                    self.ready.clear()
                    self.client.disconnect()
                    self._retire_pending()
                    self.client = self._new_client()
                    self._reset_requested = False
                    connected_socket = False
                    retry_at = now + retry_delay
                    retry_delay = min(30, retry_delay * 2)
                if not connected_socket and now >= retry_at:
                    try:
                        connected_socket = (
                            self.client.connect(
                                self.settings.host,
                                self.settings.port,
                                keepalive=30,
                                clean_start=True,
                            )
                            == mqtt.MQTT_ERR_SUCCESS
                        )
                    except OSError:
                        connected_socket = False
                    connected_at = time.monotonic()
                    if not connected_socket:
                        retry_at = connected_at + retry_delay
                        retry_delay = min(30, retry_delay * 2)
                if connected_socket:
                    if self.client.loop(timeout=0.1) != mqtt.MQTT_ERR_SUCCESS:
                        self._reset_requested = True
                        self.ready.clear()
                    elif self.ready.is_set():
                        retry_delay = 1
                else:
                    self.stop_requested.wait(0.1)
                if time.monotonic() >= next_scan:
                    self.scan()
                    next_scan = time.monotonic() + self.settings.scan_period_seconds
        except Exception:
            self._fail("control_worker_operation_failed")
        finally:
            self.ready.clear()
            self.client.disconnect()
            try:
                self._retire_pending()
            except Exception:
                self._fail("control_dispatch_storage_failed")
            self.engine.dispose()
        if self.fatal_reason:
            raise RuntimeError(self.fatal_reason)
