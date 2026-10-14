"""A-owned persistent commands/attempts/receipts; no broker credentials."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)
from sqlalchemy import CheckConstraint, Column, DateTime, Index, text
from sqlmodel import Field, SQLModel

CommandStatus = Literal["queued", "sent", "acknowledged", "failed", "timeout"]


class CommandCreate(SQLModel):
    command_id: uuid.UUID
    action: Literal["set_cooling"] = "set_cooling"
    enabled: StrictBool
    lifetime_seconds: StrictInt = Field(default=60, ge=1, le=300)
    event_id: uuid.UUID | None = None

    @field_validator("event_id")
    @classmethod
    def defer_unverified_event(cls, value: uuid.UUID | None) -> None:
        if value is not None:
            raise ValueError("Alert reference requires the B integration validator")
        return None


class DeviceCommand(SQLModel, table=True):
    __tablename__ = "iot_command"
    __table_args__ = (
        Index(
            "uq_iot_command_active_device",
            "device_id",
            unique=True,
            postgresql_where=text("status IN ('queued','sent')"),
        ),
        CheckConstraint(
            "status IN ('queued','sent','acknowledged','failed','timeout')",
            name="ck_iot_command_status",
        ),
        CheckConstraint(
            "expires_at > issued_at AND lifetime_seconds BETWEEN 1 AND 300",
            name="ck_iot_command_lifetime",
        ),
        CheckConstraint(
            "attempt_count BETWEEN 0 AND 10", name="ck_iot_command_attempts"
        ),
        CheckConstraint(
            "status NOT IN ('acknowledged','failed','timeout') OR terminal_at IS NOT NULL",
            name="ck_iot_command_terminal",
        ),
        CheckConstraint(
            "status <> 'acknowledged' OR (ack_received_at IS NOT NULL AND ack_fingerprint IS NOT NULL)",
            name="ck_iot_command_ack",
        ),
        CheckConstraint(
            "status NOT IN ('failed','timeout') OR failure_reason IS NOT NULL",
            name="ck_iot_command_failure",
        ),
    )
    command_id: uuid.UUID = Field(primary_key=True)
    device_id: uuid.UUID = Field(
        foreign_key="iot_device.id", ondelete="RESTRICT", index=True
    )
    warehouse_id: uuid.UUID
    actor_id: uuid.UUID  # Historical UUID; deleting an account cannot erase commands.
    action: str = Field(default="set_cooling", max_length=32)
    enabled: bool
    lifetime_seconds: int
    request_fingerprint: str = Field(max_length=64)
    payload_text: str = Field(max_length=4096)
    issued_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    status: str = Field(default="queued", max_length=16)
    attempt_count: int = 0
    lease_id: uuid.UUID | None = None
    lease_until: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    next_retry_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    transport_accepted_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    ack_fingerprint: str | None = Field(default=None, max_length=64)
    ack_received_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    terminal_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    failure_reason: str | None = Field(default=None, max_length=64)

    @property
    def parameters(self) -> dict[Literal["enabled"], bool]:
        return {"enabled": self.enabled}


class CommandAttempt(SQLModel, table=True):
    __tablename__ = "iot_command_attempt"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('pending','accepted','transient','permanent')",
            name="ck_iot_command_attempt_outcome",
        ),
    )
    attempt_id: uuid.UUID = Field(primary_key=True)
    command_id: uuid.UUID = Field(
        foreign_key="iot_command.command_id", ondelete="CASCADE", index=True
    )
    started_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    completed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    outcome: str = Field(default="pending", max_length=16)


class CommandReceipt(SQLModel, table=True):
    __tablename__ = "iot_command_receipt"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('accepted','late','conflict')",
            name="ck_iot_command_receipt_outcome",
        ),
    )
    command_id: uuid.UUID = Field(
        primary_key=True, foreign_key="iot_command.command_id", ondelete="CASCADE"
    )
    fingerprint: str = Field(primary_key=True, max_length=64)
    received_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    executed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    result: str = Field(max_length=16)
    cooling_enabled: bool
    reason: str | None = Field(default=None, max_length=128)
    outcome: str = Field(max_length=16)


class CommandPublic(SQLModel):
    command_id: uuid.UUID
    device_id: uuid.UUID
    warehouse_id: uuid.UUID
    action: Literal["set_cooling"]
    enabled: bool
    issued_at: datetime
    expires_at: datetime
    status: CommandStatus
    attempt_count: int
    transport_accepted_at: datetime | None
    ack_received_at: datetime | None
    terminal_at: datetime | None
    failure_reason: str | None
    event_id: None = None
    parameters: dict[Literal["enabled"], bool]


class CommandsPublic(SQLModel):
    data: list[CommandPublic]
    count: int


class ReceiptPublic(SQLModel):
    received_at: datetime
    executed_at: datetime | None
    result: Literal["executed", "failed"]
    cooling_enabled: bool
    reason: str | None
    outcome: Literal["accepted", "late", "conflict"]


class CommandDetail(CommandPublic):
    receipts: list[ReceiptPublic]


class AckState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cooling_enabled: StrictBool


class ExecutionAck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    command_id: uuid.UUID
    device_id: uuid.UUID
    executed_at: AwareDatetime | None
    result: Literal["executed", "failed"]
    state: AckState
    reason: str | None = Field(max_length=128)

    @model_validator(mode="after")
    def execution_shape(self) -> ExecutionAck:
        if self.result == "executed" and (
            self.executed_at is None or self.reason is not None
        ):
            raise ValueError(
                "Executed ACK needs an execution time and no failure reason"
            )
        if self.result == "failed" and (
            self.executed_at is not None
            or self.reason not in {"expired", "simulated_switch_failure"}
        ):
            raise ValueError(
                "Failed ACK requires a supported reason and no execution claim"
            )
        return self
