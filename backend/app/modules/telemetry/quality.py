"""Pure telemetry validation and quality classification for fixed JSON examples.

This module has no database, MQTT, authentication, or public API integration.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any
from uuid import UUID

_UNITS = {"temperature": "°C", "humidity": "%RH"}
# draft-1 belongs only to the retained standalone fixtures. A's live ingress
# accepts 1.0; this pure preview must not treat arbitrary future schemas as valid.
_PREVIEW_SCHEMA_VERSIONS = frozenset({"draft-1", "1.0"})
# RC1 quality-preview bounds; the public ingest limit is not frozen yet.
MAX_METRICS = 16
MAX_METRIC_FIELD_LENGTH = 64
_INVALID_METRIC_SHAPE_PREFIXES = (
    "invalid_metric_key",
    "invalid_metric_unit",
    "invalid_metric_entry",
)
_TIME_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)"
)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except OverflowError:
        return None
    except ValueError:
        return None
    return result if isfinite(result) else None


def _uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _utc(value: object) -> str | None:
    if not isinstance(value, str) or _TIME_PATTERN.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() != timedelta(0):
        return None
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _fingerprint(raw: dict[str, Any], normalized: dict[str, Any]) -> str:
    # received_at is a receipt, so retransmission may legitimately change it.
    # A changed extension field is still changed message content, even when C
    # does not normalize it yet. Only receipt time is exempt from replay identity.
    body = deepcopy(raw)
    body.pop("received_at", None)
    for field in ("device_id", "warehouse_id", "message_id", "sampled_at"):
        if field in normalized:
            body[field] = normalized[field]
    metrics = body.get("metrics")
    if isinstance(metrics, list) and all(
        isinstance(item, dict) and isinstance(item.get("key"), str) for item in metrics
    ):
        keys = [item["key"] for item in metrics]
        if len(keys) == len(set(keys)):
            body["metrics"] = sorted(metrics, key=lambda item: item["key"])
    return json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)


def process_batch(
    observations: list[object], threshold_c: float | None = None
) -> list[dict[str, Any]]:
    """Classify JSON observations without discarding any original input.

    Duplicate state is limited to this batch. The threshold is an explicit
    fixture rule, not a warehouse rule or a persisted alert.
    """
    if not isinstance(observations, list):
        raise ValueError("observations must be a list")
    threshold = None if threshold_c is None else _number(threshold_c)
    if threshold_c is not None and threshold is None:
        raise ValueError("threshold_c must be a finite number")

    fingerprints: dict[tuple[str, str], str] = {}
    results: list[dict[str, Any]] = []
    for observation in observations:
        raw = deepcopy(observation)
        if not isinstance(raw, dict):
            results.append(
                {
                    "raw": raw,
                    "quality": ["invalid_observation"],
                    "duplicate": "unkeyed",
                    "normalized": {},
                    "risk": {
                        "state": "unknown",
                        "reason": "invalid_observation",
                        "temperature_c": None,
                        "threshold_c": threshold,
                    },
                }
            )
            continue

        quality: list[str] = []
        normalized: dict[str, Any] = {}
        for field in (
            "schema_version",
            "device_id",
            "warehouse_id",
            "message_id",
            "sampled_at",
            "received_at",
            "metrics",
        ):
            if field not in raw or raw[field] is None or raw[field] == "":
                quality.append(f"missing_field:{field}")

        version = raw.get("schema_version")
        if isinstance(version, str) and version in _PREVIEW_SCHEMA_VERSIONS:
            normalized["schema_version"] = version
        elif "schema_version" in raw and version is not None:
            quality.append("invalid_schema_version")

        for field in ("device_id", "warehouse_id", "message_id"):
            value = _uuid(raw.get(field))
            if value is not None:
                normalized[field] = value
            elif field in raw and raw[field] is not None:
                quality.append(f"invalid_id:{field}")

        for field in ("sampled_at", "received_at"):
            value = _utc(raw.get(field))
            if value is not None:
                normalized[field] = value
            elif field in raw and raw[field] is not None:
                quality.append(f"invalid_time:{field}")

        values: dict[str, float] = {}
        seen_keys: set[str] = set()
        metrics = raw.get("metrics")
        if isinstance(metrics, list):
            if len(metrics) > MAX_METRICS:
                quality.append("too_many_metrics")
            for metric in metrics:
                if not isinstance(metric, dict):
                    quality.append("invalid_metric_entry")
                    continue
                key = metric.get("key")
                if not isinstance(key, str) or not key:
                    quality.append("invalid_metric_key")
                    continue
                if len(key) > MAX_METRIC_FIELD_LENGTH:
                    quality.append("invalid_metric_key_length")
                    continue
                if key in seen_keys:
                    quality.append(f"duplicate_metric:{key}")
                    values.pop(key, None)  # Ambiguous value cannot be normalized.
                    continue
                seen_keys.add(key)
                unit = metric.get("unit")
                if not isinstance(unit, str) or not unit:
                    quality.append(f"invalid_metric_unit:{key}")
                    continue
                if len(unit) > MAX_METRIC_FIELD_LENGTH:
                    quality.append(f"invalid_metric_unit_length:{key}")
                    continue
                if key not in _UNITS:
                    continue  # Other metrics remain intact in raw.
                if "value" not in metric or metric["value"] is None:
                    quality.append(f"missing_value:{key}")
                    continue
                if metric.get("unit") != _UNITS[key]:
                    quality.append(f"invalid_unit:{key}")
                    continue
                numeric = _number(metric["value"])
                if numeric is None:
                    quality.append(f"invalid_value:{key}")
                    continue
                if key == "humidity" and not 0 <= numeric <= 100:
                    quality.append("invalid_range:humidity")
                    continue
                values[key] = numeric
        elif "metrics" in raw and metrics is not None:
            quality.append("invalid_metrics")
        for key in _UNITS:
            if key not in seen_keys:
                quality.append(f"missing_metric:{key}")
        normalized["metrics"] = values

        duplicate = "unkeyed"
        if "device_id" in normalized and "message_id" in normalized:
            identity = (normalized["device_id"], normalized["message_id"])
            fingerprint = _fingerprint(raw, normalized)
            if identity not in fingerprints:
                fingerprints[identity] = fingerprint
                duplicate = "new"
            elif fingerprints[identity] == fingerprint:
                duplicate = "duplicate"
                quality.append("duplicate_message")
            else:
                duplicate = "conflict"
                quality.append("conflicting_message")

        temperature = values.get("temperature")
        if duplicate == "duplicate":
            state, reason = "unknown", "duplicate_message"
        elif duplicate == "conflict":
            state, reason = "unknown", "conflicting_message"
        elif "too_many_metrics" in quality or any(
            tag.startswith(_INVALID_METRIC_SHAPE_PREFIXES) for tag in quality
        ):
            state, reason = "unknown", "invalid_metric_shape"
        elif temperature is None:
            state, reason = "unknown", "missing_or_invalid_temperature"
        elif threshold is None:
            state, reason = "unknown", "no_threshold_rule"
        elif not all(
            field in normalized
            for field in (
                "schema_version",
                "device_id",
                "warehouse_id",
                "message_id",
                "sampled_at",
            )
        ):
            state, reason = "unknown", "untraceable_observation"
        elif any(tag.startswith("duplicate_metric:") for tag in quality):
            state, reason = "unknown", "ambiguous_metrics"
        elif temperature > threshold:
            state, reason = "candidate", "threshold_exceeded"
        elif quality:
            state, reason = "unknown", "incomplete_observation"
        else:
            state, reason = "normal", "within_threshold"

        results.append(
            {
                "raw": raw,
                "quality": list(dict.fromkeys(quality)) or ["ok"],
                "duplicate": duplicate,
                "normalized": normalized,
                "risk": {
                    "state": state,
                    "reason": reason,
                    "temperature_c": temperature,
                    "threshold_c": threshold,
                },
            }
        )
    return results
