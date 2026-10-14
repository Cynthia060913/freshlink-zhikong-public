"""F03 / F04 ingestion HTTP routes.

Mounted prefix-free by the aggregator (see ``routes/__init__.py``);
the global ``/api/v1`` prefix is applied exactly once by A in
``app/main.py``. Final paths:

- ``POST /candidates``                       — C pushes alert candidates
- ``GET  /candidates/by-source/{sid}``      — idempotency lookup
- ``POST /device-events``                    — A pushes device events
- ``GET  /alert-events/{id}/notification-attempts`` — audit trail

Receipt → HTTP mapping (five-way contract, #5 6139896658):

- ``accepted_new``       → 201
- ``accepted_duplicate`` → 200
- ``conflict``            → 409 ``content_digest_mismatch``
- ``invalid``            → 422 (final; caller must stop retrying)
- ``retryable_failure``  → 503 + ``Retry-After: 30``

The by-source lookup answers 404 ``not_found`` for unseen keys: that
is NOT ``invalid`` — callers retry the same ``source_event_id``
with bounded backoff.

Auth note (first version, flagged for A review): ingestion is open
to any authenticated active user because C / A pipelines do not yet
carry service-to-service credentials; tightening to a dedicated
service token is F06 scope.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from app.api.deps import CurrentUser, SessionDep
from app.modules.alerts.models import (
    AlertEvent,
    AlertEventPublic,
    CandidateCreate,
    CandidateReceipt,
    DeviceEventCreate,
    NotificationAttempt,
    NotificationAttemptPublic,
)
from app.modules.alerts.services.events import (
    EventNotFound,
    get_event_by_source,
    list_attempts_for_event,
    receive_candidate,
    receive_device_event,
)

router = APIRouter(tags=["alert-events"])

# status → (http_code, retry_after header?)
_STATUS_MAP: dict[str, tuple[int, bool]] = {
    "accepted_new": (201, False),
    "accepted_duplicate": (200, False),
    "conflict": (409, False),
    "invalid": (422, False),
    "retryable_failure": (503, True),
}


def _receipt_response(
    receipt: CandidateReceipt, response: Response
) -> CandidateReceipt:
    http_code, with_retry_after = _STATUS_MAP[str(receipt.status)]
    response.status_code = http_code
    if with_retry_after and receipt.retry_after_s is not None:
        response.headers["Retry-After"] = str(int(receipt.retry_after_s))
    return receipt


@router.post("/candidates", response_model=CandidateReceipt)
def create_candidate(
    session: SessionDep,
    _current_user: CurrentUser,
    payload: CandidateCreate,
    response: Response,
) -> CandidateReceipt:
    receipt = receive_candidate(session, payload)
    _receipt_response(receipt, response)
    return receipt


@router.get(
    "/candidates/by-source/{source_event_id}",
    response_model=AlertEventPublic,
)
def read_candidate_by_source(
    session: SessionDep,
    _current_user: CurrentUser,
    source_event_id: str,
) -> AlertEventPublic:
    try:
        row = get_event_by_source(session, source_event_id)
    except EventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_public(row)


@router.post("/device-events", response_model=CandidateReceipt)
def create_device_event(
    session: SessionDep,
    _current_user: CurrentUser,
    payload: DeviceEventCreate,
    response: Response,
) -> CandidateReceipt:
    receipt = receive_device_event(session, payload)
    _receipt_response(receipt, response)
    return receipt


@router.get(
    "/alert-events/{event_id}/notification-attempts",
    response_model=list[NotificationAttemptPublic],
)
def list_attempts(
    session: SessionDep,
    _current_user: CurrentUser,
    event_id: uuid.UUID,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[NotificationAttemptPublic]:
    try:
        rows, _total = list_attempts_for_event(
            session, event_id, skip=skip, limit=limit
        )
    except EventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_attempt_to_public(r) for r in rows]


def _to_public(row: AlertEvent) -> AlertEventPublic:
    return AlertEventPublic(
        id=row.id,
        source_event_id=row.source_event_id,
        kind=row.kind,
        batch_id=row.batch_id,
        warehouse_id=row.warehouse_id,
        device_id=row.device_id,
        metric_code=row.metric_code,
        sampled_at=row.sampled_at,
        value=row.value,
        unit=row.unit,
        content_digest=row.content_digest,
        related_source_event_id=row.related_source_event_id,
        created_at=row.created_at,
    )


def _attempt_to_public(row: NotificationAttempt) -> NotificationAttemptPublic:
    return NotificationAttemptPublic(
        id=row.id,
        alert_event_id=row.alert_event_id,
        channel=row.channel,
        outcome=row.outcome,
        delivered=row.delivered,
        detail=row.detail,
        server_response=row.server_response,
        retry_after_s=row.retry_after_s,
        created_at=row.created_at,
    )
