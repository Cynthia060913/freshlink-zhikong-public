"""Persist held correction records for late rejected reception evidence.

This is an internal C candidate only. Its JSON is not the F03 wire DTO: B has
not accepted ``kind=data_quality`` or the linked-correction fields yet. Rows
are deliberately stored as ``held_contract`` in the existing candidate
outbox, which is not eligible for claims or delivery.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.modules.telemetry.outbox_candidate import outbox, outbox_conflicts
from app.modules.telemetry.storage_candidate import observations

_CORRECTION_VERSION = "c-data-quality-correction/v0"
_LATE_EVIDENCE_NAMESPACE = UUID("3d2327ba-30ed-4e75-b5e8-04d74f8ba65a")

CorrectionStatus = Literal["staged", "duplicate", "conflict"]


@dataclass(frozen=True)
class CorrectionStageCandidate:
    source_event_id: UUID
    related_source_event_id: UUID
    status: CorrectionStatus
    evidence_key: str


def _utc(value: object, *, stored: bool = False) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("correction evidence time must be ISO-8601") from exc
    if not isinstance(value, datetime):
        raise ValueError("correction evidence time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        if not stored:
            raise ValueError("correction evidence time must carry a UTC offset")
        # SQLite strips DateTime(timezone=True); C writes these values in UTC.
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _correction_source_id(original_id: UUID, evidence_key: str) -> UUID:
    """Return the same correction UUID for every replay of this evidence."""
    return uuid5(
        original_id,
        f"{_CORRECTION_VERSION}:{evidence_key}",
    )


def _canonical_body(body: dict[str, object]) -> tuple[dict[str, object], str]:
    encoded = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    frozen = json.loads(encoded)
    return frozen, hashlib.sha256(encoded).hexdigest()


def _insert_held_correction(
    connection: Connection,
    *,
    source_event_id: UUID,
    related_source_event_id: UUID,
    observation_id: UUID,
    body: dict[str, object],
    body_sha256: str,
    received_at: datetime,
) -> CorrectionStatus:
    previous = (
        connection.execute(
            select(
                outbox.c.related_source_event_id,
                outbox.c.observation_id,
                outbox.c.body_sha256,
            ).where(outbox.c.source_event_id == source_event_id)
        )
        .mappings()
        .one_or_none()
    )
    if previous is not None:
        if (
            previous["related_source_event_id"] == related_source_event_id
            and previous["observation_id"] == observation_id
            and previous["body_sha256"] == body_sha256
        ):
            return "duplicate"
        return _record_conflict(
            connection,
            source_event_id=source_event_id,
            observation_id=observation_id,
            body=body,
            body_sha256=body_sha256,
            received_at=received_at,
        )

    try:
        with connection.begin_nested():
            connection.execute(
                outbox.insert().values(
                    source_event_id=source_event_id,
                    related_source_event_id=related_source_event_id,
                    observation_id=observation_id,
                    body_sha256=body_sha256,
                    body=body,
                    state="held_contract",
                    attempt_count=0,
                    next_attempt_at_utc=received_at,
                    last_error_code="f03_data_quality_contract_unconfirmed",
                )
            )
        return "staged"
    except IntegrityError:
        # A concurrent deterministic insert may have won. Resolve it only
        # after the savepoint has rolled back, without changing the winner.
        previous = (
            connection.execute(
                select(
                    outbox.c.related_source_event_id,
                    outbox.c.observation_id,
                    outbox.c.body_sha256,
                ).where(outbox.c.source_event_id == source_event_id)
            )
            .mappings()
            .one_or_none()
        )
        if previous is None:
            raise
        if (
            previous["related_source_event_id"] == related_source_event_id
            and previous["observation_id"] == observation_id
            and previous["body_sha256"] == body_sha256
        ):
            return "duplicate"
        return _record_conflict(
            connection,
            source_event_id=source_event_id,
            observation_id=observation_id,
            body=body,
            body_sha256=body_sha256,
            received_at=received_at,
        )


def _record_conflict(
    connection: Connection,
    *,
    source_event_id: UUID,
    observation_id: UUID,
    body: dict[str, object],
    body_sha256: str,
    received_at: datetime,
) -> CorrectionStatus:
    try:
        with connection.begin_nested():
            connection.execute(
                outbox_conflicts.insert().values(
                    id=UUID(
                        bytes=hashlib.sha256(
                            f"{source_event_id}:{observation_id}:{body_sha256}".encode()
                        ).digest()[:16]
                    ),
                    source_event_id=source_event_id,
                    observation_id=observation_id,
                    body_sha256=body_sha256,
                    body=body,
                    first_detected_at_utc=received_at,
                )
            )
    except IntegrityError:
        existing = connection.execute(
            select(outbox_conflicts.c.id).where(
                outbox_conflicts.c.source_event_id == source_event_id,
                outbox_conflicts.c.observation_id == observation_id,
                outbox_conflicts.c.body_sha256 == body_sha256,
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
    return "conflict"


def stage_late_reception_corrections_on_connection_candidate(
    connection: Connection,
    *,
    evidence_kind: Literal["reception", "reception_id_reuse"],
    evidence_id: UUID,
    receipt_sha256: str,
    outcome: Literal["invalid", "conflict"],
    reason_code: str,
    received_at_utc: str,
    device_id: UUID,
    warehouse_id: UUID,
    additional_targets: tuple[tuple[UUID, UUID], ...] = (),
) -> tuple[CorrectionStageCandidate, ...]:
    """Persist one held correction per already-delivered anomaly it affects.

    This runs inside the reception's existing C transaction. It never edits
    the original alert/outbox row, and its held state cannot be claimed by
    ``claim_candidate``. The correction payload intentionally records only
    IDs, controlled reason, UTC receive time, and a digest; raw rejected input
    stays in the privileged reception audit table.
    """
    if not connection.in_transaction():
        raise ValueError("caller must own an open C transaction")
    if not isinstance(evidence_id, UUID):
        raise ValueError("evidence_id must be a UUID")
    if len(receipt_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in receipt_sha256
    ):
        raise ValueError("receipt_sha256 must be a lowercase SHA-256 digest")
    if not reason_code or len(reason_code) > 80:
        raise ValueError("reason_code must be a controlled short code")
    if not isinstance(device_id, UUID) or not isinstance(warehouse_id, UUID):
        raise ValueError("trusted device and warehouse UUIDs are required")

    received = _utc(received_at_utc)
    evidence_key = f"{evidence_kind}:{evidence_id}:{receipt_sha256}"
    targets = tuple(dict.fromkeys(((device_id, warehouse_id), *additional_targets)))
    results: list[CorrectionStageCandidate] = []

    for target_device, target_warehouse in targets:
        originals = (
            connection.execute(
                select(
                    outbox.c.source_event_id,
                    outbox.c.observation_id,
                    outbox.c.body,
                    observations.c.first_received_at_utc,
                )
                .select_from(
                    outbox.join(
                        observations,
                        outbox.c.observation_id == observations.c.id,
                    )
                )
                .where(
                    outbox.c.state == "delivered",
                    outbox.c.related_source_event_id.is_(None),
                    observations.c.device_id == target_device,
                    observations.c.warehouse_id == target_warehouse,
                )
            )
            .mappings()
            .all()
        )
        for original in originals:
            original_id = original["source_event_id"]
            original_body = original["body"]
            if (
                not isinstance(original_id, UUID)
                or not isinstance(original_body, dict)
                or original_body.get("source_event_id") != str(original_id)
                or original_body.get("kind")
                not in ("temperature_high", "temperature_low")
                or original_body.get("warehouse_id") != str(target_warehouse)
            ):
                continue
            if received < _utc(original["first_received_at_utc"], stored=True):
                continue

            source_event_id = _correction_source_id(original_id, evidence_key)
            body, body_sha256 = _canonical_body(
                {
                    "schema_version": _CORRECTION_VERSION,
                    "source_event_id": str(source_event_id),
                    "kind": "data_quality",
                    "related_source_event_id": str(original_id),
                    "warehouse_id": str(target_warehouse),
                    "observation_id": str(original["observation_id"]),
                    "evidence": {
                        "evidence_kind": evidence_kind,
                        "evidence_id": str(evidence_id),
                        "reason_code": reason_code,
                        "outcome": outcome,
                        "received_at_utc": _utc_text(received),
                        "raw_sha256": receipt_sha256,
                        "send_state": "held_until_f03_contract_acceptance",
                    },
                }
            )
            status = _insert_held_correction(
                connection,
                source_event_id=source_event_id,
                related_source_event_id=original_id,
                observation_id=original["observation_id"],
                body=body,
                body_sha256=body_sha256,
                received_at=received,
            )
            results.append(
                CorrectionStageCandidate(
                    source_event_id=source_event_id,
                    related_source_event_id=original_id,
                    status=status,
                    evidence_key=evidence_key,
                )
            )
    return tuple(results)
