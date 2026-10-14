import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import field_validator
from sqlalchemy import Column, DateTime
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


class DevicePublic(SQLModel):
    id: uuid.UUID
    code: str
    name: str
    kind: DeviceKind
    warehouse_id: uuid.UUID | None
    is_simulated: bool
    created_at: datetime
    last_seen_at: datetime | None
    # Registration cannot establish connectivity; heartbeat is not implemented.
    connectivity: Literal["unknown"] = "unknown"


class DevicesPublic(SQLModel):
    data: list[DevicePublic]
    count: int
