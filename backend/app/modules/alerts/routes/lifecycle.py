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

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.modules.alerts.models import (
    AlertLifecyclePublic,
    AlertStatusTransitionPublic,
)
from app.modules.alerts.services.events import (
    AlertAlreadyResolved,
    EventNotFound,
    acknowledge_alert,
    get_alert_status,
    get_event,
    list_status_transitions,
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
    except EventNotFound as exc:
        raise HTTPException(status_code=404, detail="alert not found") from exc
    except AlertAlreadyResolved as exc:
        raise HTTPException(
            status_code=409, detail="alert is already resolved"
        ) from exc
    return _lifecycle(session, alert_event_id, created=created)
