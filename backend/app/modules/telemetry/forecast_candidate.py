"""Pure temperature forecast math for an already admitted as-of window.

This module does not choose a rule, write an alert, or suppress threshold alerts.
It accepts the structural shape of A's ``ForecastWindow`` without importing an
unmerged A branch. A caller must run that guard before invoking this function.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from typing import Protocol
from uuid import UUID

FEATURE_NAMES = (
    "last_temperature_c",
    "linear_change_to_target_c",
    "recent_mean_minus_last_c",
    "window_range_c",
)


class ForecastPointLike(Protocol):
    observation_id: UUID
    sampled_at: datetime
    received_at: datetime
    temperature_c: float


class ForecastWindowLike(Protocol):
    status: str
    device_id: UUID
    warehouse_id: UUID
    as_of: datetime
    target_at: datetime
    points: Sequence[ForecastPointLike]


@dataclass(frozen=True)
class RidgeParameters:
    model_version: str
    history_seconds: int
    horizon_seconds: int
    feature_mean: tuple[float, ...]
    feature_scale: tuple[float, ...]
    intercept_c: float
    coefficients: tuple[float, ...]

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> RidgeParameters:
        if value.get("schema_version") != "c-ridge-v1":
            raise ValueError("unsupported_parameter_schema")
        if value.get("feature_names") != list(FEATURE_NAMES):
            raise ValueError("feature_names_mismatch")
        version = value.get("model_version")
        history, horizon = value.get("history_seconds"), value.get("horizon_seconds")
        if not isinstance(version, str) or not version or len(version) > 80:
            raise ValueError("invalid_model_version")
        if type(history) is not int or type(horizon) is not int:
            raise ValueError("invalid_model_window")
        if not 1 <= history <= 86400 or not 1 <= horizon <= 86400:
            raise ValueError("invalid_model_window")

        def vector(name: str) -> tuple[float, ...]:
            raw = value.get(name)
            if not isinstance(raw, list) or len(raw) != len(FEATURE_NAMES):
                raise ValueError("invalid_" + name)
            if any(type(x) not in (int, float) or not isfinite(x) for x in raw):
                raise ValueError("invalid_" + name)
            return tuple(float(x) for x in raw)

        mean, scale, coefficients = (
            vector("feature_mean"),
            vector("feature_scale"),
            vector("coefficients"),
        )
        if any(x <= 0 for x in scale):
            raise ValueError("invalid_feature_scale")
        intercept = value.get("intercept_c")
        if (
            isinstance(intercept, bool)
            or not isinstance(intercept, (int, float))
            or not isfinite(intercept)
        ):
            raise ValueError("invalid_intercept")
        return cls(
            version, history, horizon, mean, scale, float(intercept), coefficients
        )

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": "c-ridge-v1",
            "model_version": self.model_version,
            "history_seconds": self.history_seconds,
            "horizon_seconds": self.horizon_seconds,
            "feature_names": list(FEATURE_NAMES),
            "feature_mean": list(self.feature_mean),
            "feature_scale": list(self.feature_scale),
            "intercept_c": self.intercept_c,
            "coefficients": list(self.coefficients),
        }

    def sha256(self) -> str:
        payload = json.dumps(
            self.to_json(), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return sha256(payload).hexdigest()


@dataclass(frozen=True)
class ForecastResult:
    status: str
    reason: str | None
    model_version: str
    parameter_sha256: str
    device_id: UUID
    warehouse_id: UUID
    as_of: datetime
    target_at: datetime
    predicted_temperature_c: float | None
    input_observation_ids: tuple[UUID, ...]
    input_first_sampled_at: datetime | None
    input_last_sampled_at: datetime | None

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "model_version": self.model_version,
            "parameter_sha256": self.parameter_sha256,
            "device_id": str(self.device_id),
            "warehouse_id": str(self.warehouse_id),
            "as_of": self.as_of.isoformat(),
            "target_at": self.target_at.isoformat(),
            "predicted_temperature_c": self.predicted_temperature_c,
            "input_observation_ids": [str(x) for x in self.input_observation_ids],
            "input_first_sampled_at": (
                self.input_first_sampled_at.isoformat()
                if self.input_first_sampled_at
                else None
            ),
            "input_last_sampled_at": (
                self.input_last_sampled_at.isoformat()
                if self.input_last_sampled_at
                else None
            ),
        }


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("aware_timestamp_required")
    return value.astimezone(UTC)


def _checked_points(
    window: ForecastWindowLike, history_seconds: int
) -> tuple[ForecastPointLike, ...]:
    as_of = _aware_utc(window.as_of)
    if len(window.points) < 2:
        raise ValueError("insufficient_forecast_points")
    ordered = sorted(window.points, key=lambda item: item.sampled_at)
    ids: set[UUID] = set()
    for point in ordered:
        sampled, received = _aware_utc(point.sampled_at), _aware_utc(point.received_at)
        if sampled > as_of or received > as_of:
            raise ValueError("future_forecast_input")
        if (as_of - sampled).total_seconds() > history_seconds:
            raise ValueError("outside_forecast_history")
        if not isinstance(point.observation_id, UUID) or point.observation_id in ids:
            raise ValueError("duplicate_forecast_observation")
        ids.add(point.observation_id)
        if type(point.temperature_c) not in (int, float) or not isfinite(
            point.temperature_c
        ):
            raise ValueError("invalid_forecast_temperature")
    return tuple(ordered)


def features(
    points: Sequence[ForecastPointLike], target_at: datetime
) -> tuple[float, ...]:
    """Four temperature-only features; timestamps and values are as-of inputs."""
    if len(points) < 2:
        raise ValueError("insufficient_forecast_points")
    times = [
        (_aware_utc(p.sampled_at) - _aware_utc(points[0].sampled_at)).total_seconds()
        for p in points
    ]
    values = [float(p.temperature_c) for p in points]
    mean_t, mean_y = sum(times) / len(times), sum(values) / len(values)
    denominator = sum((t - mean_t) ** 2 for t in times)
    slope = (
        sum((t - mean_t) * (y - mean_y) for t, y in zip(times, values, strict=True))
        / denominator
        if denominator > 0
        else 0.0
    )
    horizon_from_last = (
        _aware_utc(target_at) - _aware_utc(points[-1].sampled_at)
    ).total_seconds()
    recent = [
        y
        for p, y in zip(points, values, strict=True)
        if (
            _aware_utc(points[-1].sampled_at) - _aware_utc(p.sampled_at)
        ).total_seconds()
        <= 30
    ]
    return (
        values[-1],
        slope * horizon_from_last,
        sum(recent) / len(recent) - values[-1],
        max(values) - min(values),
    )


def last_value_baseline(points: Sequence[ForecastPointLike]) -> float:
    return float(points[-1].temperature_c)


def linear_trend_baseline(
    points: Sequence[ForecastPointLike], target_at: datetime
) -> float:
    vector = features(points, target_at)
    return vector[0] + vector[1]


def predict_temperature(
    window: ForecastWindowLike, parameters: RidgeParameters
) -> ForecastResult:
    """Return a traceable prediction only for a ready guarded window."""
    as_of, target_at = _aware_utc(window.as_of), _aware_utc(window.target_at)
    if (target_at - as_of).total_seconds() != parameters.horizon_seconds:
        raise ValueError("forecast_target_mismatch")
    if not isinstance(window.device_id, UUID) or not isinstance(
        window.warehouse_id, UUID
    ):
        raise ValueError("UUID_scope_required")
    digest = parameters.sha256()
    if window.status != "ready":
        return ForecastResult(
            "unavailable",
            window.status,
            parameters.model_version,
            digest,
            window.device_id,
            window.warehouse_id,
            as_of,
            target_at,
            None,
            (),
            None,
            None,
        )
    points = _checked_points(window, parameters.history_seconds)
    vector = features(points, target_at)
    prediction = parameters.intercept_c + sum(
        coefficient * (value - mean) / scale
        for coefficient, value, mean, scale in zip(
            parameters.coefficients,
            vector,
            parameters.feature_mean,
            parameters.feature_scale,
            strict=True,
        )
    )
    if not isfinite(prediction):
        raise ValueError("nonfinite_forecast_result")
    return ForecastResult(
        "predicted",
        None,
        parameters.model_version,
        digest,
        window.device_id,
        window.warehouse_id,
        as_of,
        target_at,
        prediction,
        tuple(p.observation_id for p in points),
        _aware_utc(points[0].sampled_at),
        _aware_utc(points[-1].sampled_at),
    )
