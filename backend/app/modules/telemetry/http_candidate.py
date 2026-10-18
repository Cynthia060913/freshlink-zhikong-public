"""Authenticated C read router over the isolated candidate schema.

The integration owner must mount this relative router only after the reviewed C
migration. It does not create tables or add the shared /api/v1 prefix.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.modules.telemetry.history_candidate import HistoryFiltersCandidate
from app.modules.telemetry.snapshot_candidate import (
    create_snapshot_candidate,
    render_snapshot_csv_candidate,
)
from app.modules.telemetry.snapshot_cursor_candidate import (
    read_signed_snapshot_page_candidate,
)
from app.modules.telemetry.storage_candidate import observations, receptions

router = APIRouter(prefix="/telemetry/observations", tags=["telemetry"])
_POLICY = "active-user-read-v1"


def _filters(
    device_id: UUID | None = None,
    warehouse_id: UUID | None = None,
    sampled_from: datetime | None = None,
    sampled_to: datetime | None = None,
    received_from: datetime | None = None,
    received_to: datetime | None = None,
    quality_in: Annotated[list[str] | None, Query()] = None,
) -> HistoryFiltersCandidate:
    return HistoryFiltersCandidate(
        device_id=device_id,
        warehouse_id=warehouse_id,
        sampled_from=sampled_from,
        sampled_to=sampled_to,
        received_from=received_from,
        received_to=received_to,
        quality_in=tuple(quality_in) if quality_in is not None else None,
    )


def _engine(session: SessionDep) -> Engine:
    bind = session.get_bind()
    if not isinstance(bind, Engine):
        raise HTTPException(503, detail={"reason_code": "storage_unavailable"})
    return bind


def _signing_key() -> bytes:
    # Reuse the application's private secret without publishing or logging it.
    # Key rotation is an integration concern; old cursors then become invalid.
    import hashlib
    import hmac

    return hmac.new(
        settings.SECRET_KEY.encode(), b"freshlink-c-snapshot-cursor-v1", hashlib.sha256
    ).digest()


def _failure(error: Exception) -> HTTPException:
    if isinstance(error, SQLAlchemyError):
        return HTTPException(
            503,
            detail={"reason_code": "storage_unavailable"},
            headers={"Retry-After": "30"},
        )
    reason = str(error)
    if reason == "snapshot_capacity":
        return HTTPException(
            429,
            detail={"reason_code": reason},
            headers={"Retry-After": "30"},
        )
    return HTTPException(422, detail={"reason_code": reason})


@router.get("/")
def list_observations(
    actor: CurrentUser,
    engine: Annotated[Engine, Depends(_engine)],
    filters: Annotated[HistoryFiltersCandidate, Depends(_filters)],
    snapshot_id: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Freeze a selection on first request; subsequent pages use signed cursors."""
    try:
        if snapshot_id is None and cursor is None:
            snapshot_id = create_snapshot_candidate(
                engine,
                actor_id=actor.id,
                filters=filters,
                policy_version=_POLICY,
                now=datetime.now(UTC),
            )
        page = read_signed_snapshot_page_candidate(
            engine,
            actor_id=actor.id,
            filters=filters,
            policy_version=_POLICY,
            now=datetime.now(UTC),
            signing_key=_signing_key(),
            key_id="c1",
            snapshot_id=snapshot_id,
            cursor=cursor,
            limit=limit,
        )
        return {
            "snapshot_id": page.snapshot_id,
            "items": page.items,
            "total": page.total,
            "next_cursor": page.next_cursor,
        }
    except (ValueError, SQLAlchemyError) as error:
        raise _failure(error) from None


@router.get("/export.csv")
def export_observations(
    actor: CurrentUser,
    engine: Annotated[Engine, Depends(_engine)],
    filters: Annotated[HistoryFiltersCandidate, Depends(_filters)],
    snapshot_id: UUID,
) -> Response:
    """Export exactly the authenticated snapshot membership and projection."""
    try:
        body = render_snapshot_csv_candidate(
            engine,
            snapshot_id=snapshot_id,
            actor_id=actor.id,
            filters=filters,
            policy_version=_POLICY,
            now=datetime.now(UTC),
        )
    except (ValueError, SQLAlchemyError) as error:
        raise _failure(error) from None
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="observations.csv"'},
    )


@router.get("/{observation_id}")
def read_observation(
    actor: CurrentUser,
    engine: Annotated[Engine, Depends(_engine)],
    observation_id: UUID,
) -> dict:
    """Expose exact raw and reception evidence to an active logged-in user."""
    _ = actor  # CurrentUser is evaluated on every request; no warehouse ACL exists yet.
    try:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    select(observations).where(observations.c.id == observation_id)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise HTTPException(404, detail={"reason_code": "observation_unknown"})
            attempts = (
                connection.execute(
                    select(receptions)
                    .where(receptions.c.observation_id == observation_id)
                    .order_by(receptions.c.received_at_utc, receptions.c.id)
                )
                .mappings()
                .all()
            )
        return {
            "observation_id": row["id"],
            "device_id": row["device_id"],
            "warehouse_id": row["warehouse_id"],
            "message_id": row["message_id"],
            "raw_text": row["raw_text"],
            "normalized": row["normalized"],
            "quality": row["quality"],
            "risk": row["risk"],
            "receptions": [
                {
                    "reception_id": attempt["id"],
                    "received_at": attempt["received_at_utc"],
                    "outcome": attempt["outcome"],
                    "reason": attempt["reason"],
                }
                for attempt in attempts
            ],
        }
    except SQLAlchemyError as error:
        raise _failure(error) from None
