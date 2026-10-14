"""Warehouse SQLModel + create / public DTOs.

A warehouse is the physical cold room that holds one or more batches.
Code is unique per warehouse (case-normalized) so devices and batches
can refer to it without exposing UUIDs in URLs.

This module is intentionally framework-agnostic: it imports neither
FastAPI nor the SMTP transport. It must be importable from alembic
env.py and from unit tests.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from pydantic import field_validator
from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

# Case-normalized code: 1-64 ASCII letters, digits, underscore, hyphen.
_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class WarehouseCreate(SQLModel):
    """Payload accepted by ``POST /warehouses``."""

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    location: str | None = Field(default=None, max_length=200)

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Warehouse code must be text")
        normalized = value.strip().lower()
        if not _CODE_RE.fullmatch(normalized):
            raise ValueError(
                "Warehouse code must be 1-64 ASCII letters, digits, "
                "underscores or hyphens, starting with letter or digit"
            )
        return normalized

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Warehouse name must be text")
        stripped = value.strip()
        if not stripped:
            raise ValueError("Warehouse name cannot be blank")
        return stripped

    @field_validator("location", mode="before")
    @classmethod
    def normalize_location(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Warehouse location must be text")
        stripped = value.strip()
        return stripped or None


class Warehouse(SQLModel, table=True):
    """Persistent warehouse row.

    Lifecycle is append-on-create + read/update via service layer;
    delete is not exposed in v1 because batches point at warehouses
    with FK references.
    """

    __tablename__ = "alerts_warehouse"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    code: str = Field(unique=True, index=True, max_length=64)
    name: str = Field(max_length=120)
    location: str | None = Field(default=None, max_length=200)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class WarehousePublic(SQLModel):
    """Read-only shape returned by the warehouse API."""

    id: uuid.UUID
    code: str
    name: str
    location: str | None
    created_at: datetime
