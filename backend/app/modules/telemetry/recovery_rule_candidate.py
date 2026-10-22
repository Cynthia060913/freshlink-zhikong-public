"""Bind a recovery condition to the original immutable B rule selection.

Only the first temperature-high scenario is supported. The caller retrieves
the historical rule for the original anomaly's sampled_at, not the current
rule. No B import, database write, or automatic alert resolution occurs here.
"""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any, Literal

from app.modules.telemetry.recovery_window_candidate import RecoveryRuleCandidate


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _instant(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an aware timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an aware timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} must be an aware timestamp")
    return result


def bind_original_recovery_rule(
    original: dict[str, Any], selected: dict[str, Any] | None
) -> RecoveryRuleCandidate | None:
    """Accept only the same historical rule that produced the original event.

    A missing rule or missing explicit recovery condition cannot prove recovery.
    B's allowed one/two-sample configuration is strengthened to S0's minimum
    three samples; a shorter-than-90-second gap may be retained. Invalid or
    changed rule identity is rejected rather than silently using a new rule.
    """
    if not isinstance(original, dict) or original.get("kind") != "temperature_high":
        raise ValueError("original must be a temperature-high anomaly")
    if selected is None:
        return None
    if not isinstance(selected, dict):
        raise ValueError("selected historical rule must be an object")
    for event_key, rule_key in (
        ("warehouse_id", "warehouse_id"),
        ("batch_id", "batch_id"),
        ("metric_code", "metric_code"),
        ("rule_snapshot_id", "snapshot_id"),
        ("rule_version", "rule_version"),
        ("severity", "severity"),
        ("kind", "kind"),
    ):
        value = original.get(event_key)
        if not isinstance(value, str) or not value or selected.get(rule_key) != value:
            raise ValueError(f"historical recovery rule {event_key} mismatch")
    if (
        original.get("unit") != "°C"
        or selected.get("unit") != "°C"
        or selected.get("comparison") != ">"
    ):
        raise ValueError("original temperature rule unit or comparator mismatch")
    evidence = original.get("evidence")
    if not isinstance(evidence, dict) or evidence.get("comparison") != ">":
        raise ValueError("original threshold evidence is unavailable")
    if _number(evidence.get("threshold"), "original threshold") != _number(
        selected.get("threshold"), "historical threshold"
    ):
        raise ValueError("historical threshold differs from original anomaly")
    sampled_at = _instant(original.get("sampled_at"), "original sampled_at")
    starts_at = _instant(selected.get("effective_start_at"), "rule start")
    ends_at = selected.get("effective_end_at")
    if sampled_at < starts_at or (
        ends_at is not None and sampled_at >= _instant(ends_at, "rule end")
    ):
        raise ValueError("historical rule is not effective for original anomaly")
    recovery = selected.get("recovery")
    if recovery is None:
        return None
    if not isinstance(recovery, dict):
        raise ValueError("recovery condition must be an object")
    count = recovery.get("min_samples", 1)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("recovery min_samples must be positive")
    requested_comparison = recovery.get("comparison")
    comparison: Literal["<", "<="]
    if requested_comparison == "<":
        comparison = "<"
    elif requested_comparison == "<=":
        comparison = "<="
    else:
        raise ValueError("temperature-high recovery comparison must be < or <=")
    return RecoveryRuleCandidate(
        comparison=comparison,
        threshold_c=_number(recovery.get("threshold"), "recovery threshold"),
        min_samples=max(3, count),
        max_gap_seconds=recovery.get("max_gap_seconds", 90),
    )
