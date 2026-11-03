"""Read-only warehouse/batch grouping of existing disposition records.

Counts are records, not incidents, product loss, or a new risk decision. Recovery
and device lifecycle events do not open disposition records and are excluded,
exactly as in the existing alert list. No transaction commit, write, or dispatch.
"""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import and_, case, func, select, true
from sqlmodel import Session, col

from app.modules.alerts.models import AlertEvent, AlertStatus, Batch, Warehouse
from app.modules.alerts.services.events import _ALERT_EVENT_KINDS, _state_condition


class AlertGroup(BaseModel):
    warehouse_id: UUID | None
    warehouse_name: str | None
    batch_id: UUID | None
    batch_code: str | None
    product: str | None
    open_count: int
    acknowledged_count: int
    resolved_count: int
    total_count: int
    latest_created_at: datetime


class AlertGroupsPublic(BaseModel):
    data: list[AlertGroup]
    count: int
    queried_at: datetime


def list_alert_groups(session: Session, *, skip: int, limit: int) -> AlertGroupsPublic:
    # One statement gives page and total from the same database snapshot,
    # including an empty/out-of-range page. Never aggregate a truncated list.
    grouped = (
        select(
            col(AlertEvent.warehouse_id).label("warehouse_id"),
            col(Warehouse.name).label("warehouse_name"),
            col(AlertEvent.batch_id).label("batch_id"),
            col(Batch.code).label("batch_code"),
            col(Batch.product).label("product"),
            func.sum(case((_state_condition(AlertStatus.OPEN), 1), else_=0)).label(
                "open_count"
            ),
            func.sum(
                case((_state_condition(AlertStatus.ACKNOWLEDGED), 1), else_=0)
            ).label("acknowledged_count"),
            func.sum(case((_state_condition(AlertStatus.RESOLVED), 1), else_=0)).label(
                "resolved_count"
            ),
            func.count().label("total_count"),
            func.max(col(AlertEvent.created_at)).label("latest_created_at"),
        )
        .select_from(AlertEvent)
        .outerjoin(Warehouse, col(AlertEvent.warehouse_id) == col(Warehouse.id))
        .outerjoin(
            Batch,
            and_(
                col(AlertEvent.batch_id) == col(Batch.id),
                col(AlertEvent.warehouse_id) == col(Batch.warehouse_id),
            ),
        )
        .where(col(AlertEvent.kind).in_(_ALERT_EVENT_KINDS))
        .group_by(
            col(AlertEvent.warehouse_id),
            col(Warehouse.name),
            col(AlertEvent.batch_id),
            col(Batch.code),
            col(Batch.product),
        )
        .cte("alert_groups")
    )
    total = select(func.count().label("count")).select_from(grouped).cte("group_count")
    page = (
        select(grouped)
        .order_by(
            (grouped.c.open_count + grouped.c.acknowledged_count).desc(),
            grouped.c.latest_created_at.desc(),
            grouped.c.warehouse_id,
            grouped.c.batch_id,
        )
        .offset(skip)
        .limit(limit)
        .cte("group_page")
    )
    rows = (
        session.connection()
        .execute(select(total.c.count, page).select_from(total.outerjoin(page, true())))
        .mappings()
        .all()
    )
    return AlertGroupsPublic(
        count=int(rows[0]["count"]),
        queried_at=datetime.now(UTC),
        data=[
            AlertGroup.model_validate(row)
            for row in rows
            if row["total_count"] is not None
        ],
    )
