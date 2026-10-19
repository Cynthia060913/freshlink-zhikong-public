import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import field_validator
from sqlalchemy import CheckConstraint, Column, DateTime, String
from sqlmodel import Field, SQLModel

DeviceKind = Literal["temperature_sensor", "cooling_actuator"]


class DeviceCreate(SQLModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    kind: DeviceKind
    warehouse_id: uuid.UUID | None = None

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Device code must be text")
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", normalized):
            raise ValueError("Use 1-64 ASCII letters, digits, underscores or hyphens")
        return normalized

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Device name cannot be blank")
        return value.strip()

    @field_validator("warehouse_id")
    @classmethod
    def require_unbound(cls, value: uuid.UUID | None) -> None:
        # Do not accept unverified warehouse IDs before B's module is connected.
        if value is not None:
            raise ValueError("Warehouse binding is not available yet")
        return None


class Device(SQLModel, table=True):
    __tablename__ = "iot_device"
    __table_args__ = (
        CheckConstraint(
            "connectivity IN ('unknown', 'online', 'offline')",
            name="ck_iot_device_connectivity",
        ),
        CheckConstraint(
            "connectivity = 'unknown' OR (last_seen_at IS NOT NULL AND last_sent_at IS NOT NULL AND last_heartbeat_message_id IS NOT NULL)",
            name="ck_iot_device_heartbeat_history",
        ),
        CheckConstraint(
            "(connectivity = 'offline' AND offline_source_event_id IS NOT NULL) OR (connectivity <> 'offline' AND offline_source_event_id IS NULL)",
            name="ck_iot_device_offline_reference",
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    code: str = Field(unique=True, index=True, max_length=64)
    name: str = Field(max_length=120)
    kind: str = Field(max_length=32)
    warehouse_id: uuid.UUID | None = None
    is_simulated: bool = True
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    last_seen_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    connectivity: str = Field(
        default="unknown",
        sa_column=Column(String(16), nullable=False, server_default="unknown"),
    )
    last_sent_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    last_heartbeat_message_id: uuid.UUID | None = None
    offline_source_event_id: uuid.UUID | None = None


class DevicePublic(SQLModel):
    id: uuid.UUID
    code: str
    name: str
    kind: DeviceKind
    warehouse_id: uuid.UUID | None
    is_simulated: bool
    created_at: datetime
    last_seen_at: datetime | None
    # Registration starts unknown. Only committed, verified heartbeats can
    # establish online; the persistent scanner can establish offline.
    connectivity: Literal["unknown", "online", "offline"] = "unknown"


class DevicesPublic(SQLModel):
    data: list[DevicePublic]
    count: int


class HeartbeatMessage(SQLModel, table=True):
    __tablename__ = "iot_heartbeat_message"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('accepted', 'out_of_order')", name="ck_iot_heartbeat_outcome"
        ),
    )
    device_id: uuid.UUID = Field(
        primary_key=True, foreign_key="iot_device.id", ondelete="CASCADE"
    )
    message_id: uuid.UUID = Field(primary_key=True)
    fingerprint: str = Field(max_length=64)
    outcome: str = Field(max_length=32)
    received_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class HeartbeatConflict(SQLModel, table=True):
    __tablename__ = "iot_heartbeat_conflict"
    device_id: uuid.UUID = Field(
        primary_key=True, foreign_key="iot_device.id", ondelete="CASCADE"
    )
    message_id: uuid.UUID = Field(primary_key=True)
    fingerprint: str = Field(primary_key=True, max_length=64)
    received_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class DeviceConnectivityEvent(SQLModel, table=True):
    """Durable event/outbox body; B delivery is a separate later operation."""

    __tablename__ = "iot_connectivity_event"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('offline', 'recovered')", name="ck_iot_connectivity_event_kind"
        ),
        CheckConstraint(
            "delivery_state IN ('pending', 'held_unbound', 'delivered', 'failed')",
            name="ck_iot_connectivity_event_delivery",
        ),
    )
    source_event_id: uuid.UUID = Field(primary_key=True)
    device_id: uuid.UUID = Field(
        foreign_key="iot_device.id", ondelete="CASCADE", index=True
    )
    warehouse_id: uuid.UUID | None = None
    occurred_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    kind: str = Field(max_length=16)
    reason: str = Field(max_length=64)
    related_source_event_id: uuid.UUID | None = None
    delivery_state: str = Field(default="pending", max_length=16)


class DeviceConnectivityEventPublic(SQLModel):
    source_event_id: uuid.UUID
    device_id: uuid.UUID
    warehouse_id: uuid.UUID | None
    occurred_at: datetime
    kind: Literal["offline", "recovered"]
    reason: str
    related_source_event_id: uuid.UUID | None
    delivery_state: Literal["pending", "held_unbound", "delivered", "failed"]


class DeviceConnectivityEventsPublic(SQLModel):
    data: list[DeviceConnectivityEventPublic]
    count: int
