"""Pure bounded CSV preview for fixed C1 vectors; no DB, auth, or public API."""

from __future__ import annotations

import csv
from io import StringIO
from typing import Any
from unicodedata import category

from app.modules.telemetry.query_preview import (
    select_preview_observations,
    sort_preview_by_sampled_at,
)

MAX_PREVIEW_EXPORT_ROWS = 10_000
_LEADING_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
PREVIEW_COLUMNS = (
    "observation_id",
    "device_id",
    "warehouse_id",
    "sampled_at",
    "received_at",
    "raw_temperature_value",
    "raw_temperature_unit",
    "temperature_c",
    "raw_humidity_value",
    "raw_humidity_unit",
    "humidity_percent",
    "quality",
    "risk_state",
    "rule_version",
)


def _display_cell(value: object) -> str:
    if value is None:
        return ""
    # JSON permits escaped lone surrogates. Keep the exact value in raw evidence,
    # but render it visibly in a UTF-8 CSV instead of failing the whole export.
    rendered = str(value).encode("utf-8", errors="backslashreplace").decode("utf-8")
    starts_with_format = (
        bool(rendered) and category(rendered[0]) in _LEADING_UNSAFE_CATEGORIES
    )
    if isinstance(value, str) and (
        rendered.lstrip().startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠"))
        or starts_with_format
    ):
        # Human spreadsheet preview only; this is not safe for arbitrary re-import.
        return "'" + rendered
    return rendered


def _raw_metric(raw: dict[str, Any], key: str) -> tuple[object, object]:
    metrics = raw.get("metrics")
    if not isinstance(metrics, list):
        return None, None
    matches = [
        metric
        for metric in metrics
        if isinstance(metric, dict) and metric.get("key") == key
    ]
    if len(matches) != 1:
        return None, None  # Repeated keys are ambiguous; raw remains in evidence.
    return matches[0].get("value"), matches[0].get("unit")


def _preview_row(result: dict[str, Any]) -> list[str]:
    normalized = result.get("normalized")
    risk = result.get("risk")
    quality = result.get("quality")
    raw = result.get("raw")
    if (
        not isinstance(normalized, dict)
        or not isinstance(risk, dict)
        or not isinstance(quality, list)
        or not isinstance(raw, dict)
    ):
        raise ValueError("result must contain normalized, risk, quality, and raw data")
    metrics = normalized.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("normalized metrics must be an object")
    if any(not isinstance(tag, str) for tag in quality):
        raise ValueError("quality must contain text tags")
    raw_temperature, raw_temperature_unit = _raw_metric(raw, "temperature")
    raw_humidity, raw_humidity_unit = _raw_metric(raw, "humidity")
    values = (
        result.get("observation_id"),
        normalized.get("device_id"),
        normalized.get("warehouse_id"),
        normalized.get("sampled_at"),
        normalized.get("received_at"),
        raw_temperature,
        raw_temperature_unit,
        metrics.get("temperature"),
        raw_humidity,
        raw_humidity_unit,
        metrics.get("humidity"),
        "|".join(quality),
        risk.get("state"),
        result.get("rule_version"),
    )
    return [_display_cell(value) for value in values]


def render_preview_csv(results: list[dict[str, Any]], filters: dict[str, Any]) -> bytes:
    """Render in-memory fixtures as UTF-8 BOM CSV with a bounded draft column set.

    Filtering is shared with the page preview. More than 10000 rows is an
    error, never a silent partial export. This has no actor, DB watermark,
    signed cursor, production route, or approved versioned rule result.
    """
    selected = select_preview_observations(results, filters)
    if len(selected) > MAX_PREVIEW_EXPORT_ROWS:
        raise ValueError("preview CSV row limit exceeded")
    ordered = sort_preview_by_sampled_at(selected)
    output = StringIO(newline="")
    csv_writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    csv_writer.writerow(PREVIEW_COLUMNS)
    for result in ordered:
        csv_writer.writerow(_preview_row(result))
    return ("\ufeff" + output.getvalue()).encode("utf-8")
