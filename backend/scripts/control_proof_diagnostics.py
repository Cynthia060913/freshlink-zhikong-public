"""Bounded, value-whitelisted diagnostics for disposable control proofs."""

import math
from datetime import datetime
from uuid import UUID


def _identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):  # fmt: skip
        return None


def _time(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):  # fmt: skip
        return None


def _number(value):
    return value if type(value) in (float, int) and math.isfinite(value) else None


def observation_view(row):
    normalized = row.get("normalized") or {}
    return {
        "observation_id": _identifier(row.get("id")),
        "device_id": _identifier(row.get("device_id")),
        "first_received_at": _time(row.get("first_received_at_utc")),
        "sampled_at": _time(normalized.get("sampled_at")),
        "temperature_c": _number(normalized.get("metrics", {}).get("temperature")),
        "quality_ok": row.get("quality") == ["ok"],
    }


def checkpoint_view(
    *,
    stage,
    error,
    started,
    ack_seen,
    rule_start,
    baseline,
    observations,
    counts,
    plant,
):
    stages = {
        "starting_workers",
        "online_baseline",
        "control_ack",
        "normal_rule",
        "stored_cooling",
        "replay",
        "completed",
    }
    return {
        "scope": "disposable_control_checkpoint_only",
        "stage": stage if stage in stages else "unknown",
        "error_type": type(error).__name__,
        "started_at": _time(started),
        "ack_seen_at": _time(ack_seen),
        "normal_rule_start_at": _time(rule_start),
        "baseline": observation_view(baseline) if baseline is not None else None,
        "observation_total": len(observations),
        "last_observations": [observation_view(row) for row in observations[-64:]],
        "pipeline_counts": {
            name: value
            for name, value in counts.items()
            if name in {"inbox", "receipts", "observations"}
            and type(value) is int
            and value >= 0
        },
        "plant": {
            "temperature_c": _number(plant.get("temperature_c")),
            "execution_count": _number(plant.get("execution_count")),
            "cooling_enabled": plant.get("cooling_enabled") is True,
            "stepped_at": _time(plant.get("stepped_at")),
        },
    }
