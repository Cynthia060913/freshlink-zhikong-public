"""Bounded, privileged audit of rejected C receptions (unmounted candidate).

The UUID cursor is a stable keyset over saved rows, not a time ordering or a
frozen snapshot. A new row whose UUID sorts before the cursor needs a fresh
audit pass. Exact raw input is intentionally restricted to superusers until a
warehouse-scoped read policy is agreed.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, cast, literal, null, or_, select, union_all
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.sqltypes import Uuid

from app.api.deps import SessionDep, get_current_active_superuser
from app.models import User
from app.modules.telemetry.storage_candidate import (
    reception_reuse_conflicts,
    receptions,
)

router = APIRouter(prefix="/telemetry/receptions", tags=["telemetry"])


def _engine(session: SessionDep) -> Engine:
    bind = session.get_bind()
    if not isinstance(bind, Engine):
        raise HTTPException(503, detail={"reason_code": "storage_unavailable"})
    return bind


@router.get("/rejected")
def rejected_receptions(
    actor: Annotated[User, Depends(get_current_active_superuser)],
    engine: Annotated[Engine, Depends(_engine)],
    response: Response,
    device_id: UUID,
    warehouse_id: UUID,
    after: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    """Read exact rejected evidence for one trusted device/warehouse pair."""
    _ = actor
    original = receptions.alias("original_reception")
    direct = select(
        receptions.c.id.label("audit_id"),
        receptions.c.id.label("reception_id"),
        receptions.c.device_id,
        receptions.c.warehouse_id,
        cast(null(), Uuid(as_uuid=True)).label("original_device_id"),
        cast(null(), Uuid(as_uuid=True)).label("original_warehouse_id"),
        receptions.c.received_at_utc,
        receptions.c.raw_text,
        receptions.c.outcome,
        receptions.c.reason,
    ).where(
        receptions.c.device_id == device_id,
        receptions.c.warehouse_id == warehouse_id,
        receptions.c.outcome.in_(("invalid", "conflict")),
    )
    reused = (
        select(
            reception_reuse_conflicts.c.id.label("audit_id"),
            reception_reuse_conflicts.c.claimed_reception_id.label("reception_id"),
            reception_reuse_conflicts.c.device_id,
            reception_reuse_conflicts.c.warehouse_id,
            original.c.device_id.label("original_device_id"),
            original.c.warehouse_id.label("original_warehouse_id"),
            reception_reuse_conflicts.c.received_at_utc,
            reception_reuse_conflicts.c.raw_text,
            literal("conflict").label("outcome"),
            literal("reception_id_reuse").label("reason"),
        )
        .select_from(
            reception_reuse_conflicts.join(
                original,
                reception_reuse_conflicts.c.claimed_reception_id == original.c.id,
            )
        )
        .where(
            or_(
                and_(
                    reception_reuse_conflicts.c.device_id == device_id,
                    reception_reuse_conflicts.c.warehouse_id == warehouse_id,
                ),
                and_(
                    original.c.device_id == device_id,
                    original.c.warehouse_id == warehouse_id,
                ),
            )
        )
    )
    audit = union_all(direct, reused).subquery()
    query = select(audit).order_by(audit.c.audit_id).limit(limit + 1)
    if after is not None:
        query = query.where(audit.c.audit_id > after)
    try:
        with engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
    except SQLAlchemyError:
        raise HTTPException(
            503,
            detail={"reason_code": "storage_unavailable"},
            headers={"Retry-After": "30"},
        ) from None
    page = rows[:limit]
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [dict(row) for row in page],
        "next_after": page[-1]["audit_id"] if len(rows) > limit else None,
    }
