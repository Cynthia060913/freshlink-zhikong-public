"""Batch rule snapshot service layer.

Per B → A reply on Issue #5 (comment 6108448780), PR #10 review
round 2, and the S0 F02 contract signed in #5 comment 6127332268:

- A snapshot is **strictly append-only**. Once a row is committed,
  the service layer never UPDATEs or DELETEs any column. A future
  alembic phase installs a postgres ``BEFORE UPDATE / DELETE``
  trigger to enforce this at the DB layer too.
- Rule **selection** is by ``sampled_at`` against the half-open UTC
  effective interval ``[effective_start_at, effective_end_at)``
  (:func:`select_rules_for_observation`) — never "latest wins".
- Overlapping effective intervals for the same
  ``(batch_id, metric_code)`` are **rejected at write time**
  (:class:`SnapshotOverlapConflict`). To replace a predecessor
  without mutating it, the successor lists its snapshot id in
  ``supersedes_snapshot_ids`` — the link lives on the successor row
  and closes the predecessor's window from the successor's start.
- The signature is computed at insert time; duplicate
  ``(batch_id, signature)`` pairs are rejected with
  :class:`SnapshotConflict`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, col, func, or_, select

from app.modules.alerts.models import (
    BatchRuleSnapshot,
    BatchRuleSnapshotCreate,
    compute_snapshot_signature,
)
from app.modules.alerts.models.rule import _METRIC_CODE_RE
from app.modules.alerts.services.batches import get_batch
from app.modules.alerts.services.warehouses import WarehouseNotFound, get_warehouse


class SnapshotNotFound(Exception):
    """Raised when a snapshot id does not resolve to a row."""


class SnapshotConflict(Exception):
    """Raised when an identical (batch_id, signature) pair already exists."""

    def __init__(self, batch_id: uuid.UUID, signature: str) -> None:
        super().__init__(
            f"Snapshot signature already exists for batch {batch_id}: {signature[:12]}"
        )
        self.batch_id = batch_id
        self.signature = signature


class SnapshotOverlapConflict(Exception):
    """Raised when a new snapshot's effective interval overlaps an
    existing snapshot's interval on the same ``(batch_id, metric_code)``
    and the predecessor is not explicitly superseded (F02: the reader
    must never have to pick one of two overlapping rules)."""

    def __init__(
        self,
        batch_id: uuid.UUID,
        metric_code: str,
        existing_snapshot_id: uuid.UUID,
    ) -> None:
        super().__init__(
            f"rule_overlap_conflict: metric {metric_code!r} interval overlaps "
            f"snapshot {existing_snapshot_id} in batch {batch_id}"
        )
        self.batch_id = batch_id
        self.metric_code = metric_code
        self.existing_snapshot_id = existing_snapshot_id


def _payload_to_rules(payload: BatchRuleSnapshotCreate) -> list[dict[str, Any]]:
    """Drop pydantic-only fields and return plain JSON-able dicts."""

    return [r.model_dump(mode="json") for r in payload.rules]


def _payload_metric_codes(rules: list[dict[str, Any]]) -> set[str]:
    return {str(r["metric_code"]) for r in rules}


def _check_supersedes(
    session: Session,
    batch_id: uuid.UUID,
    supersedes: Sequence[uuid.UUID],
) -> None:
    """Every superseded snapshot must exist in the same batch."""

    for sid in supersedes:
        row = session.get(BatchRuleSnapshot, sid)
        if row is None:
            raise SnapshotNotFound(f"Snapshot {sid} not found")
        if row.batch_id != batch_id:
            raise SnapshotNotFound(
                f"Snapshot {sid} belongs to batch {row.batch_id}, not {batch_id}"
            )


def _check_overlap(
    session: Session,
    batch_id: uuid.UUID,
    payload: BatchRuleSnapshotCreate,
    new_metrics: set[str],
    *,
    signature: str,
) -> None:
    """Reject intervals overlapping a same-metric predecessor unless
    that predecessor is explicitly superseded by this payload.

    A row carrying OUR OWN signature is an identical replay, not a
    conflicting version: it is skipped here so the (stable) signature
    path classifies it. Otherwise a concurrent identical insert that
    loses the race could be mislabelled ``SnapshotOverlapConflict``
    depending on commit interleaving.
    """

    new_start = payload.effective_start_at
    new_end = payload.effective_end_at
    candidates = session.exec(
        select(BatchRuleSnapshot).where(
            BatchRuleSnapshot.batch_id == batch_id,
            col(BatchRuleSnapshot.effective_start_at)
            < (new_end if new_end is not None else datetime.max.replace(tzinfo=UTC)),
            or_(
                col(BatchRuleSnapshot.effective_end_at).is_(None),
                col(BatchRuleSnapshot.effective_end_at) > new_start,
            ),
        )
    ).all()
    exempt = set(payload.supersedes_snapshot_ids)
    for row in candidates:
        if row.id in exempt:
            continue
        if row.signature == signature:
            continue
        for existing_rule in parse_rules(row):
            if str(existing_rule.get("metric_code")) in new_metrics:
                raise SnapshotOverlapConflict(
                    batch_id, str(existing_rule["metric_code"]), row.id
                )


def create_snapshot(
    session: Session,
    batch_id: uuid.UUID,
    payload: BatchRuleSnapshotCreate,
) -> BatchRuleSnapshot:
    """Insert a new snapshot row for ``batch_id``.

    The service:

      1. Verifies the batch exists.
      2. Validates that every ``supersedes_snapshot_ids`` entry
         exists in the same batch.
      3. Computes the canonical signature; rejects an identical
         signature already present for the batch.
      4. Rejects effective-interval overlap on the same
         ``(batch_id, metric_code)`` unless the overlapping
         predecessor is explicitly superseded.
      5. INSERTs the new row. **No prior row is modified.**
    """

    get_batch(session, batch_id)
    rules = _payload_to_rules(payload)
    signature = compute_snapshot_signature(payload.version_id, rules)
    payload_json = json.dumps(rules, sort_keys=True, separators=(",", ":"))
    new_metrics = _payload_metric_codes(rules)

    _check_supersedes(session, batch_id, payload.supersedes_snapshot_ids)

    # Reject identical signature in the same batch.
    if session.exec(
        select(BatchRuleSnapshot).where(
            BatchRuleSnapshot.batch_id == batch_id,
            BatchRuleSnapshot.signature == signature,
        )
    ).first():
        raise SnapshotConflict(batch_id, signature)

    _check_overlap(session, batch_id, payload, new_metrics, signature=signature)

    new_row = BatchRuleSnapshot(
        batch_id=batch_id,
        version_id=payload.version_id,
        rule_version=payload.rule_version,
        payload_json=payload_json,
        signature=signature,
        effective_start_at=payload.effective_start_at,
        effective_end_at=payload.effective_end_at,
        supersedes_json=json.dumps(
            sorted(str(i) for i in payload.supersedes_snapshot_ids),
            sort_keys=True,
        ),
    )
    session.add(new_row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        # Re-check the conflict case in case another transaction beat
        # us to inserting the same signature between our pre-check
        # and the flush — surface the same domain error.
        if session.exec(
            select(BatchRuleSnapshot).where(
                BatchRuleSnapshot.batch_id == batch_id,
                BatchRuleSnapshot.signature == signature,
            )
        ).first():
            raise SnapshotConflict(batch_id, signature) from None
        raise
    session.refresh(new_row)
    return new_row


def get_current_snapshot(session: Session, batch_id: uuid.UUID) -> BatchRuleSnapshot:
    """Return the snapshot with the latest ``created_at`` for ``batch_id``.

    Raises :class:`SnapshotNotFound` if no snapshot exists yet. This
    is a pure read: nothing in this function (or any caller) is
    permitted to UPDATE a prior snapshot to make it "current".
    """

    get_batch(session, batch_id)
    row = session.exec(
        select(BatchRuleSnapshot)
        .where(BatchRuleSnapshot.batch_id == batch_id)
        .order_by(
            col(BatchRuleSnapshot.created_at).desc(), col(BatchRuleSnapshot.id).desc()
        )
        .limit(1)
    ).first()
    if row is None:
        raise SnapshotNotFound(f"No snapshot for batch {batch_id}")
    return row


def get_snapshot(session: Session, snapshot_id: uuid.UUID) -> BatchRuleSnapshot:
    row = session.get(BatchRuleSnapshot, snapshot_id)
    if row is None:
        raise SnapshotNotFound(f"Snapshot {snapshot_id} not found")
    return row


def list_snapshots_for_batch(
    session: Session,
    batch_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[BatchRuleSnapshot], int]:
    """All snapshots (current + superseded) for one batch, oldest first.

    Ordering by ``created_at`` then ``id`` keeps the result stable
    even when two snapshots share the same timestamp; uuid4 ids are
    NOT monotonic — the id is only a deterministic tie-break.
    """

    get_batch(session, batch_id)
    total = session.exec(
        select(func.count())
        .select_from(BatchRuleSnapshot)
        .where(BatchRuleSnapshot.batch_id == batch_id)
    ).one()
    rows = session.exec(
        select(BatchRuleSnapshot)
        .where(BatchRuleSnapshot.batch_id == batch_id)
        .order_by(col(BatchRuleSnapshot.created_at), col(BatchRuleSnapshot.id))
        .offset(skip)
        .limit(limit)
    ).all()
    return list(rows), int(total)


def parse_rules(row: BatchRuleSnapshot) -> list[dict[str, Any]]:
    """Return the rule dicts stored in a snapshot row."""

    loaded = json.loads(row.payload_json)
    if not isinstance(loaded, list):
        raise ValueError(f"Snapshot {row.id} payload is not a JSON list")
    return [r for r in loaded if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# F02: rule selection by sampled_at (B-side contract, #5 6127332268)
# ---------------------------------------------------------------------------


class RuleSelectionEntry(SQLModel):
    """One selected rule for one batch in a :class:`RuleSelection`."""

    batch_id: uuid.UUID
    warehouse_id: uuid.UUID
    rule_version: str
    snapshot_id: uuid.UUID
    metric_code: str
    unit: str | None
    kind: str
    severity: str
    comparison: Literal[">", "<", ">=", "<="]
    threshold: float
    recovery: dict[str, Any] | None
    effective_start_at: datetime
    effective_end_at: datetime | None
    source: str = "warehouse-manager-ui"


class RuleSelection(SQLModel):
    """Uniform envelope: always the same shape for every status.

    ``configured`` — one entry per matching (batch, rule);
    ``no_rule`` — ``rules == []`` (C must treat this as ``unknown``,
    never fall back to a "current" rule);
    ``invalid`` — permanent caller input error, ``reason_code`` set.
    """

    status: Literal["configured", "no_rule", "invalid"]
    rules: list[RuleSelectionEntry]
    reason_code: str | None = None


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _invalid(reason_code: str) -> RuleSelection:
    return RuleSelection(status="invalid", rules=[], reason_code=reason_code)


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; the DTO stored UTC instants,
    so a naive read-back is UTC by construction (A review 6110998037:
    never compare naive and aware directly)."""

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _derive_comparison(
    rule: dict[str, Any],
) -> tuple[Literal[">", "<", ">=", "<="], float] | None:
    """Derive ``(comparison, threshold)`` from a stored rule dict."""

    explicit = rule.get("comparison")
    if explicit in (">", "<", ">=", "<="):
        op = cast('Literal[">", "<", ">=", "<="]', explicit)
        if rule.get("threshold_high") is not None:
            return op, float(rule["threshold_high"])
        if rule.get("threshold_low") is not None:
            return op, float(rule["threshold_low"])
        return None
    if rule.get("threshold_high") is not None:
        return ">", float(rule["threshold_high"])
    if rule.get("threshold_low") is not None:
        return "<", float(rule["threshold_low"])
    return None


def load_supersedes(row: BatchRuleSnapshot) -> list[uuid.UUID]:
    """Parse the successor-side supersede links stored on ``row``."""

    try:
        links = json.loads(row.supersedes_json)
    except TypeError, ValueError:
        return []
    result: list[uuid.UUID] = []
    if isinstance(links, list):
        for item in links:
            try:
                result.append(uuid.UUID(str(item)))
            except ValueError:
                continue
    return result


def select_rules_for_observation(
    session: Session,
    *,
    warehouse_id: uuid.UUID,
    batch_ids: Sequence[uuid.UUID],
    metric_code: str,
    sampled_at: datetime,
) -> RuleSelection:
    """Select the historical rule version matching ``sampled_at`` (F02).

    Uniform envelope regardless of outcome (C's requirement: never
    mix list/dict shapes, never fall back to "current"):

    - ``invalid`` — naive ``sampled_at`` / unknown metric / unknown
      warehouse or batch / empty ``batch_ids``; permanent caller
      error, never retried as-is.
    - ``no_rule`` — no snapshot covers ``sampled_at`` for the
      requested metric; C must treat this as ``unknown``.
    - ``configured`` — one entry per batch whose half-open UTC
      interval ``[start, end)`` covers ``sampled_at``. Multi-batch is
      independent: each batch contributes its own entry with its own
      ``rule_version``. Write-side overlap rejection plus
      supersedes closure guarantees at most one entry per batch.
    """

    if not isinstance(metric_code, str) or not _METRIC_CODE_RE.fullmatch(
        metric_code.strip().lower()
    ):
        return _invalid("invalid_metric_code")
    metric = metric_code.strip().lower()

    if sampled_at.tzinfo is None or sampled_at.tzinfo.utcoffset(sampled_at) is None:
        return _invalid("naive_sampled_at")
    sampled_utc = sampled_at.astimezone(UTC)

    if not batch_ids:
        return _invalid("empty_batch_ids")

    try:
        get_warehouse(session, warehouse_id)
    except WarehouseNotFound:
        return _invalid("unknown_warehouse")

    # Validate every batch exists and belongs to the warehouse.
    for bid in batch_ids:
        try:
            batch = get_batch(session, bid)
        except Exception:  # noqa: BLE001 - BatchNotFound
            return _invalid("unknown_batch")
        if batch.warehouse_id != warehouse_id:
            return _invalid("unknown_batch")

    rows = session.exec(
        select(BatchRuleSnapshot)
        .where(
            col(BatchRuleSnapshot.batch_id).in_(list(batch_ids)),
            col(BatchRuleSnapshot.effective_start_at) <= sampled_utc,
            or_(
                col(BatchRuleSnapshot.effective_end_at).is_(None),
                col(BatchRuleSnapshot.effective_end_at) > sampled_utc,
            ),
        )
        .order_by(col(BatchRuleSnapshot.created_at), col(BatchRuleSnapshot.id))
    ).all()

    # Supersedes closure: a row whose id appears in a covering
    # successor's supersedes list is considered closed from that
    # successor's start. The link lives on the SUCCESSOR row
    # (append-only); this closure is a pure read-side computation.
    row_by_id = {row.id: row for row in rows}
    excluded: set[uuid.UUID] = set()
    for row in rows:
        for pred_id in load_supersedes(row):
            if pred_id in row_by_id and _as_utc(row.effective_start_at) <= sampled_utc:
                excluded.add(pred_id)

    entries: list[RuleSelectionEntry] = []
    for row in rows:
        if row.id in excluded:
            continue
        for rule in parse_rules(row):
            if str(rule.get("metric_code")) != metric:
                continue
            derived = _derive_comparison(rule)
            if derived is None:
                continue
            comparison, threshold = derived
            recovery = rule.get("recovery")
            entries.append(
                RuleSelectionEntry(
                    batch_id=row.batch_id,
                    warehouse_id=warehouse_id,
                    rule_version=row.rule_version or str(row.version_id),
                    snapshot_id=row.id,
                    metric_code=metric,
                    unit=rule.get("unit"),
                    kind=str(rule.get("candidate_kind")),
                    severity=str(rule.get("severity")),
                    comparison=comparison,
                    threshold=threshold,
                    recovery=recovery if isinstance(recovery, dict) else None,
                    effective_start_at=_as_utc(row.effective_start_at),
                    effective_end_at=(
                        _as_utc(row.effective_end_at)
                        if row.effective_end_at is not None
                        else None
                    ),
                )
            )

    if not entries:
        return RuleSelection(status="no_rule", rules=[])
    entries.sort(key=lambda e: (e.batch_id, e.snapshot_id))
    return RuleSelection(status="configured", rules=entries)
