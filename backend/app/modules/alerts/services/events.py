"""F03 / F04 ingestion service layer.

Pure-Python functions over a SQLModel Session; route handlers wrap
them and translate receipts into HTTP responses.

Idempotency contract (pinned in #5 6139896658):

- ``source_event_id`` is the stable idempotency key.
- Content identity is the canonical-payload sha256 digest. A replay
  with identical content is ``accepted_duplicate``; a replay with
  different content under the same key is ``conflict``.
- ``invalid`` is returned ONLY for explicit semantic rejections
  (unknown batch / warehouse). It is final — the caller must stop
  retrying.
- ``retryable_failure`` covers transient storage errors. The caller
  must retry the SAME ``source_event_id`` with bounded backoff; a
  by-source lookup that answers ``not_found`` is NOT ``invalid`` and
  must not be treated as such.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, col, func, select

from app.modules.alerts.models import (
    AlertEvent,
    Batch,
    NotificationAttempt,
    Warehouse,
)
from app.modules.alerts.models.event import (
    CandidateCreate,
    CandidateReceipt,
    DeviceEventCreate,
    canonical_payload_json,
    compute_content_digest,
)
from app.modules.alerts.notifications.transport import NotificationResult

_RETRY_AFTER_S = 30.0


class EventNotFound(Exception):
    """Raised when a source_event_id / event id does not resolve."""


def _get_by_source(session: Session, source_event_id: str) -> AlertEvent | None:
    return session.exec(
        select(AlertEvent).where(col(AlertEvent.source_event_id) == source_event_id)
    ).first()


def _duplicate_receipt(
    stored: AlertEvent, source_event_id: str, digest: str
) -> CandidateReceipt:
    return CandidateReceipt(
        status="accepted_duplicate",  # type: ignore[arg-type]
        event_id=stored.id,
        source_event_id=source_event_id,
        content_digest=digest,
        stored_event_id=stored.id,
        created_at=stored.created_at,
    )


def _conflict_receipt(
    stored: AlertEvent, source_event_id: str, digest: str
) -> CandidateReceipt:
    return CandidateReceipt(
        status="conflict",  # type: ignore[arg-type]
        source_event_id=source_event_id,
        content_digest=digest,
        reason_code="content_digest_mismatch",
        stored_event_id=stored.id,
        created_at=stored.created_at,
    )


def _retryable_receipt(source_event_id: str) -> CandidateReceipt:
    return CandidateReceipt(
        status="retryable_failure",  # type: ignore[arg-type]
        source_event_id=source_event_id,
        reason_code="storage_unavailable",
        retryable=True,
        retry_after_s=_RETRY_AFTER_S,
    )


def _insert_event(session: Session, row: AlertEvent) -> str | None:
    """INSERT one row; return None on success, a failure tag on error.

    A unique-violation on ``source_event_id`` is a concurrent replay
    of the same key: fall back to the stored row and classify by
    digest (``"integrity"``). An ``OperationalError`` is a transient
    storage failure (``"operational"``).
    """

    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return "integrity"
    except OperationalError:
        session.rollback()
        return "operational"
    session.refresh(row)
    return None


def receive_candidate(session: Session, payload: CandidateCreate) -> CandidateReceipt:
    """Ingest one C-side alert candidate, five-way receipt envelope."""

    source_event_id = payload.source_event_id
    digest = compute_content_digest(payload)

    batch = session.get(Batch, payload.batch_id)
    if batch is None:
        return CandidateReceipt(
            status="invalid",  # type: ignore[arg-type]
            source_event_id=source_event_id,
            reason_code="unknown_batch",
        )

    existing = _get_by_source(session, source_event_id)
    if existing is not None:
        if existing.content_digest == digest:
            return _duplicate_receipt(existing, source_event_id, digest)
        return _conflict_receipt(existing, source_event_id, digest)

    row = AlertEvent(
        source_event_id=source_event_id,
        kind=str(payload.kind),
        batch_id=payload.batch_id,
        metric_code=payload.metric_code,
        sampled_at=payload.sampled_at,
        value=payload.value,
        unit=payload.unit,
        content_digest=digest,
        payload_json=canonical_payload_json(payload),
    )
    failure = _insert_event(session, row)
    if failure == "operational":
        return _retryable_receipt(source_event_id)
    if failure == "integrity":
        existing = _get_by_source(session, source_event_id)
        if existing is None:
            # A non-key unique violation: no reason to classify as
            # retryable, but nothing was stored either.
            return _retryable_receipt(source_event_id)
        if existing.content_digest == digest:
            return _duplicate_receipt(existing, source_event_id, digest)
        return _conflict_receipt(existing, source_event_id, digest)
    return CandidateReceipt(
        status="accepted_new",  # type: ignore[arg-type]
        event_id=row.id,
        source_event_id=source_event_id,
        content_digest=digest,
        created_at=row.created_at,
    )


def receive_device_event(
    session: Session, payload: DeviceEventCreate
) -> CandidateReceipt:
    """Ingest one A-side device lifecycle event (offline/fault/recovered).

    A recovery is a brand-new ``source_event_id`` carrying
    ``related_source_event_id`` to the original event. Existence of
    the referenced original is NOT enforced at insert time: events
    may legitimately arrive out of order, and rejecting a recovery
    would lose it. Dangling links are tolerated on read.
    """

    source_event_id = payload.source_event_id
    digest = compute_content_digest(payload)

    if session.get(Warehouse, payload.warehouse_id) is None:
        return CandidateReceipt(
            status="invalid",  # type: ignore[arg-type]
            source_event_id=source_event_id,
            reason_code="unknown_warehouse",
        )
    if payload.batch_id is not None and session.get(Batch, payload.batch_id) is None:
        return CandidateReceipt(
            status="invalid",  # type: ignore[arg-type]
            source_event_id=source_event_id,
            reason_code="unknown_batch",
        )

    existing = _get_by_source(session, source_event_id)
    if existing is not None:
        if existing.content_digest == digest:
            return _duplicate_receipt(existing, source_event_id, digest)
        return _conflict_receipt(existing, source_event_id, digest)

    row = AlertEvent(
        source_event_id=source_event_id,
        kind=str(payload.kind),
        warehouse_id=payload.warehouse_id,
        device_id=payload.device_id,
        batch_id=payload.batch_id,
        sampled_at=payload.sampled_at,
        content_digest=digest,
        payload_json=canonical_payload_json(payload),
        related_source_event_id=payload.related_source_event_id,
    )
    failure = _insert_event(session, row)
    if failure == "operational":
        return _retryable_receipt(source_event_id)
    if failure == "integrity":
        existing = _get_by_source(session, source_event_id)
        if existing is None:
            return _retryable_receipt(source_event_id)
        if existing.content_digest == digest:
            return _duplicate_receipt(existing, source_event_id, digest)
        return _conflict_receipt(existing, source_event_id, digest)
    return CandidateReceipt(
        status="accepted_new",  # type: ignore[arg-type]
        event_id=row.id,
        source_event_id=source_event_id,
        content_digest=digest,
        created_at=row.created_at,
    )


def get_event_by_source(session: Session, source_event_id: str) -> AlertEvent:
    """Fetch one event by idempotency key or raise :class:`EventNotFound`."""

    row = _get_by_source(session, source_event_id)
    if row is None:
        raise EventNotFound(f"not_found: {source_event_id}")
    return row


def get_event(session: Session, event_id: uuid.UUID) -> AlertEvent:
    row = session.get(AlertEvent, event_id)
    if row is None:
        raise EventNotFound(f"not_found: event {event_id}")
    return row


def record_notification_attempt(
    session: Session,
    alert_event_id: uuid.UUID,
    result: NotificationResult,
) -> NotificationAttempt:
    """Persist one transport outcome as an append-only attempt row."""

    if session.get(AlertEvent, alert_event_id) is None:
        raise EventNotFound(f"not_found: event {alert_event_id}")
    attempt = NotificationAttempt(
        alert_event_id=alert_event_id,
        channel=str(result.transport),
        outcome=str(result.outcome),
        delivered=result.delivered,
        detail=(result.detail or None),
        server_response=result.server_response,
        retry_after_s=result.retry_after_s,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


def list_attempts_for_event(
    session: Session, alert_event_id: uuid.UUID, *, skip: int = 0, limit: int = 50
) -> tuple[list[NotificationAttempt], int]:
    """Return (rows, total_count) for one event, newest first."""

    if session.get(AlertEvent, alert_event_id) is None:
        raise EventNotFound(f"not_found: event {alert_event_id}")
    total = session.exec(
        select(func.count())
        .select_from(NotificationAttempt)
        .where(col(NotificationAttempt.alert_event_id) == alert_event_id)
    ).one()
    rows = session.exec(
        select(NotificationAttempt)
        .where(col(NotificationAttempt.alert_event_id) == alert_event_id)
        .order_by(col(NotificationAttempt.created_at), col(NotificationAttempt.id))
        .offset(skip)
        .limit(limit)
    ).all()
    return list(rows), int(total)
