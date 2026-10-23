"""Explicit A→C gated storage adapter; no startup, DDL or recovery sender.

The delivery worker invokes this sink outside its inbox transaction. C writes
and A evidence revision share one new transaction; the receipt escapes only
after commit. A's later terminal checkpoint remains a separate transaction.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col

from app.modules.alerts.models.warehouse import Warehouse
from app.modules.iot.ingress import ReceptionEnvelope
from app.modules.iot.models import Device
from app.modules.iot.stream_frontier import (
    FrontierUnavailable,
    begin_evidence_write,
    mark_uncertain,
)
from app.modules.telemetry.storage_candidate import (
    IngestResult,
    ingest_observation_on_connection_candidate,
    observations,
)


@dataclass
class _CReception:
    """Fresh mutable projection for C; original A envelope stays frozen."""

    raw_text: str
    authenticated_device_id: UUID
    warehouse_id: UUID
    received_at: datetime
    reception_id: UUID
    raw_payload: dict[str, Any]


class GatedTelemetrySink:
    """Opt-in sink for reviewed, explicitly initialized device/epoch scope.

    All other C writers still need their own same-Connection gate. This class
    does not certify a recovery window or permit automatic recovery dispatch.
    Broker authentication/revocation remains the receiver's responsibility.
    """

    def __init__(self, engine: Engine, *, frontier_epochs: Mapping[UUID, UUID]):
        epochs = dict(frontier_epochs)
        if not 1 <= len(epochs) <= 14 or not all(
            isinstance(device, UUID) and isinstance(epoch, UUID)
            for device, epoch in epochs.items()
        ):
            raise ValueError("1-14 explicit trusted device/epoch UUIDs required")
        self.engine = engine
        self._epochs = epochs

    def __call__(self, envelope: ReceptionEnvelope) -> IngestResult:
        identity = envelope.authenticated_device_id
        epoch = self._epochs.get(identity)
        if epoch is None:
            return IngestResult(
                "retryable_failure",
                envelope.reception_id,
                reason="unverified_stream_scope",
            )
        try:
            with self.engine.begin() as connection:
                # Locks stream first, verifies epoch/current binding, advances
                # evidence revision on THIS transaction before any C evidence.
                begin_evidence_write(
                    connection,
                    device_id=identity,
                    warehouse_id=envelope.warehouse_id,
                    epoch=epoch,
                )
                # Hold binding and real warehouse against changes/deletion
                # through the C commit, without another Session/connection.
                device = connection.execute(
                    select(col(Device.warehouse_id), col(Device.kind))
                    .where(col(Device.id) == identity)
                    .with_for_update(read=True)
                ).one_or_none()
                if (
                    device is None
                    or device.warehouse_id != envelope.warehouse_id
                    or device.kind != "temperature_sensor"
                ):
                    raise FrontierUnavailable("current sensor binding mismatch")
                warehouse = connection.execute(
                    select(col(Warehouse.id))
                    .where(col(Warehouse.id) == envelope.warehouse_id)
                    .with_for_update(read=True, key_share=True)
                ).scalar_one_or_none()
                if warehouse is None:
                    raise FrontierUnavailable("current warehouse missing")
                view = _CReception(
                    envelope.raw_text,
                    envelope.authenticated_device_id,
                    envelope.warehouse_id,
                    envelope.received_at,
                    envelope.reception_id,
                    envelope.raw_payload,
                )
                result = ingest_observation_on_connection_candidate(connection, view)
                if result.status not in {
                    "accepted_new",
                    "accepted_duplicate",
                    "invalid",
                    "conflict",
                }:
                    raise FrontierUnavailable("C did not stage a terminal receipt")
                quality = (
                    connection.execute(
                        select(observations.c.quality).where(
                            observations.c.id == result.observation_id
                        )
                    ).scalar_one()
                    if result.observation_id is not None
                    else None
                )
                if result.status in {"invalid", "conflict"} or quality != ["ok"]:
                    mark_uncertain(connection, device_id=identity, epoch=epoch)
            # Context manager has committed. Commit failure is caught below;
            # no provisional C success is returned to the delivery worker.
            return result
        except SQLAlchemyError, FrontierUnavailable:
            return IngestResult(
                "retryable_failure",
                envelope.reception_id,
                reason="gated_storage_unavailable",
            )
