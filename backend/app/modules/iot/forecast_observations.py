"""Bounded, read-only PostgreSQL input for a future forecast service.

This internal adapter is not a route or an authorization boundary. A caller
must authenticate before using it and only pass ready windows to a model.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col

from app.modules.iot.forecast_window import (
    ForecastWindow,
    ForecastWindowPolicy,
    WindowStatus,
    build_forecast_window,
)
from app.modules.iot.models import Device
from app.modules.telemetry.storage_candidate import observations

ReadStatus = (
    WindowStatus
    | Literal[
        "unknown_device",
        "unbound_device",
        "unsupported_device",
        "warehouse_mismatch",
        "input_limit_exceeded",
    ]
)


class ForecastObservationUnavailable(RuntimeError):
    """Storage failed; no successful or normal forecast may be inferred."""


@dataclass(frozen=True)
class ForecastObservationRead:
    status: ReadStatus
    rows_read: int = 0
    device_connectivity: str | None = None
    window: ForecastWindow | None = None


def read_forecast_observations(
    engine: Engine,
    *,
    device_id: UUID,
    warehouse_id: UUID,
    as_of: datetime,
    policy: ForecastWindowPolicy = ForecastWindowPolicy(),
) -> ForecastObservationRead:
    """Read one device's normalized observations available at a UTC decision.

    Binding and rows share a repeatable-read, read-only transaction. Quality is
    never filtered in SQL: silently dropping bad rows could hide a blocked
    window. Capacity overflow also refuses input instead of hiding older rows.
    """
    if not isinstance(device_id, UUID) or not isinstance(warehouse_id, UUID):
        raise ValueError("UUID_scope_required")
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError("aware_decision_time_required")
    if not isinstance(policy, ForecastWindowPolicy):
        raise ValueError("forecast_policy_required")
    if engine.dialect.name != "postgresql":
        raise ValueError("postgresql_required")
    decision = as_of.astimezone(UTC)
    start = decision - timedelta(seconds=policy.history_seconds)
    try:
        with engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                device = (
                    connection.execute(
                        select(
                            col(Device.kind),
                            col(Device.warehouse_id),
                            col(Device.connectivity),
                        ).where(col(Device.id) == device_id)
                    )
                    .mappings()
                    .one_or_none()
                )
                if device is None:
                    return ForecastObservationRead("unknown_device")
                connectivity = device["connectivity"]
                if device["kind"] != "temperature_sensor":
                    return ForecastObservationRead(
                        "unsupported_device", device_connectivity=connectivity
                    )
                if device["warehouse_id"] is None:
                    return ForecastObservationRead(
                        "unbound_device", device_connectivity=connectivity
                    )
                if device["warehouse_id"] != warehouse_id:
                    return ForecastObservationRead(
                        "warehouse_mismatch", device_connectivity=connectivity
                    )
                rows = (
                    connection.execute(
                        select(
                            observations.c.id,
                            observations.c.sampled_at_utc,
                            observations.c.first_received_at_utc,
                            observations.c.normalized,
                            observations.c.quality,
                        )
                        .where(
                            observations.c.device_id == device_id,
                            observations.c.warehouse_id == warehouse_id,
                            observations.c.first_received_at_utc <= decision,
                            or_(
                                and_(
                                    observations.c.sampled_at_utc >= start,
                                    observations.c.sampled_at_utc <= decision,
                                ),
                                # Unknown sampling time in a recent arrival
                                # must block, not disappear behind SQL NULL.
                                and_(
                                    observations.c.sampled_at_utc.is_(None),
                                    observations.c.first_received_at_utc >= start,
                                ),
                            ),
                        )
                        .order_by(
                            observations.c.sampled_at_utc.desc().nulls_first(),
                            observations.c.first_received_at_utc.desc(),
                            observations.c.id.desc(),
                        )
                        .limit(policy.max_points + 1)
                    )
                    .mappings()
                    .all()
                )
    except SQLAlchemyError:
        raise ForecastObservationUnavailable(
            "forecast_observation_read_failed"
        ) from None
    if len(rows) > policy.max_points:
        return ForecastObservationRead("input_limit_exceeded", len(rows), connectivity)
    projected: list[dict[str, object]] = []
    for row in rows:
        normalized = row["normalized"]
        metrics = normalized.get("metrics") if isinstance(normalized, dict) else None
        projected.append(
            {
                "observation_id": str(row["id"]),
                "device_id": str(device_id),
                "warehouse_id": str(warehouse_id),
                "sampled_at": row["sampled_at_utc"],
                "received_at": row["first_received_at_utc"],
                "temperature_c": (
                    metrics.get("temperature") if isinstance(metrics, dict) else None
                ),
                "quality": row["quality"],
            }
        )
    window = build_forecast_window(
        projected,
        device_id=device_id,
        warehouse_id=warehouse_id,
        as_of=decision,
        policy=policy,
    )
    return ForecastObservationRead(window.status, len(rows), connectivity, window)
