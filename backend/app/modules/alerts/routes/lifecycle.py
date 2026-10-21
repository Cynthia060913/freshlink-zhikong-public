"""Alert lifecycle HTTP routes.

Mounted under ``/alerts`` and aggregated by
:mod:`app.modules.alerts.routes` (A mounts the aggregate in the shared
root; B never writes the global prefix here — see ``routes/__init__``).

- ``GET /alerts/{alert_event_id}/status`` — current status plus the
  full append-only transition history. Any active user.
- ``POST /alerts/{alert_event_id}/acknowledge`` — operator
  acknowledgement ``open -> acknowledged``. **Superuser only**:
  normal accounts stay read-only. Idempotent repeat returns the
  acknowledged state without a second transition row; acknowledging a
  resolved alert is rejected (409) with nothing written.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.modules.alerts.models import (
    AlertEventsPublic,
    AlertEventSummary,
    AlertLifecyclePublic,
    AlertStatus,
    AlertStatusTransitionPublic,
)
from app.modules.alerts.services.events import (
    AlertAlreadyResolved,
    AlertLifecycleUnavailable,
    EventNotFound,
    acknowledge_alert,
    get_alert_status,
    get_event,
    list_alert_events,
    list_status_transitions,
    statuses_for_events,
)

router = APIRouter(prefix="/alerts", tags=["alert-lifecycle"])


def _lifecycle(
    session: Session, alert_event_id: uuid.UUID, *, created: bool = False
) -> AlertLifecyclePublic:
    get_event(session, alert_event_id)  # 404 when unknown
    return AlertLifecyclePublic(
        alert_event_id=alert_event_id,
        status=get_alert_status(session, alert_event_id),
        transition_created=created,
        transitions=[
            AlertStatusTransitionPublic.model_validate(row)
            for row in list_status_transitions(session, alert_event_id)
        ],
    )


@router.get("/", response_model=AlertEventsPublic)
def list_alerts(
    session: SessionDep,
    _current_user: CurrentUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    state: AlertStatus | None = None,
    severity: str | None = None,
) -> AlertEventsPublic:
    """Disposition list: anomalies newest first, optional state/severity.

    Only events that open an alert appear here; recovery candidates and
    device lifecycle events are excluded (see ``list_alert_events``).
    """

    rows, total = list_alert_events(
        session, skip=skip, limit=limit, state=state, severity=severity
    )
    statuses = statuses_for_events(session, [row.id for row in rows])
    return AlertEventsPublic(
        data=[
            AlertEventSummary(
                alert_event_id=row.id,
                source_event_id=row.source_event_id,
                kind=row.kind,
                severity=row.severity,
                warehouse_id=row.warehouse_id,
                batch_id=row.batch_id,
                device_id=row.device_id,
                metric_code=row.metric_code,
                sampled_at=row.sampled_at,
                value=row.value,
                unit=row.unit,
                status=statuses.get(row.id, AlertStatus.OPEN),
                created_at=row.created_at,
            )
            for row in rows
        ],
        count=total,
    )


@router.get(
    "/{alert_event_id}/status",
    response_model=AlertLifecyclePublic,
)
def read_alert_status(
    session: SessionDep,
    _current_user: CurrentUser,
    alert_event_id: uuid.UUID,
) -> AlertLifecyclePublic:
    try:
        return _lifecycle(session, alert_event_id)
    except EventNotFound as exc:
        raise HTTPException(status_code=404, detail="alert not found") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(
            503,
            detail={"reason_code": "storage_unavailable"},
            headers={"Retry-After": "30"},
        ) from exc


@router.post(
    "/{alert_event_id}/acknowledge",
    response_model=AlertLifecyclePublic,
    dependencies=[Depends(get_current_active_superuser)],
)
def acknowledge_alert_endpoint(
    session: SessionDep,
    alert_event_id: uuid.UUID,
) -> AlertLifecyclePublic:
    try:
        _status, created = acknowledge_alert(session, alert_event_id)
        return _lifecycle(session, alert_event_id, created=created)
    except EventNotFound as exc:
        raise HTTPException(status_code=404, detail="alert not found") from exc
    except AlertAlreadyResolved as exc:
        raise HTTPException(
            status_code=409, detail="alert is already resolved"
        ) from exc
    except (AlertLifecycleUnavailable, SQLAlchemyError) as exc:
        # The service may have refused caller-owned pending writes before
        # entering its unit. Do not roll back those unrelated changes here.
        if isinstance(exc, SQLAlchemyError):
            session.rollback()
        raise HTTPException(
            503,
            detail={"reason_code": "storage_unavailable"},
            headers={"Retry-After": "30"},
        ) from exc
