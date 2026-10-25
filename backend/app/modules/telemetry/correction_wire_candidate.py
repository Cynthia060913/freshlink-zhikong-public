"""Deterministic, read-only projection of a held C correction to B F03.

This prepares a proposed wire body and digest. It does not release a held
outbox row, claim work, call B, or rewrite C's frozen internal body/hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.modules.telemetry.data_quality_correction_candidate import (
    _correction_source_id,
)
from app.modules.telemetry.envelope_preview import (
    _reject_json_constant,
    _unique_json_object,
)
from app.modules.telemetry.f03_body_candidate import (
    f03_digest_candidate,
    normalize_f03_body_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.recovery_observation_candidate import _sample_from_saved
from app.modules.telemetry.recovery_outbox_candidate import _instant
from app.modules.telemetry.storage_candidate import observations

_INTERNAL_VERSION = "c-data-quality-correction/v0"
_EVIDENCE_KINDS = frozenset({"reception", "reception_id_reuse"})
_OUTCOMES = frozenset({"invalid", "conflict"})


@dataclass(frozen=True)
class CorrectionWireCandidate:
    body: dict[str, Any]
    content_digest: str
    internal_body_sha256: str


def _uuid(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID string")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError(f"{name} must be a UUID string") from exc


def _canonical(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("correction must be finite JSON") from exc


def _utc(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("correction evidence time must be UTC text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("correction evidence time must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("correction evidence time needs an offset")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def project_held_correction_wire_candidate(
    *,
    internal_body: dict[str, Any],
    internal_body_sha256: str,
    original_body: dict[str, Any],
    original_body_sha256: str,
) -> CorrectionWireCandidate:
    """Validate both immutable records and project one reproducible DTO.

    The caller must separately prove that the original C row is delivered,
    that the held row references it, and that both belong to the same trusted
    warehouse. No row state transition or B send is authorized here.
    """
    if not isinstance(internal_body, dict) or not isinstance(original_body, dict):
        raise ValueError("both frozen bodies must be JSON objects")
    internal_digest = hashlib.sha256(_canonical(internal_body)).hexdigest()
    if internal_digest != internal_body_sha256:
        raise ValueError("internal correction body digest mismatch")
    original = normalize_f03_body_candidate(original_body)
    if f03_digest_candidate(original).removeprefix("sha256:") != original_body_sha256:
        raise ValueError("original anomaly body digest mismatch")
    if original["kind"] not in {"temperature_high", "temperature_low"}:
        raise ValueError("linked event is not a temperature anomaly")
    if original["related_source_event_id"] is not None:
        raise ValueError("linked anomaly cannot itself be a child")
    if (
        internal_body.get("schema_version") != _INTERNAL_VERSION
        or internal_body.get("kind") != "data_quality"
    ):
        raise ValueError("unsupported internal correction version or kind")
    if set(internal_body) != {
        "schema_version",
        "source_event_id",
        "kind",
        "related_source_event_id",
        "warehouse_id",
        "observation_id",
        "evidence",
    }:
        raise ValueError("internal correction fields changed")
    source_id = _uuid(internal_body["source_event_id"], "source_event_id")
    related_id = _uuid(
        internal_body["related_source_event_id"], "related_source_event_id"
    )
    observation_id = _uuid(internal_body["observation_id"], "observation_id")
    warehouse_id = _uuid(internal_body["warehouse_id"], "warehouse_id")
    if (
        source_id == related_id
        or related_id != original["source_event_id"]
        or warehouse_id != original["warehouse_id"]
        or observation_id not in original["observation_ids"]
    ):
        raise ValueError("correction does not match its original anomaly")
    evidence = internal_body["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {
        "evidence_kind",
        "evidence_id",
        "reason_code",
        "outcome",
        "received_at_utc",
        "raw_sha256",
        "send_state",
    }:
        raise ValueError("invalid frozen correction evidence")
    if (
        evidence["evidence_kind"] not in _EVIDENCE_KINDS
        or evidence["outcome"] not in _OUTCOMES
        or evidence["send_state"] != "held_until_f03_contract_acceptance"
        or not isinstance(evidence["reason_code"], str)
        or not 1 <= len(evidence["reason_code"]) <= 80
    ):
        raise ValueError("invalid correction evidence classification")
    evidence_id = _uuid(evidence["evidence_id"], "evidence_id")
    raw_sha256 = evidence["raw_sha256"]
    if (
        not isinstance(raw_sha256, str)
        or len(raw_sha256) != 64
        or any(char not in "0123456789abcdef" for char in raw_sha256)
    ):
        raise ValueError("invalid raw evidence digest")
    received_at = _utc(evidence["received_at_utc"])
    evidence_key = f"{evidence['evidence_kind']}:{evidence_id}:{raw_sha256}"
    if UUID(source_id) != _correction_source_id(UUID(related_id), evidence_key):
        raise ValueError("correction source_event_id is not its stable evidence key")
    wire = {
        "schema_version": "1.0",
        "source_event_id": source_id,
        "kind": "data_quality",
        "warehouse_id": warehouse_id,
        "batch_id": original["batch_id"],
        "observation_ids": [observation_id],
        "metric_code": "temperature",
        # The rejected reception may have no usable sampled_at. This UTC
        # instant is the receipt time, not a claimed sensor sampling time.
        "sampled_at": received_at,
        "value": None,
        "unit": "°C",
        "severity": "info",
        "rule_snapshot_id": original["rule_snapshot_id"],
        "rule_version": original["rule_version"],
        "algorithm_version": _INTERNAL_VERSION,
        "evidence": {
            "correction": {
                "evidence_kind": evidence["evidence_kind"],
                "evidence_id": evidence_id,
                "reason_code": evidence["reason_code"],
                "outcome": evidence["outcome"],
                "received_at_utc": received_at,
                "raw_sha256": raw_sha256,
            }
        },
        "related_source_event_id": related_id,
    }
    encoded = _canonical(wire)
    if len(encoded) > 32768:
        raise ValueError("correction wire body exceeds F03 limit")
    frozen = json.loads(encoded)
    return CorrectionWireCandidate(
        body=frozen,
        content_digest="sha256:" + hashlib.sha256(encoded).hexdigest(),
        internal_body_sha256=internal_digest,
    )


def prepare_held_correction_wire_on_connection_candidate(
    connection: Connection, *, source_event_id: UUID
) -> CorrectionWireCandidate | None:
    """Read a held correction and its delivered original without changing them.

    This is a preparation port only. In particular, returning a DTO is not a
    claim, sender enablement, receipt, or permission to perform B HTTP.
    """
    if not connection.in_transaction():
        raise ValueError("caller must own an open C transaction")
    if not isinstance(source_event_id, UUID):
        raise ValueError("correction source_event_id must be a UUID")
    correction = (
        connection.execute(
            select(outbox).where(outbox.c.source_event_id == source_event_id)
        )
        .mappings()
        .one_or_none()
    )
    if correction is None or correction["state"] != "held_contract":
        return None
    original_id = correction["related_source_event_id"]
    if (
        original_id is None
        or correction["attempt_count"] != 0
        or correction["lease_token"] is not None
        or correction["lease_until_utc"] is not None
    ):
        raise ValueError("held correction has invalid outbox state")
    original = (
        connection.execute(
            select(outbox).where(outbox.c.source_event_id == original_id)
        )
        .mappings()
        .one_or_none()
    )
    if original is None or original["state"] != "delivered":
        return None
    if (
        correction["observation_id"] != original["observation_id"]
        or not isinstance(correction["body"], dict)
        or not isinstance(original["body"], dict)
        or correction["body"].get("source_event_id") != str(source_event_id)
        or correction["body"].get("related_source_event_id") != str(original_id)
    ):
        raise ValueError("held correction does not match original outbox row")
    projection = project_held_correction_wire_candidate(
        internal_body=correction["body"],
        internal_body_sha256=correction["body_sha256"],
        original_body=original["body"],
        original_body_sha256=original["body_sha256"],
    )
    saved = (
        connection.execute(
            select(observations).where(observations.c.id == original["observation_id"])
        )
        .mappings()
        .one_or_none()
    )
    if saved is None:
        raise ValueError("original anomaly observation is missing")
    try:
        parsed_raw = json.loads(
            saved["raw_text"],
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(parsed_raw, dict) or _canonical(parsed_raw) != _canonical(
            saved["raw_payload"]
        ):
            raise ValueError("original raw_text and raw_payload differ")
    except (TypeError, RecursionError, ValueError) as exc:
        raise ValueError("original raw_text and raw_payload differ") from exc
    sample = _sample_from_saved(saved, batch_id=UUID(projection.body["batch_id"]))
    original_body = original["body"]
    if (
        sample.observation_id != original["observation_id"]
        or sample.warehouse_id != UUID(projection.body["warehouse_id"])
        or sample.quality != ("ok",)
        or sample.sampled_at != _instant(original_body["sampled_at"])
        or sample.temperature_c != original_body["value"]
        or original_body["observation_ids"] != [str(sample.observation_id)]
    ):
        raise ValueError("original anomaly differs from preserved raw observation")
    return projection
