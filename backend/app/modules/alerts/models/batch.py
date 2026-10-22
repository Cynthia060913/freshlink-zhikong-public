"""Batch SQLModel + create / public DTOs.

A :class:`Batch` is a unit of perishable goods stored in a warehouse
for a bounded time window. ``(warehouse_id, code)`` is unique so two
warehouses can both have a batch called ``b-001`` without collision.
``ended_at`` is nullable — batches can be open / ongoing.

The model is intentionally framework-agnostic; importable from alembic
env.py and unit tests.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from pydantic import ValidationInfo, field_validator
from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel

_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class BatchCreate(SQLModel):
    """Payload accepted by ``POST /warehouses/{wid}/batches``."""

    code: str = Field(min_length=1, max_length=64)
    product: str = Field(min_length=1, max_length=120)
    started_at: datetime | None = None
    ended_at: datetime | None = None

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Batch code must be text")
        normalized = value.strip().lower()
        if not _CODE_RE.fullmatch(normalized):
            raise ValueError(
                "Batch code must be 1-64 ASCII letters, digits, "
                "underscores or hyphens, starting with letter or digit"
            )
        return normalized

    @field_validator("product", mode="before")
    @classmethod
    def normalize_product(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Batch product must be text")
        stripped = value.strip()
        if not stripped:
            raise ValueError("Batch product cannot be blank")
        return stripped

    @field_validator("ended_at")
    @classmethod
    def end_after_start(
        cls, value: datetime | None, info: ValidationInfo
    ) -> datetime | None:
        started = info.data.get("started_at")
        if value is not None and started is not None and value < started:
            raise ValueError("ended_at must be at or after started_at")
        return value


class Batch(SQLModel, table=True):
    """Persistent batch row.

    Lifecycle: append-only at the API; closed by setting ``ended_at``;
    deleted only via alembic downgrade (no service-level delete in v1).
    """

    __tablename__ = "alerts_batch"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "code", name="uq_alerts_batch_warehouse_code"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    code: str = Field(max_length=64, index=True)
    warehouse_id: uuid.UUID = Field(foreign_key="alerts_warehouse.id", index=True)
    product: str = Field(max_length=120)
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    ended_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class BatchPublic(SQLModel):
    """Read-only shape returned by the batch API."""

    id: uuid.UUID
    code: str
    warehouse_id: uuid.UUID
    product: str
    started_at: datetime
    ended_at: datetime | None
    created_at: datetime
