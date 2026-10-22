"""Batch rule snapshot HTTP routes.

Mounted under ``/batches/{bid}/rule-snapshots``. Writes are
superuser-only; reads are open to active users. Snapshots are
strictly append-only: there is no UPDATE or DELETE handler and
the model carries no ``is_current`` / ``superseded_by`` columns.
The "current snapshot" for a batch is the row with the latest
``created_at``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.modules.alerts.models import (
    BatchRuleSnapshot,
    BatchRuleSnapshotCreate,
    BatchRuleSnapshotPublic,
)
from app.modules.alerts.services.batches import BatchNotFound
from app.modules.alerts.services.rules import (
    SnapshotConflict,
    SnapshotNotFound,
    SnapshotOverlapConflict,
    create_snapshot,
    get_current_snapshot,
    get_snapshot,
    list_snapshots_for_batch,
    load_supersedes,
    parse_rules,
)

router = APIRouter(tags=["rule-snapshots"])


def _to_public(row: BatchRuleSnapshot) -> BatchRuleSnapshotPublic:
    """Re-hydrate ``payload_json`` for the public response.

    The public DTO carries no ``is_current`` / ``superseded_by``
    fields. Whether a snapshot is the current one is determined by
    comparing the row to the GET-current response, not by reading
    a stored flag.
    """

    return BatchRuleSnapshotPublic(
        id=row.id,
        batch_id=row.batch_id,
        version_id=row.version_id,
        rule_version=row.rule_version,
        rules=parse_rules(row),
        signature=row.signature,
        effective_start_at=row.effective_start_at,
        effective_end_at=row.effective_end_at,
        supersedes_snapshot_ids=load_supersedes(row),
        created_at=row.created_at,
    )


@router.post(
    "/batches/{batch_id}/rule-snapshots",
    response_model=BatchRuleSnapshotPublic,
    status_code=201,
    dependencies=[Depends(get_current_active_superuser)],
)
def create_snapshot_endpoint(
    session: SessionDep,
    batch_id: uuid.UUID,
    payload: BatchRuleSnapshotCreate,
) -> BatchRuleSnapshotPublic:
    try:
        row = create_snapshot(session, batch_id, payload)
    except BatchNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SnapshotConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Snapshot signature already exists for batch "
                f"{exc.batch_id}: {exc.signature[:12]}"
            ),
        ) from exc
    except SnapshotOverlapConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"rule_overlap_conflict: metric {exc.metric_code!r} overlaps "
                f"snapshot {exc.existing_snapshot_id}; supersede it explicitly "
                "to replace it"
            ),
        ) from exc
    return _to_public(row)


@router.get(
    "/batches/{batch_id}/rule-snapshots",
    response_model=list[BatchRuleSnapshotPublic],
)
def list_snapshots_endpoint(
    session: SessionDep,
    _current_user: CurrentUser,
    batch_id: uuid.UUID,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[BatchRuleSnapshotPublic]:
    try:
        rows, _total = list_snapshots_for_batch(
            session, batch_id, skip=skip, limit=limit
        )
    except BatchNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_to_public(r) for r in rows]


@router.get(
    "/batches/{batch_id}/rule-snapshots/current",
    response_model=BatchRuleSnapshotPublic,
)
def read_current_snapshot(
    session: SessionDep,
    _current_user: CurrentUser,
    batch_id: uuid.UUID,
) -> BatchRuleSnapshotPublic:
    try:
        row = get_current_snapshot(session, batch_id)
    except BatchNotFound as exc:
        # get_current_snapshot verifies the batch exists first; a
        # missing batch must be 404, not an unhandled 500.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SnapshotNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_public(row)


@router.get(
    "/rule-snapshots/{snapshot_id}",
    response_model=BatchRuleSnapshotPublic,
)
def read_snapshot(
    session: SessionDep,
    _current_user: CurrentUser,
    snapshot_id: uuid.UUID,
) -> BatchRuleSnapshotPublic:
    try:
        row = get_snapshot(session, snapshot_id)
    except SnapshotNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_public(row)
