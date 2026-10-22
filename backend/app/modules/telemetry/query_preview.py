"""Pure C1 filter preview for fixed, already processed observations.

This is not a database query, authenticated API, paginator, or CSV exporter.
The filter vocabulary is a proposal until the shared contract is approved.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.modules.telemetry.quality import _utc, _uuid

_FILTER_FIELDS = frozenset(
    {
        "device_id",
        "warehouse_id",
        "sampled_from",
        "sampled_to",
        "received_from",
        "received_to",
        "quality_in",
    }
)


def _utc_bound(value: object, name: str) -> datetime:
    normalized = _utc(value)
    if normalized is None:
        raise ValueError(f"{name} must be an ISO 8601 UTC timestamp")
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def select_preview_observations(
    results: list[dict[str, Any]], filters: dict[str, Any]
) -> list[dict[str, Any]]:
    """Select fixed-example rows without changing raw or inventing a normal state."""
    if not isinstance(filters, dict):
        raise ValueError("filters must be an object")
    unknown = set(filters) - _FILTER_FIELDS
    if unknown:
        raise ValueError(f"unsupported filters: {', '.join(sorted(unknown))}")

    ids: dict[str, str] = {}
    for field in ("device_id", "warehouse_id"):
        if field in filters:
            value = _uuid(filters[field])
            if value is None:
                raise ValueError(f"{field} must be a UUID")
            ids[field] = value

    start = (
        _utc_bound(filters["sampled_from"], "sampled_from")
        if "sampled_from" in filters
        else None
    )
    end = (
        _utc_bound(filters["sampled_to"], "sampled_to")
        if "sampled_to" in filters
        else None
    )
    if start is not None and end is not None and start >= end:
        raise ValueError("sampled_from must be before sampled_to")
    received_start = (
        _utc_bound(filters["received_from"], "received_from")
        if "received_from" in filters
        else None
    )
    received_end = (
        _utc_bound(filters["received_to"], "received_to")
        if "received_to" in filters
        else None
    )
    if received_start is not None and received_end is not None:
        if received_start >= received_end:
            raise ValueError("received_from must be before received_to")

    quality_in = filters.get("quality_in")
    if "quality_in" in filters and (
        not isinstance(quality_in, list)
        or not quality_in
        or any(not isinstance(tag, str) or not tag for tag in quality_in)
    ):
        raise ValueError("quality_in must be a nonempty list of tags")

    selected: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("results must contain processed observations")
        normalized = result.get("normalized")
        quality = result.get("quality")
        if not isinstance(normalized, dict) or not isinstance(quality, list):
            raise ValueError("results must contain normalized data and quality tags")
        if any(normalized.get(field) != value for field, value in ids.items()):
            continue
        if quality_in is not None and not set(quality).intersection(quality_in):
            continue
        if start is not None or end is not None:
            sampled = normalized.get("sampled_at")
            if sampled is None:
                continue  # Invalid raw time remains available without a time filter.
            sampled_at = _utc_bound(sampled, "normalized sampled_at")
            if start is not None and sampled_at < start:
                continue
            if end is not None and sampled_at >= end:
                continue
        if received_start is not None or received_end is not None:
            received = normalized.get("received_at")
            if received is None:
                continue  # Invalid receipt remains available without this range.
            received_at = _utc_bound(received, "normalized received_at")
            if received_start is not None and received_at < received_start:
                continue
            if received_end is not None and received_at >= received_end:
                continue
        selected.append(deepcopy(result))
    return selected


def sort_preview_by_sampled_at(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Order fixed rows by valid sampled UTC time, then ID; unknown time last.

    This does not implement a database cursor, receiving watermark, or paging.
    """
    keyed: list[tuple[bool, datetime, str, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("results must contain processed observations")
        observation_id = _uuid(result.get("observation_id"))
        normalized = result.get("normalized")
        if observation_id is None or not isinstance(normalized, dict):
            raise ValueError("each result needs an observation_id and normalized data")
        if observation_id in seen_ids:
            raise ValueError("observation_id must be unique")
        seen_ids.add(observation_id)
        sampled = normalized.get("sampled_at")
        sampled_at = (
            _utc_bound(sampled, "normalized sampled_at")
            if sampled is not None
            else datetime.max.replace(tzinfo=UTC)
        )
        keyed.append((sampled is None, sampled_at, observation_id, result))
    keyed.sort(key=lambda item: item[:3])
    return [deepcopy(item[3]) for item in keyed]


def page_preview_by_sampled_at(
    results: list[dict[str, Any]],
    *,
    limit: int,
    after_observation_id: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Page fixed, already selected rows in stable sampled-time order.

    The after ID is a local test marker, not a signed cursor or snapshot.
    Callers must apply the same filters before each page. This function does
    not query a database, enforce permissions, or freeze newly received rows.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    ordered = sort_preview_by_sampled_at(results)
    start = 0
    if after_observation_id is not None:
        after_id = _uuid(after_observation_id)
        if after_id is None:
            raise ValueError("after_observation_id must be a UUID")
        for index, row in enumerate(ordered):
            if _uuid(row["observation_id"]) == after_id:
                start = index + 1
                break
        else:
            raise ValueError("after_observation_id is not in the selected rows")
    page = ordered[start : start + limit]
    next_after = (
        _uuid(page[-1]["observation_id"])
        if page and start + limit < len(ordered)
        else None
    )
    return page, next_after
