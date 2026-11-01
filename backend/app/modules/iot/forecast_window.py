"""Pure input guard for a future forecast; never infers risk or sends anything.

Availability is determined by server receipt time. A late observation must not
appear in a historical decision simply because its sampling timestamp is old.
The caller must require ``status == 'ready'`` before invoking any model.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Literal
from uuid import UUID

WindowStatus = Literal[
    "ready",
    "invalid_input",
    "quality_blocked",
    "insufficient_data",
    "stale_data",
    "gapped_data",
]


@dataclass(frozen=True)
class ForecastWindowPolicy:
    history_seconds: int = 120
    horizon_seconds: int = 60
    max_age_seconds: int = 15
    min_span_seconds: int = 10
    max_gap_seconds: int = 10
    min_points: int = 8
    max_points: int = 360

    def __post_init__(self) -> None:
        values = vars(self).values()
        if any(type(value) is not int or not 1 <= value <= 86400 for value in values):
            raise ValueError("invalid_forecast_window_policy")
        if (
            self.min_points < 2
            or self.min_points > self.max_points
            or self.max_points > 10000
            or max(self.max_age_seconds, self.min_span_seconds, self.max_gap_seconds)
            > self.history_seconds
        ):
            raise ValueError("invalid_forecast_window_policy")


@dataclass(frozen=True)
class ForecastInputPoint:
    observation_id: UUID
    sampled_at: datetime
    received_at: datetime
    temperature_c: float


@dataclass(frozen=True)
class ForecastWindow:
    status: WindowStatus
    device_id: UUID
    warehouse_id: UUID
    as_of: datetime
    target_at: datetime
    eligible_count: int
    latest_sample_age_seconds: float | None
    points: tuple[ForecastInputPoint, ...]
    excluded: tuple[tuple[str, int], ...]


def _utc(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("aware_timestamp_required")
    return value.astimezone(UTC)


def build_forecast_window(
    observations: Iterable[Mapping[str, object]],
    *,
    device_id: UUID,
    warehouse_id: UUID,
    as_of: datetime,
    policy: ForecastWindowPolicy = ForecastWindowPolicy(),
) -> ForecastWindow:
    """Select clean, available observations from exactly one device and cold room.

    Non-ready windows expose no model points. Counts and timestamps remain for
    diagnostics; they are not a normal-risk or a temperature prediction result.
    Threshold rules, learned parameters and forecast accuracy belong elsewhere.
    """
    if not isinstance(device_id, UUID) or not isinstance(warehouse_id, UUID):
        raise ValueError("UUID_scope_required")
    decision = _utc(as_of)
    start = decision - timedelta(seconds=policy.history_seconds)
    excluded: Counter[str] = Counter()
    points: dict[UUID, ForecastInputPoint] = {}
    latest_bad: datetime | None = None
    for row in observations:
        if row.get("device_id") != str(device_id) or row.get("warehouse_id") != str(
            warehouse_id
        ):
            excluded["other_scope"] += 1
            continue
        try:
            received = _utc(row.get("received_at"))
            # Filter availability BEFORE reading quality or values, so future
            # arrivals cannot affect even the historical window's status.
            if received > decision:
                excluded["not_available_as_of"] += 1
                continue
            sampled = _utc(row.get("sampled_at"))
            if sampled > decision:
                excluded["not_available_as_of"] += 1
                continue
            if sampled < start:
                excluded["outside_window"] += 1
                continue
            quality = row.get("quality")
            if quality != ["ok"]:
                excluded["quality"] += 1
                latest_bad = max(latest_bad, sampled) if latest_bad else sampled
                continue
            value = row.get("temperature_c")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError("finite_normalized_temperature_required")
            key = UUID(str(row.get("observation_id")))
            point = ForecastInputPoint(key, sampled, received, float(value))
            if key in points:
                excluded["duplicate" if points[key] == point else "invalid"] += 1
            else:
                points[key] = point
        except ValueError, TypeError, OverflowError:
            excluded["invalid"] += 1

    ordered = sorted(
        points.values(),
        key=lambda p: (p.sampled_at, p.received_at, p.observation_id.int),
    )
    if len(ordered) > policy.max_points:
        excluded["bounded_history"] += len(ordered) - policy.max_points
        ordered = ordered[-policy.max_points :]
    latest = ordered[-1].sampled_at if ordered else None
    age = (decision - latest).total_seconds() if latest else None
    status: WindowStatus
    if excluded["invalid"]:
        status = "invalid_input"
    elif latest_bad is not None and (latest is None or latest_bad >= latest):
        status = "quality_blocked"
    elif (
        len(ordered) < policy.min_points
        or (ordered[-1].sampled_at - ordered[0].sampled_at).total_seconds()
        < policy.min_span_seconds
    ):
        status = "insufficient_data"
    elif age is not None and age > policy.max_age_seconds:
        status = "stale_data"
    elif any(
        (right.sampled_at - left.sampled_at).total_seconds() > policy.max_gap_seconds
        for left, right in zip(ordered, ordered[1:], strict=False)
    ):
        status = "gapped_data"
    else:
        status = "ready"
    return ForecastWindow(
        status=status,
        device_id=device_id,
        warehouse_id=warehouse_id,
        as_of=decision,
        target_at=decision + timedelta(seconds=policy.horizon_seconds),
        eligible_count=len(ordered),
        latest_sample_age_seconds=age,
        points=tuple(ordered) if status == "ready" else (),
        excluded=tuple(
            sorted((key, value) for key, value in excluded.items() if value)
        ),
    )
