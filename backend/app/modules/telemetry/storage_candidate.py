"""C-owned transactional observation candidate; no migration or public route.

The tables are deliberately in separate metadata. Production must not create
them with create_all: a reviewed Alembic revision must follow the shared head.
Tests may create these tables in an isolated database to exercise transactions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.modules.telemetry.envelope_preview import ReceptionLike, prepare_receptions
from app.modules.telemetry.quality import _fingerprint

metadata = MetaData()

observations = Table(
    "telemetry_observation_candidate",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("device_id", Uuid(as_uuid=True), nullable=False),
    Column("message_id", Uuid(as_uuid=True), nullable=False),
    Column("warehouse_id", Uuid(as_uuid=True), nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("raw_text", String, nullable=False),
    Column("raw_payload", JSON, nullable=False),
    Column("sampled_at_utc", DateTime(timezone=True), nullable=True),
    Column("first_received_at_utc", DateTime(timezone=True), nullable=False),
    Column("normalized", JSON, nullable=False),
    Column("quality", JSON, nullable=False),
    Column("risk", JSON, nullable=False),
    UniqueConstraint("device_id", "message_id", name="uq_c_candidate_device_message"),
)

quality_tags = Table(
    "telemetry_quality_tag_candidate",
    metadata,
    Column(
        "observation_id",
        Uuid(as_uuid=True),
        ForeignKey(observations.c.id),
        primary_key=True,
    ),
    Column("tag", String(128), primary_key=True),
)

Index(
    "ix_c_candidate_quality_tag",
    quality_tags.c.tag,
    quality_tags.c.observation_id,
)

Index(
    "ix_c_candidate_history",
    observations.c.warehouse_id,
    observations.c.sampled_at_utc,
    observations.c.id,
)

receptions = Table(
    "telemetry_reception_candidate",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("observation_id", Uuid(as_uuid=True), ForeignKey(observations.c.id)),
    Column("device_id", Uuid(as_uuid=True), nullable=False),
    Column("warehouse_id", Uuid(as_uuid=True), nullable=False),
    Column("received_at_utc", String(35), nullable=False),
    Column("raw_text", String, nullable=False),
    Column("receipt_sha256", String(64), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("reason", String(80), nullable=False),
)

reception_reuse_conflicts = Table(
    "telemetry_reception_reuse_candidate",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column(
        "claimed_reception_id",
        Uuid(as_uuid=True),
        ForeignKey(receptions.c.id),
        nullable=False,
    ),
    Column("device_id", Uuid(as_uuid=True), nullable=False),
    Column("warehouse_id", Uuid(as_uuid=True), nullable=False),
    Column("received_at_utc", String(35), nullable=False),
    Column("raw_text", String, nullable=False),
    Column("receipt_sha256", String(64), nullable=False),
    UniqueConstraint(
        "claimed_reception_id",
        "receipt_sha256",
        name="uq_c_candidate_claimed_receipt_digest",
    ),
)

evaluation_jobs = Table(
    "telemetry_evaluation_candidate",
    metadata,
    Column(
        "observation_id",
        Uuid(as_uuid=True),
        ForeignKey(observations.c.id),
        primary_key=True,
    ),
    Column("state", String(24), nullable=False),
)

IngestStatus = Literal[
    "accepted_new", "accepted_duplicate", "conflict", "invalid", "retryable_failure"
]


@dataclass(frozen=True)
class IngestResult:
    status: IngestStatus
    reception_id: UUID
    observation_id: UUID | None = None
    reason: str = ""


def _receipt_digest(view: dict[str, Any]) -> str:
    trusted = view["trusted"]
    parts = (
        view["raw_text"],
        trusted["authenticated_device_id"],
        trusted["warehouse_id"],
        trusted["received_at"],
    )
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _content_digest(view: dict[str, Any]) -> str:
    canonical = _fingerprint(view["processing_view"], view["normalized"])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _invalid_reason(view: dict[str, Any]) -> str | None:
    normalized = view["normalized"]
    if normalized.get("schema_version") != "1.0":
        return "invalid_schema_version"
    if "message_id" not in normalized:
        return "invalid_message_id"
    if "too_many_metrics" in view["quality"]:
        return "too_many_metrics"
    return None


def _write_receipt(
    connection: Any,
    view: dict[str, Any],
    *,
    observation_id: UUID | None,
    outcome: IngestStatus,
    reason: str,
) -> None:
    trusted = view["trusted"]
    connection.execute(
        receptions.insert().values(
            id=UUID(trusted["reception_id"]),
            observation_id=observation_id,
            device_id=UUID(trusted["authenticated_device_id"]),
            warehouse_id=UUID(trusted["warehouse_id"]),
            received_at_utc=trusted["received_at"],
            raw_text=view["raw_text"],
            receipt_sha256=_receipt_digest(view),
            outcome=outcome,
            reason=reason,
        )
    )


def _ingest_once(engine: Engine, view: dict[str, Any]) -> IngestResult:
    trusted = view["trusted"]
    reception_id = UUID(trusted["reception_id"])
    with engine.begin() as connection:
        previous_receipt = (
            connection.execute(
                select(receptions).where(receptions.c.id == reception_id)
            )
            .mappings()
            .first()
        )
        if previous_receipt is not None:
            digest = _receipt_digest(view)
            if previous_receipt["receipt_sha256"] != digest:
                # Preserve the rejected exact raw separately. A repeat of the
                # same tampered attempt is idempotent by claimed ID + digest.
                existing_attempt = connection.execute(
                    select(reception_reuse_conflicts.c.id).where(
                        reception_reuse_conflicts.c.claimed_reception_id
                        == reception_id,
                        reception_reuse_conflicts.c.receipt_sha256 == digest,
                    )
                ).scalar_one_or_none()
                if existing_attempt is None:
                    connection.execute(
                        reception_reuse_conflicts.insert().values(
                            id=uuid4(),
                            claimed_reception_id=reception_id,
                            device_id=UUID(trusted["authenticated_device_id"]),
                            warehouse_id=UUID(trusted["warehouse_id"]),
                            received_at_utc=trusted["received_at"],
                            raw_text=view["raw_text"],
                            receipt_sha256=digest,
                        )
                    )
                return IngestResult(
                    "conflict", reception_id, reason="reception_id_reuse"
                )
            return IngestResult(
                previous_receipt["outcome"],
                reception_id,
                previous_receipt["observation_id"],
                previous_receipt["reason"],
            )

        invalid_reason = _invalid_reason(view)
        if invalid_reason is not None:
            _write_receipt(
                connection,
                view,
                observation_id=None,
                outcome="invalid",
                reason=invalid_reason,
            )
            return IngestResult("invalid", reception_id, reason=invalid_reason)

        normalized = view["normalized"]
        device_id = UUID(trusted["authenticated_device_id"])
        message_id = UUID(normalized["message_id"])
        previous = (
            connection.execute(
                select(observations).where(
                    observations.c.device_id == device_id,
                    observations.c.message_id == message_id,
                )
            )
            .mappings()
            .first()
        )
        if previous is not None:
            same = previous["content_sha256"] == _content_digest(view)
            status: IngestStatus = "accepted_duplicate" if same else "conflict"
            reason = "duplicate_message" if same else "conflicting_message"
            _write_receipt(
                connection,
                view,
                observation_id=previous["id"],
                outcome=status,
                reason=reason,
            )
            return IngestResult(status, reception_id, previous["id"], reason)

        observation_id = uuid4()
        connection.execute(
            observations.insert().values(
                id=observation_id,
                device_id=device_id,
                message_id=message_id,
                warehouse_id=UUID(trusted["warehouse_id"]),
                content_sha256=_content_digest(view),
                raw_text=view["raw_text"],
                raw_payload=view["raw_payload"],
                sampled_at_utc=(
                    datetime.fromisoformat(
                        normalized["sampled_at"].replace("Z", "+00:00")
                    )
                    if "sampled_at" in normalized
                    else None
                ),
                first_received_at_utc=datetime.fromisoformat(
                    trusted["received_at"].replace("Z", "+00:00")
                ),
                normalized=normalized,
                quality=view["quality"],
                risk=view["risk"],
            )
        )
        if view["quality"]:
            connection.execute(
                quality_tags.insert(),
                [
                    {"observation_id": observation_id, "tag": tag}
                    for tag in sorted(set(view["quality"]))
                ],
            )
        evaluation_state = (
            "pending"
            if "sampled_at" in normalized
            and "temperature" in normalized.get("metrics", {})
            and view["risk"]["reason"] == "no_threshold_rule"
            else "held_unknown"
        )
        connection.execute(
            evaluation_jobs.insert().values(
                observation_id=observation_id, state=evaluation_state
            )
        )
        _write_receipt(
            connection,
            view,
            observation_id=observation_id,
            outcome="accepted_new",
            reason="",
        )
        return IngestResult("accepted_new", reception_id, observation_id)


def ingest_observation(engine: Engine, envelope: ReceptionLike) -> IngestResult:
    """Commit a trusted A envelope; return success only after the transaction.

    A boundary errors remain explicit ValueErrors. Storage errors are retryable.
    A unique-key race is retried in a new transaction and resolved against the
    committed first writer; neither raw evidence nor business IDs are changed.
    """
    view = prepare_receptions([envelope])[0]
    reception_id = UUID(view["trusted"]["reception_id"])
    for _ in range(2):
        try:
            return _ingest_once(engine, view)
        except IntegrityError:
            # Unique device/message or reception race. The failed transaction
            # is rolled back by engine.begin before the next lookup.
            continue
        except SQLAlchemyError:
            return IngestResult(
                "retryable_failure", reception_id, reason="storage_unavailable"
            )
    return IngestResult("retryable_failure", reception_id, reason="storage_unavailable")
