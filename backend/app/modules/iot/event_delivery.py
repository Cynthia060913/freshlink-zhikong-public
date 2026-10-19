"""A outbox -> B ingestion checkpoint; never sends email or push.

One call attempts one already committed connectivity event. The receiver runs
outside A's transaction. If B commits but A cannot mark delivery, replay uses
the same immutable source/body and B resolves it as duplicate. This is not an
automatic retry scheduler or a formal migration/notification deployment.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.modules.alerts.models.event import (
    CandidateReceipt,
    DeviceEventCreate,
    compute_content_digest,
)
from app.modules.alerts.services.events import receive_device_event
from app.modules.iot.models import DeviceConnectivityEvent

SessionFactory = Callable[[], Session]
EventReceiver = Callable[[DeviceEventCreate], CandidateReceipt]
DeliveryStatus = Literal[
    "delivered",
    "already_delivered",
    "held_unbound",
    "retryable_failure",
    "blocked",
    "not_found",
]


@dataclass(frozen=True)
class DeliveryResult:
    source_event_id: UUID
    status: DeliveryStatus
    reason_code: str = ""
    target_event_id: UUID | None = None


@dataclass(frozen=True)
class EventSnapshot:
    source_event_id: UUID
    device_id: UUID
    warehouse_id: UUID
    occurred_at: datetime
    kind: str
    reason: str
    related_source_event_id: UUID | None

    @classmethod
    def from_row(cls, row: DeviceConnectivityEvent) -> EventSnapshot:
        if row.warehouse_id is None:
            raise ValueError("unbound_event")
        return cls(
            row.source_event_id,
            row.device_id,
            row.warehouse_id,
            row.occurred_at,
            row.kind,
            row.reason,
            row.related_source_event_id,
        )

    def payload(self) -> DeviceEventCreate:
        # B's current field is sampled_at, meaning this device event's occurrence
        # time. Do not substitute delivery/retry time. Original reason remains
        # in A's immutable historical event, reachable via device/source IDs;
        # B's current DTO has no reason field, so do not silently add/ignore it.
        return DeviceEventCreate.model_validate(
            {
                "source_event_id": str(self.source_event_id),
                "device_id": str(self.device_id),
                "warehouse_id": self.warehouse_id,
                "sampled_at": self.occurred_at,
                "kind": self.kind,
                "related_source_event_id": (
                    str(self.related_source_event_id)
                    if self.related_source_event_id
                    else None
                ),
            }
        )


def deliver_connectivity_event(
    session_factory: SessionFactory,
    receiver: EventReceiver,
    source_event_id: UUID,
) -> DeliveryResult:
    """Mark delivered only after B's matching durable receipt and A commit.

    Invalid/conflict or malformed receipts leave pending and return blocked:
    caller must surface the problem, not busy-loop it. Storage failures leave
    pending and return retryable. Unknown receiver exceptions propagate, never
    become success. Caller controls bounded attempts/backoff, not this helper.
    """
    try:
        with session_factory() as session:
            row = session.get(DeviceConnectivityEvent, source_event_id)
            if row is None:
                return DeliveryResult(source_event_id, "not_found")
            if row.delivery_state == "delivered":
                return DeliveryResult(source_event_id, "already_delivered")
            if row.delivery_state == "held_unbound" or row.warehouse_id is None:
                return DeliveryResult(source_event_id, "held_unbound")
            if row.delivery_state != "pending":
                return DeliveryResult(source_event_id, "blocked", "source_not_pending")
            snapshot = EventSnapshot.from_row(row)
            try:
                payload = snapshot.payload()
            except ValueError:
                return DeliveryResult(source_event_id, "blocked", "source_invalid")
            expected_digest = compute_content_digest(payload)
        # No A row lock or transaction is held across B's independently owned
        # transaction. Concurrent attempts are safe through B's source key.
        receipt = receiver(payload)
        if not isinstance(receipt, CandidateReceipt):
            return DeliveryResult(source_event_id, "blocked", "receipt_invalid")
        if receipt.status == "retryable_failure":
            return DeliveryResult(
                source_event_id, "retryable_failure", "target_unavailable"
            )
        if receipt.status not in ("accepted_new", "accepted_duplicate"):
            return DeliveryResult(source_event_id, "blocked", "target_rejected")
        target_id = receipt.event_id or receipt.stored_event_id
        if (
            receipt.source_event_id != str(source_event_id)
            or receipt.content_digest != expected_digest
            or not isinstance(target_id, UUID)
        ):
            return DeliveryResult(source_event_id, "blocked", "receipt_mismatch")
        with session_factory() as session:
            current = session.exec(
                select(DeviceConnectivityEvent)
                .where(DeviceConnectivityEvent.source_event_id == source_event_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).first()
            if current is None:
                return DeliveryResult(source_event_id, "blocked", "source_changed")
            if (
                current.warehouse_id is None
                or EventSnapshot.from_row(current) != snapshot
            ):
                return DeliveryResult(source_event_id, "blocked", "source_changed")
            if current.delivery_state == "delivered":
                return DeliveryResult(
                    source_event_id, "already_delivered", target_event_id=target_id
                )
            if current.delivery_state != "pending":
                return DeliveryResult(source_event_id, "blocked", "source_not_pending")
            current.delivery_state = "delivered"
            session.add(current)
            session.commit()
        return DeliveryResult(source_event_id, "delivered", target_event_id=target_id)
    except SQLAlchemyError:
        return DeliveryResult(
            source_event_id, "retryable_failure", "storage_unavailable"
        )


def deliver_to_local_alerts(engine: Engine, source_event_id: UUID) -> DeliveryResult:
    """Explicit local integration, with fresh A/B sessions; no public endpoint."""

    def receive(payload: DeviceEventCreate) -> CandidateReceipt:
        with Session(engine) as session:
            return receive_device_event(session, payload)

    return deliver_connectivity_event(lambda: Session(engine), receive, source_event_id)
