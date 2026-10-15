"""Pure C-side F03 RC2 candidate projection for fixed examples only.

The caller supplies already assigned observation/source IDs and an explicit
synthetic rule snapshot. This module does not select B rules, persist an outbox,
call B, or define the production candidate API.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from app.modules.telemetry.quality import _number, _utc, _uuid

ALGORITHM_VERSION = "threshold-rule/0.2.0-rc2"


def _required_uuid(value: object, name: str) -> str:
    normalized = _uuid(value)
    if normalized is None:
        raise ValueError(f"{name} must be a UUID")
    return normalized


def _required_utc(value: object, name: str) -> datetime:
    normalized = _utc(value)
    if normalized is None:
        raise ValueError(f"{name} must be a UTC timestamp")
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be nonempty text")
    return value


def build_candidate_preview(
    processed: dict[str, Any],
    *,
    observation_id: str,
    source_event_id: str,
    rule: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Project one eligible high-temperature row to the proposed RC2 JSON.

    A missing rule or an ineligible observation emits nothing. A supplied rule
    with inconsistent identity, time, unit, or threshold is rejected instead of
    manufacturing a candidate. IDs must come from the future durable store.
    """
    if rule is None:
        return None
    if not isinstance(processed, dict) or not isinstance(rule, dict):
        raise ValueError("processed and rule must be objects")
    if processed.get("duplicate") != "new":
        return None
    risk = processed.get("risk")
    if not isinstance(risk, dict) or risk.get("state") != "candidate":
        return None

    normalized = processed.get("normalized")
    raw = processed.get("raw")
    quality = processed.get("quality")
    if (
        not isinstance(normalized, dict)
        or not isinstance(raw, dict)
        or not isinstance(quality, list)
        or any(not isinstance(tag, str) for tag in quality)
    ):
        raise ValueError("processed observation is incomplete")
    if normalized.get("schema_version") != "1.0":
        raise ValueError("RC2 preview requires schema version 1.0")

    device_id = _required_uuid(normalized.get("device_id"), "device_id")
    message_id = _required_uuid(normalized.get("message_id"), "message_id")
    warehouse_id = _required_uuid(normalized.get("warehouse_id"), "warehouse_id")
    if _required_uuid(rule.get("warehouse_id"), "rule warehouse_id") != warehouse_id:
        raise ValueError("rule warehouse differs from observation")
    batch_id = _required_uuid(rule.get("batch_id"), "batch_id")
    snapshot_id = _required_uuid(rule.get("rule_snapshot_id"), "rule_snapshot_id")
    sampled_at = _required_utc(normalized.get("sampled_at"), "sampled_at")
    if raw.get("schema_version") != "1.0" or any(
        _uuid(raw.get(field)) != expected
        for field, expected in (
            ("device_id", device_id),
            ("warehouse_id", warehouse_id),
            ("message_id", message_id),
        )
    ):
        raise ValueError("raw identity differs from processed observation")
    if _utc(raw.get("sampled_at")) != _utc(normalized["sampled_at"]):
        raise ValueError("raw sampled_at differs from processed observation")
    starts_at = _required_utc(rule.get("effective_from"), "effective_from")
    ends_at = (
        _required_utc(rule["effective_to"], "effective_to")
        if rule.get("effective_to") is not None
        else None
    )
    if sampled_at < starts_at or (ends_at is not None and sampled_at >= ends_at):
        raise ValueError("rule is not effective at sampled_at")
    if ends_at is not None and ends_at <= starts_at:
        raise ValueError("rule effective interval is invalid")
    if rule.get("metric_code") != "temperature" or rule.get("unit") != "°C":
        raise ValueError("rule metric or unit differs from temperature preview")
    if rule.get("comparison") != ">":
        raise ValueError("RC2 preview requires a strict high-temperature rule")

    threshold = _number(rule.get("threshold"))
    metrics = normalized.get("metrics")
    value = metrics.get("temperature") if isinstance(metrics, dict) else None
    numeric_value = _number(value)
    if (
        threshold is None
        or numeric_value is None
        or risk.get("threshold_c") != threshold
        or risk.get("temperature_c") != numeric_value
        or risk.get("reason") != "threshold_exceeded"
        or numeric_value <= threshold
    ):
        raise ValueError("processed temperature and rule threshold disagree")
    raw_metrics = raw.get("metrics")
    matching = (
        [
            metric
            for metric in raw_metrics
            if isinstance(metric, dict) and metric.get("key") == "temperature"
        ]
        if isinstance(raw_metrics, list)
        else []
    )
    if len(matching) != 1 or matching[0].get("unit") != "°C":
        raise ValueError("raw temperature evidence is ambiguous")
    if _number(matching[0].get("value")) != numeric_value:
        raise ValueError("raw temperature differs from processed temperature")

    return {
        "schema_version": "1.0",
        "source_event_id": _required_uuid(source_event_id, "source_event_id"),
        "warehouse_id": warehouse_id,
        "batch_id": batch_id,
        "observation_ids": [_required_uuid(observation_id, "observation_id")],
        "metric_code": "temperature",
        "sampled_at": _utc(normalized["sampled_at"]),
        "value": numeric_value,
        "unit": "°C",
        "kind": "temperature_high",
        "severity": _required_text(rule.get("severity"), "severity"),
        "rule_snapshot_id": snapshot_id,
        "rule_version": _required_text(rule.get("rule_version"), "rule_version"),
        "algorithm_version": ALGORITHM_VERSION,
        "evidence": {
            "device_id": device_id,
            "message_id": message_id,
            "raw_value": deepcopy(matching[0].get("value")),
            "raw_unit": matching[0]["unit"],
            "normalized_value": numeric_value,
            "normalized_unit": "°C",
            "comparison": ">",
            "threshold": threshold,
            "quality": deepcopy(quality),
        },
    }
