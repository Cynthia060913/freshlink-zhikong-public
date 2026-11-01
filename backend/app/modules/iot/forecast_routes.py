"""Authenticated, disabled-by-default research preview; no notification entry."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.db import engine
from app.modules.iot.forecast_preview import (
    ForecastPreview,
    read_temperature_forecast_preview,
)
from app.modules.iot.models import Device

router = APIRouter(prefix="/devices", tags=["devices"])


def forecast_engine() -> Engine:
    return engine


def forecast_enabled() -> bool:
    return settings.FRESHLINK_FORECAST_PREVIEW_ENABLED


def forecast_clock() -> datetime:
    return datetime.now(UTC)


class ForecastPreviewStatus(BaseModel):
    enabled: bool


@router.get("/forecast-preview/status", response_model=ForecastPreviewStatus)
def preview_status(
    current_user: CurrentUser,
    enabled: Annotated[bool, Depends(forecast_enabled)],
) -> ForecastPreviewStatus:
    _ = current_user
    return ForecastPreviewStatus(enabled=enabled)


@router.get("/{device_id}/temperature-forecast", response_model=ForecastPreview)
def preview_temperature(
    device_id: UUID,
    session: SessionDep,
    current_user: CurrentUser,
    response: Response,
    source: Annotated[Engine, Depends(forecast_engine)],
    enabled: Annotated[bool, Depends(forecast_enabled)],
    decision: Annotated[datetime, Depends(forecast_clock)],
) -> ForecastPreview:
    """Use the server clock and registered binding, never caller-supplied scope."""
    _ = current_user
    response.headers["Cache-Control"] = "no-store"
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return read_temperature_forecast_preview(
        source, device=device, decision_at=decision, enabled=enabled
    )
