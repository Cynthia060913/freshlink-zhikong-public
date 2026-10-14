"""Trusted-broker heartbeat consumer. No C/B calls or public write endpoint.

Topic identity is trusted ONLY after the operator has verified broker ACLs:
each device account may publish its own exact heartbeat topic; service accounts
must be read-only on these topics. A config flag cannot verify remote ACLs.
One owner processes callbacks and DB work; no unbounded in-memory queue.
"""

import argparse
import logging
import math
import os
import signal
import ssl
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode
from paho.mqtt.subscribeoptions import SubscribeOptions
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.core.db import engine
from app.modules.iot.heartbeat import HeartbeatPolicy
from app.modules.iot.heartbeat_storage import (
    persist_registered_heartbeat,
    scan_registered_device,
)
from app.modules.iot.ingress import MAX_PAYLOAD_BYTES, IngressRejected

logger = logging.getLogger(__name__)


class HeartbeatWorkerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=8883, ge=1, le=65535)
    tls: bool = True
    ca_file: Path | None = None
    isolated_test_plaintext: bool = False
    username: str = Field(min_length=1, max_length=128)
    client_id: str = Field(min_length=1, max_length=128)
    acl_confirmed: Literal[True]
    device_ids: list[UUID] = Field(min_length=1, max_length=14)
    scan_period_seconds: float = Field(default=5, ge=0.1, le=10)

    @model_validator(mode="after")
    def secure_scope(self) -> HeartbeatWorkerSettings:
        if not self.tls and (
            not self.isolated_test_plaintext
            or self.host not in {"localhost", "127.0.0.1", "broker"}
        ):
            raise ValueError("plaintext requires an isolated local test")
        if len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("duplicate device scope")
        return self


class HeartbeatWorker:
    def __init__(
        self,
        settings: HeartbeatWorkerSettings,
        *,
        password: str,
        policy: HeartbeatPolicy = HeartbeatPolicy(),
    ) -> None:
        if not password:
            raise ValueError("MQTT password required from account owner")
        self.settings = settings
        self.policy = policy
        # Separate worker pool; never change the application's engine/settings.
        self.engine = create_engine(
            engine.url,
            pool_pre_ping=True,
            pool_timeout=5,
            connect_args={"connect_timeout": 5},
        )
        self.topics = {
            f"freshlink/v1/devices/{identity}/heartbeat": identity
            for identity in settings.device_ids
        }
        self.ready = threading.Event()
        self.stop_requested = threading.Event()
        self.fatal_reason: str | None = None
        self.rejections = 0
        self.committed = 0
        self.client = mqtt.Client(
            CallbackAPIVersion.VERSION2,
            client_id=settings.client_id,
            protocol=mqtt.MQTTv5,
            manual_ack=True,
        )
        self.client.username_pw_set(settings.username, password)
        self.client.connect_timeout = 5
        if settings.tls:
            context = ssl.create_default_context(cafile=settings.ca_file)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.client.tls_set_context(context)
        self.client.on_connect = self._on_connect
        self.client.on_subscribe = self._on_subscribe
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _fail(self, reason: str) -> None:
        self.fatal_reason = reason
        self.ready.clear()
        self.stop_requested.set()
        # Never include payload, broker credentials, config or DB exception text.
        logger.error("Heartbeat worker stopped: %s", reason)

    def _on_connect(
        self,
        client: mqtt.Client,
        _data: Any,
        _flags: mqtt.ConnectFlags,
        reason: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        self.ready.clear()
        if reason.is_failure:
            self._fail("broker_authentication_rejected")
            return
        subscriptions = [
            (topic, SubscribeOptions(qos=1, retainAsPublished=True))
            for topic in self.topics
        ]
        result, _ = client.subscribe(subscriptions)
        if result != mqtt.MQTT_ERR_SUCCESS:
            self._fail("broker_subscription_failed")

    def _on_subscribe(
        self,
        _client: mqtt.Client,
        _data: Any,
        _mid: int,
        reasons: list[ReasonCode],
        _properties: Properties | None,
    ) -> None:
        if len(reasons) != len(self.topics) or any(r.is_failure for r in reasons):
            self._fail("broker_subscription_rejected")
        else:
            self.ready.set()

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _data: Any,
        _flags: mqtt.DisconnectFlags,
        _reason: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        self.ready.clear()

    def _on_message(
        self, client: mqtt.Client, _data: Any, message: mqtt.MQTTMessage
    ) -> None:
        if self.stop_requested.is_set():
            return
        received_at = datetime.now(UTC)  # Server receipt, not device claims.
        try:
            identity = self.topics.get(message.topic)
            if identity is None:
                raise IngressRejected("topic_outside_verified_scope", message.payload)
            if len(message.payload) > MAX_PAYLOAD_BYTES:
                raise IngressRejected("payload_too_large", message.payload)
            with Session(self.engine) as session:
                # Bound connection acquisition and lock/query time separately.
                session.connection().execute(
                    text("SET LOCAL statement_timeout = '5000ms'")
                )
                persist_registered_heartbeat(
                    session,
                    authenticated_device_id=identity,
                    credential_enabled=True,
                    topic=message.topic,
                    payload=message.payload,
                    received_at=received_at,
                    retained=message.retain,
                    policy=self.policy,
                )
            self.committed += 1
        except IngressRejected as rejection:
            self.rejections += 1
            logger.warning("Heartbeat rejected: %s", rejection.reason_code)
        except Exception:
            # No QoS acknowledgement on DB failure. QoS0 is best-effort; the
            # device's next fresh heartbeat, not a stale replay, restores status.
            self._fail("heartbeat_storage_failed")
            return
        if (
            message.qos > 0
            and client.ack(message.mid, message.qos) != mqtt.MQTT_ERR_SUCCESS
        ):
            self._fail("broker_ack_failed")

    def scan(self) -> None:
        for identity in self.settings.device_ids:
            if self.stop_requested.is_set():
                return
            with Session(self.engine) as session:
                session.connection().execute(
                    text("SET LOCAL statement_timeout = '5000ms'")
                )
                scan_registered_device(
                    session,
                    device_id=identity,
                    now=datetime.now(UTC),
                    policy=self.policy,
                )

    def run(self, *, max_seconds: float | None = None) -> None:
        if max_seconds is not None and (
            not math.isfinite(max_seconds) or max_seconds <= 0
        ):
            raise ValueError("max_seconds must be finite and positive")
        start = time.monotonic()
        next_scan = start
        retry_at = start
        retry_delay = 1.0
        connected_socket = False
        try:
            while not self.stop_requested.is_set():
                now = time.monotonic()
                if max_seconds is not None and now - start >= max_seconds:
                    break
                if not connected_socket and now >= retry_at:
                    try:
                        result = self.client.connect(
                            self.settings.host, self.settings.port, keepalive=30
                        )
                        connected_socket = result == mqtt.MQTT_ERR_SUCCESS
                    except OSError:
                        connected_socket = False
                    if not connected_socket:
                        retry_at = now + retry_delay
                        retry_delay = min(30, retry_delay * 2)
                if connected_socket:
                    result = self.client.loop(timeout=0.1)
                    if result != mqtt.MQTT_ERR_SUCCESS:
                        connected_socket = False
                        self.ready.clear()
                        retry_at = now + retry_delay
                        retry_delay = min(30, retry_delay * 2)
                    elif self.ready.is_set():
                        retry_delay = 1
                else:
                    self.stop_requested.wait(0.1)
                if now >= next_scan:
                    try:
                        self.scan()
                    except Exception:
                        self._fail("heartbeat_scan_failed")
                    next_scan = time.monotonic() + self.settings.scan_period_seconds
        finally:
            self.ready.clear()
            self.client.disconnect()
            self.engine.dispose()
        if self.fatal_reason:
            raise RuntimeError(self.fatal_reason)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float)
    args = parser.parse_args()
    if args.max_seconds is not None and args.max_seconds <= 0:
        raise ValueError("max-seconds must be positive")
    if args.config.stat().st_size > 65536:
        raise ValueError("worker config too large")
    settings = HeartbeatWorkerSettings.model_validate_json(args.config.read_bytes())
    worker = HeartbeatWorker(
        settings, password=os.environ.get("FRESHLINK_HEARTBEAT_MQTT_PASSWORD", "")
    )
    signal.signal(signal.SIGINT, lambda _s, _f: worker.stop_requested.set())
    signal.signal(signal.SIGTERM, lambda _s, _f: worker.stop_requested.set())
    worker.run(max_seconds=args.max_seconds)


if __name__ == "__main__":
    main()
