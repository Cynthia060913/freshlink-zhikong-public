"""Freeze a C temperature candidate in B's pinned F03 JSON shape.

This is a fail-closed adapter for the B PR #10 CandidateCreate shape at
3f43af70a29bfc0859fed711333cd38e7e505a05. It has no service, rule
lookup, migration, or HTTP side effect. The wire body keeps the optional
recovery link; B omits a null link from the canonical digest for ordinary
candidates to preserve the schema 1.0 replay format.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

_FIELDS = frozenset(
    {
        "schema_version",
        "source_event_id",
        "kind",
        "warehouse_id",
        "batch_id",
        "observation_ids",
        "metric_code",
        "sampled_at",
        "value",
        "unit",
        "severity",
        "rule_snapshot_id",
        "rule_version",
        "algorithm_version",
        "evidence",
        "related_source_event_id",
    }
)
_KINDS = frozenset({"temperature_high", "temperature_low", "temperature_recovered"})
_SEVERITIES = frozenset({"info", "warning", "critical"})


def _uuid(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID string")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError(f"{name} must be a UUID string") from exc


def _text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    stripped = value.strip()
    if not stripped or len(stripped) > maximum:
        raise ValueError(f"{name} must be 1-{maximum} characters")
    return stripped


def _sampled_at(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("sampled_at must be an ISO time with timezone")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError("sampled_at must be an ISO time with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("sampled_at must carry a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def normalize_f03_body_candidate(body: dict[str, Any]) -> dict[str, Any]:
    """Return the complete canonical *input* to B's CandidateCreate digest.

    Production staging must use this result, not a partial projection. Unknown
    fields fail instead of being silently dropped by B's DTO. The function is
    C-only and only admits temperature threshold/recovery candidates.
    """
    if not isinstance(body, dict) or not _FIELDS.issuperset(body):
        raise ValueError("candidate must be an object of known F03 fields")
    if body.get("schema_version") != "1.0":
        raise ValueError("schema_version must be 1.0")
    source_id = _uuid(body.get("source_event_id"), "source_event_id")
    kind = body.get("kind")
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ValueError("unsupported C temperature candidate kind")
    linked = body.get("observation_ids")
    if not isinstance(linked, list) or not 1 <= len(linked) <= 64:
        raise ValueError("observation_ids must contain 1-64 UUIDs")
    observation_ids = [_uuid(value, "observation_id") for value in linked]
    if len(observation_ids) != len(set(observation_ids)):
        raise ValueError("observation_ids must be distinct")
    if body.get("metric_code") != "temperature" or body.get("unit") != "°C":
        raise ValueError("temperature candidate requires temperature in °C")
    value = body.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("temperature value must be numeric")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError("temperature value must be finite")
    severity = body.get("severity")
    if not isinstance(severity, str) or severity.strip().lower() not in _SEVERITIES:
        raise ValueError("severity must be info, warning, or critical")
    evidence = body.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError("candidate evidence must be a nonempty object")
    related = body.get("related_source_event_id")
    if kind == "temperature_recovered":
        related = _uuid(related, "related_source_event_id")
        if related == source_id:
            raise ValueError("recovery must reference a different source_event_id")
    elif related is not None:
        raise ValueError("only a recovery can reference an original candidate")

    normalized: dict[str, Any] = {
        "schema_version": "1.0",
        "source_event_id": source_id,
        "kind": kind,
        "warehouse_id": _uuid(body.get("warehouse_id"), "warehouse_id"),
        "batch_id": _uuid(body.get("batch_id"), "batch_id"),
        "observation_ids": observation_ids,
        "metric_code": "temperature",
        "sampled_at": _sampled_at(body.get("sampled_at")),
        "value": numeric_value,
        "unit": "°C",
        "severity": severity.strip().lower(),
        "rule_snapshot_id": _uuid(body.get("rule_snapshot_id"), "rule_snapshot_id"),
        "rule_version": _text(body.get("rule_version"), "rule_version", 64),
        "algorithm_version": _text(
            body.get("algorithm_version"), "algorithm_version", 64
        ),
        "evidence": evidence,
        "related_source_event_id": related,
    }
    try:
        encoded = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate evidence must be finite JSON") from exc
    if len(encoded) > 32768:
        raise ValueError("candidate exceeds B F03 32768-byte limit")
    return json.loads(encoded)


def f03_digest_candidate(body: dict[str, Any]) -> str:
    """Digest a frozen wire body using B's compatible candidate field set."""
    encoded = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    normalized = normalize_f03_body_candidate(body)
    expected = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if encoded != expected:
        raise ValueError("F03 digest requires a complete normalized B body")
    canonical = dict(normalized)
    if canonical["related_source_event_id"] is None:
        del canonical["related_source_event_id"]
    canonical_bytes = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()
