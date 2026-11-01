"""Static bridge fixture mirroring PR103's frozen A input shape.

Run from backend: python -m mypy tests/typing/forecast_frozen_bridge.py --strict
The actual A module is unmerged and is intentionally not imported by C's PR.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.modules.telemetry.forecast_candidate import (
    ForecastResult,
    RidgeParameters,
    predict_temperature,
)

WindowStatus = Literal[
    "ready",
    "invalid_input",
    "quality_blocked",
    "insufficient_data",
    "stale_data",
    "gapped_data",
]


@dataclass(frozen=True)
class FrozenPoint:
    observation_id: UUID
    sampled_at: datetime
    received_at: datetime
    temperature_c: float


@dataclass(frozen=True)
class FrozenWindow:
    status: WindowStatus
    device_id: UUID
    warehouse_id: UUID
    as_of: datetime
    target_at: datetime
    eligible_count: int
    latest_sample_age_seconds: float | None
    points: tuple[FrozenPoint, ...]
    excluded: tuple[tuple[str, int], ...]


def call_frozen_window(
    window: FrozenWindow, parameters: RidgeParameters
) -> ForecastResult:
    return predict_temperature(window, parameters)
