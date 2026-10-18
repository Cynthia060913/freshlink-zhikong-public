"""F03 / F04 ingestion service layer.

Pure-Python functions over a SQLModel Session; route handlers wrap
them and translate receipts into HTTP responses.

Idempotency contract (pinned in #5 6139896658, F03 shape per RC2):

- ``source_event_id`` is the stable idempotency key.
- Content identity is the canonical-payload sha256 digest — the
  canonical payload covers the full F03-RC2 traceability body
  (observation ids, rule/algorithm identity, severity, evidence), so
  replaying a key with different traceability is ``conflict``,
  never ``accepted_duplicate``.
- ``invalid`` is returned ONLY for explicit semantic rejections
  (unknown batch / warehouse, batch from another warehouse). It is
  final — the caller must stop retrying.
- ``retryable_failure`` covers transient storage errors on every
  phase: the batch/warehouse pre-read, the idempotency lookup, the
  INSERT, the post-integrity re-read and the post-commit refresh
  (F03-RC2: 读前置对象、幂等查找、写提交、并发冲突后读取和提交后
  读取均要考虑数据库故障). The caller must retry the SAME
  ``source_event_id`` with bounded backoff; a by-source lookup that
  answers ``not_found`` is NOT ``invalid`` and must not be treated
  as such.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, SQLModel, col, func, select

from app.modules.alerts.models import (
    AlertEvent,
    AlertStatus,
    AlertStatusTransition,
    Batch,
    CandidateKind,
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


def _invalid_receipt(source_event_id: str, reason_code: str) -> CandidateReceipt:
    return CandidateReceipt(
        status="invalid",  # type: ignore[arg-type]
        source_event_id=source_event_id,
        reason_code=reason_code,
    )


def _retryable_receipt(source_event_id: str) -> CandidateReceipt:
    return CandidateReceipt(
        status="retryable_failure",  # type: ignore[arg-type]
        source_event_id=source_event_id,
        reason_code="storage_unavailable",
        retryable=True,
        retry_after_s=_RETRY_AFTER_S,
    )


def _classify_existing(
    stored: AlertEvent, source_event_id: str, digest: str
) -> CandidateReceipt:
    """Duplicate-or-conflict classification shared by every read path."""

    if stored.content_digest == digest:
        return _duplicate_receipt(stored, source_event_id, digest)
    return _conflict_receipt(stored, source_event_id, digest)


def _insert_event(session: Session, row: AlertEvent, *extra: SQLModel) -> str | None:
    """INSERT one row (plus optional extra rows in the SAME
    transaction); return None on success, a failure tag on error.

    A unique-violation on ``source_event_id`` is a concurrent replay
    of the same key: fall back to the stored row and classify by
    digest (``"integrity"``). An ``OperationalError`` on the commit is
    a transient storage failure (``"operational"``). A refresh
    failure AFTER a successful commit is not a write failure — the
    row is stored — but the ORM object is expired and its attributes
    must not be read. Roll back the dead session and report
    ``"refresh"``: the caller answers ``retryable_failure`` and the
    client retries the SAME ``source_event_id``, which then classifies
    as ``accepted_duplicate`` (PR #10 round-2 review).

    Extra rows (e.g. a recovery's alert-status transition) are added
    before the single commit, so the event and its side effects land
    atomically or not at all (S0 f98 §3 decision 3).
    """

    session.add(row)
    for extra_row in extra:
        session.add(extra_row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return "integrity"
    except OperationalError:
        session.rollback()
        return "operational"
    try:
        session.refresh(row)
    except OperationalError:
        session.rollback()
        return "refresh"
    return None


def _candidate_row(payload: CandidateCreate, digest: str) -> AlertEvent:
    """Build the append-only row, persisting the F03-RC2 body."""

    return AlertEvent(
        source_event_id=payload.source_event_id,
        kind=str(payload.kind),
        warehouse_id=payload.warehouse_id,
        batch_id=payload.batch_id,
        metric_code=payload.metric_code,
        sampled_at=payload.sampled_at,
        value=payload.value,
        unit=payload.unit,
        severity=payload.severity,
        observation_ids_json=json.dumps(payload.observation_ids, ensure_ascii=False),
        rule_snapshot_id=payload.rule_snapshot_id,
        rule_version=payload.rule_version,
        algorithm_version=payload.algorithm_version,
        evidence_json=(
            json.dumps(payload.evidence, sort_keys=True, ensure_ascii=False)
            if payload.evidence is not None
            else None
        ),
        content_digest=digest,
        payload_json=canonical_payload_json(payload),
        related_source_event_id=payload.related_source_event_id,
    )


def get_alert_status(session: Session, alert_event_id: uuid.UUID) -> AlertStatus:
    """Current lifecycle status of one alert (S0 f98 §3 decision 3).

    The event row is immutable; status is the latest transition's
    ``to_status`` by ``(created_at, id)``, ``open`` while no
    transition exists.
    """

    row = session.exec(
        select(AlertStatusTransition)
        .where(col(AlertStatusTransition.alert_event_id) == alert_event_id)
        .order_by(
            col(AlertStatusTransition.created_at).desc(),
            col(AlertStatusTransition.id).desc(),
        )
        .limit(1)
    ).first()
    if row is None:
        return AlertStatus.OPEN
    return row.to_status


def list_status_transitions(
    session: Session, alert_event_id: uuid.UUID
) -> list[AlertStatusTransition]:
    """Full append-only transition history for one alert, oldest first."""

    return list(
        session.exec(
            select(AlertStatusTransition)
            .where(col(AlertStatusTransition.alert_event_id) == alert_event_id)
            .order_by(
                col(AlertStatusTransition.created_at), col(AlertStatusTransition.id)
            )
        ).all()
    )


def _recovery_transition(
    session: Session, payload: CandidateCreate, source_event_id: str
) -> AlertStatusTransition | None | str:
    """Validate a recovery candidate against its ORIGINAL anomaly and
    build the ``-> resolved`` transition (S0 f98 §3 decision 3).

    Returns the transition to commit alongside the recovery row,
    ``None`` when the original is already resolved (event lands, no
    redundant transition), a permanent ``invalid`` reason code, or the
    literal ``"retryable"`` when a read failed transiently. An unknown
    original is an EXPLICIT rejection (C keeps the recovery pending
    and re-posts after the original is accepted) — never a silently
    stored dangling link like device events, and never a manufactured
    "resolved" or closed alert.
    """

    related = payload.related_source_event_id
    if not related:
        return "recovery_missing_related"
    try:
        original = _get_by_source(session, related)
        status = get_alert_status(session, original.id) if original else None
    except OperationalError:
        session.rollback()
        return "retryable"
    if original is None:
        return "unknown_related_event"
    # The original must be the SAME alert: warehouse/batch/metric all
    # match, and it is an anomaly, not another recovery.
    if (
        original.warehouse_id != payload.warehouse_id
        or original.batch_id != payload.batch_id
        or original.metric_code != payload.metric_code
        or original.kind == str(CandidateKind.TEMPERATURE_RECOVERED)
    ):
        return "related_event_mismatch"
    assert status is not None
    if status == AlertStatus.RESOLVED:
        return None
    return AlertStatusTransition(
        alert_event_id=original.id,
        from_status=status,
        to_status=AlertStatus.RESOLVED,
        trigger_source_event_id=source_event_id,
        reason="temperature_recovered",
    )


def receive_candidate(session: Session, payload: CandidateCreate) -> CandidateReceipt:
    """Ingest one C-side alert candidate, five-way receipt envelope.

    Recovery candidates (``temperature_recovered``) additionally
    validate the referenced original anomaly and commit the original
    alert's ``open/acknowledged -> resolved`` transition in the same
    transaction as the recovery row.
    """

    source_event_id = payload.source_event_id
    digest = compute_content_digest(payload)

    try:
        batch = session.get(Batch, payload.batch_id)
    except OperationalError:
        session.rollback()
        return _retryable_receipt(source_event_id)
    if batch is None:
        return _invalid_receipt(source_event_id, "unknown_batch")
    # F03-RC2: the candidate names both warehouse and batch — the
    # batch must belong to that warehouse or the input is invalid.
    if batch.warehouse_id != payload.warehouse_id:
        return _invalid_receipt(source_event_id, "batch_warehouse_mismatch")

    try:
        existing = _get_by_source(session, source_event_id)
    except OperationalError:
        session.rollback()
        return _retryable_receipt(source_event_id)
    if existing is not None:
        return _classify_existing(existing, source_event_id, digest)

    transition: AlertStatusTransition | None = None
    if payload.kind is CandidateKind.TEMPERATURE_RECOVERED:
        outcome = _recovery_transition(session, payload, source_event_id)
        if isinstance(outcome, str):
            if outcome == "retryable":
                return _retryable_receipt(source_event_id)
            return _invalid_receipt(source_event_id, outcome)
        transition = outcome

    row = _candidate_row(payload, digest)
    failure = _insert_event(session, row, *([transition] if transition else ()))
    if failure in ("operational", "refresh"):
        return _retryable_receipt(source_event_id)
    if failure == "integrity":
        try:
            existing = _get_by_source(session, source_event_id)
        except OperationalError:
            session.rollback()
            return _retryable_receipt(source_event_id)
        if existing is None:
            # A non-key unique violation: nothing was stored, but there
            # is no caller input we could fix — classify retriable.
            return _retryable_receipt(source_event_id)
        return _classify_existing(existing, source_event_id, digest)
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

    try:
        if session.get(Warehouse, payload.warehouse_id) is None:
            return _invalid_receipt(source_event_id, "unknown_warehouse")
        if (
            payload.batch_id is not None
            and session.get(Batch, payload.batch_id) is None
        ):
            return _invalid_receipt(source_event_id, "unknown_batch")
    except OperationalError:
        session.rollback()
        return _retryable_receipt(source_event_id)

    try:
        existing = _get_by_source(session, source_event_id)
    except OperationalError:
        session.rollback()
        return _retryable_receipt(source_event_id)
    if existing is not None:
        return _classify_existing(existing, source_event_id, digest)

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
    if failure in ("operational", "refresh"):
        return _retryable_receipt(source_event_id)
    if failure == "integrity":
        try:
            existing = _get_by_source(session, source_event_id)
        except OperationalError:
            session.rollback()
            return _retryable_receipt(source_event_id)
        if existing is None:
            return _retryable_receipt(source_event_id)
        return _classify_existing(existing, source_event_id, digest)
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
