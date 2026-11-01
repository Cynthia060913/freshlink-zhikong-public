"""Read-only model preview, separated from all alert and control paths."""

import json
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import Engine

from app.modules.iot.forecast_observations import (
    ForecastObservationUnavailable,
    read_forecast_observations,
)
from app.modules.iot.forecast_window import ForecastWindowPolicy
from app.modules.iot.models import Device
from app.modules.telemetry.forecast_candidate import (
    RidgeParameters,
    last_value_baseline,
    linear_trend_baseline,
    predict_temperature,
)

# Fixed C research artifact, not a newly trained or accepted warning policy.
MODEL_SOURCE = "34cad612dde789c85d257963b3dc3c2754f1bfad"
PARAMETER_SHA256 = "631c342435be05626f4ddafc648984091d9a317a803ffe2ee652d324ab5a7ec8"
POLICY_VERSION = "a-forecast-window-v1"
PARAMETER_FILE = Path(__file__).with_name("forecast_assets") / "c-ridge-v1.json"
MAX_PARAMETER_BYTES = 65536


class ForecastPreviewPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    observation_id: UUID
    sampled_at: datetime
    received_at: datetime
    temperature_c: float


class ForecastPreview(BaseModel):
    """One decision snapshot; unavailable is never a normal-risk conclusion."""

    status: Literal["predicted", "unavailable"] = "unavailable"
    reason: str | None = None
    experimental: Literal[True] = True
    business_warning_accepted: Literal[False] = False
    device_id: UUID
    warehouse_id: UUID | None = None
    decision_at: datetime
    target_at: datetime | None = None
    refresh_before: datetime | None = None
    device_connectivity: str | None = None
    model_source: str = MODEL_SOURCE
    model_version: str | None = None
    parameter_sha256: str | None = None
    policy_version: str = POLICY_VERSION
    input_points: list[ForecastPreviewPoint] = []
    predicted_temperature_c: float | None = None
    last_value_temperature_c: float | None = None
    linear_trend_temperature_c: float | None = None


class ForecastModelUnavailable(RuntimeError):
    pass


def load_forecast_parameters(path: Path = PARAMETER_FILE) -> RidgeParameters:
    """Load a bounded, pinned artifact; missing/corrupt/changed means unavailable."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_PARAMETER_BYTES + 1)
        if len(raw) > MAX_PARAMETER_BYTES:
            raise ValueError("oversized_parameters")
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError("parameter_object_required")
        parameters = RidgeParameters.from_json(document)
        if (
            parameters.sha256() != PARAMETER_SHA256
            or parameters.history_seconds != 120
            or parameters.horizon_seconds != 60
        ):
            raise ValueError("unreviewed_parameters")
        return parameters
    except OSError, ValueError, TypeError, OverflowError:
        # Never expose file paths, arbitrary JSON or parser errors in the API.
        raise ForecastModelUnavailable("model_unavailable") from None


def read_temperature_forecast_preview(
    engine: Engine,
    *,
    device: Device,
    decision_at: datetime,
    enabled: bool = False,
    parameters_path: Path = PARAMETER_FILE,
) -> ForecastPreview:
    """Read committed C data and invoke C math, without writes or dispatchers."""
    if decision_at.utcoffset() is None:
        raise ValueError("aware_decision_time_required")
    decision = decision_at.astimezone(UTC)
    empty = ForecastPreview(
        device_id=device.id, warehouse_id=device.warehouse_id, decision_at=decision
    )

    def unavailable(reason: str) -> ForecastPreview:
        return empty.model_copy(update={"reason": reason})

    if not enabled:
        return unavailable("preview_disabled")
    if device.kind != "temperature_sensor":
        return unavailable("unsupported_device")
    if device.warehouse_id is None:
        return unavailable("unbound_device")
    try:
        parameters = load_forecast_parameters(parameters_path)
    except ForecastModelUnavailable:
        return unavailable("model_unavailable")
    policy = ForecastWindowPolicy(
        history_seconds=parameters.history_seconds,
        horizon_seconds=parameters.horizon_seconds,
    )
    try:
        read = read_forecast_observations(
            engine,
            device_id=device.id,
            warehouse_id=device.warehouse_id,
            as_of=decision,
            policy=policy,
        )
    except ForecastObservationUnavailable:
        return unavailable("storage_unavailable")
    empty = empty.model_copy(update={"device_connectivity": read.device_connectivity})
    if read.status != "ready" or read.window is None:
        return unavailable(read.status)
    window = read.window
    try:
        prediction = predict_temperature(window, parameters)
        last = last_value_baseline(window.points)
        trend = linear_trend_baseline(window.points, window.target_at)
    except ValueError, TypeError, OverflowError:
        return unavailable("prediction_unavailable")
    if prediction.status != "predicted" or prediction.predicted_temperature_c is None:
        return unavailable("prediction_unavailable")
    if not all(
        isfinite(value) for value in (prediction.predicted_temperature_c, last, trend)
    ):
        return unavailable("prediction_unavailable")
    return ForecastPreview(
        status="predicted",
        device_id=device.id,
        warehouse_id=device.warehouse_id,
        decision_at=prediction.as_of,
        target_at=prediction.target_at,
        # The panel must expire even if its next HTTP refresh fails or is delayed.
        refresh_before=window.points[-1].sampled_at
        + timedelta(seconds=policy.max_age_seconds),
        device_connectivity=read.device_connectivity,
        model_version=prediction.model_version,
        parameter_sha256=prediction.parameter_sha256,
        input_points=[
            ForecastPreviewPoint(
                observation_id=point.observation_id,
                sampled_at=point.sampled_at,
                received_at=point.received_at,
                temperature_c=point.temperature_c,
            )
            for point in window.points
        ],
        predicted_temperature_c=prediction.predicted_temperature_c,
        last_value_temperature_c=last,
        linear_trend_temperature_c=trend,
    )
