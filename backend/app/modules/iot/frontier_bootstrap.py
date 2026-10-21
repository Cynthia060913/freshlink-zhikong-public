"""Explicit, caller-transaction fresh-origin candidate; never app startup.

Does not migrate/create tables, delete history, rotate an epoch, clear a hold,
start workers or authorize automatic recovery. C's complete write-gate coverage
and dispatch/recovery semantics remain independent activation prerequisites.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection
from sqlmodel import col

from app.models import User
from app.modules.alerts.models import Warehouse
from app.modules.iot.models import Device
from app.modules.iot.stream_frontier import FrontierUnavailable, _transaction
from app.modules.iot.stream_frontier_models import streams
from app.modules.iot.telemetry_inbox_models import queue_guard

# This is the current C inventory, not fallback C DDL. Missing/new C tables
# require the integration owner to reconcile the reviewed migration first.
C_EVIDENCE_TABLES = frozenset(
    {
        "telemetry_observation_candidate",
        "telemetry_quality_tag_candidate",
        "telemetry_reception_candidate",
        "telemetry_reception_reuse_candidate",
        "telemetry_evaluation_candidate",
        "telemetry_candidate_outbox_candidate",
        "telemetry_candidate_outbox_conflict_candidate",
        "telemetry_snapshot_candidate",
        "telemetry_snapshot_member_candidate",
        "telemetry_recovery_progress_candidate",
    }
)
A_EVIDENCE_TABLES = frozenset(
    {
        "iot_stream_admission",
        "iot_telemetry_inbox",
        "iot_telemetry_ingest_receipt",
        "iot_telemetry_ingress_audit",
    }
)


@dataclass(frozen=True)
class FreshOriginResult:
    created: bool
    epochs: tuple[tuple[UUID, UUID], ...]


def initialize_fresh_streams(
    connection: Connection,
    *,
    actor_id: UUID,
    bindings: Mapping[UUID, UUID],
    epochs: Mapping[UUID, UUID],
) -> FreshOriginResult:
    """Create one whole explicit 1-14 sensor scope, ONLY on verified empty data.

    Outer transaction owns all locks/insert/commit/rollback. Lock order is A
    inbox singleton -> stream/evidence tables -> sorted device rows. Do not
    call from a C write unit that already holds a stream lock. Operators must
    keep unreviewed/ungated legacy workers stopped; this does not enable them.

    Exact same-scope retries return existing epochs without changing evidence,
    holds or counters; that return is NOT a completeness/health certificate.
    Different scope/epochs, partially initialized state, or prior A/B/C evidence
    fails closed. No automatic incremental scope or historical reinitialization.
    """
    selected = dict(bindings)
    requested = dict(epochs)
    if (
        not isinstance(actor_id, UUID)
        or not 1 <= len(selected) <= 14
        or set(selected) != set(requested)
        or not all(
            isinstance(value, UUID) for pair in selected.items() for value in pair
        )
        or not all(isinstance(value, UUID) for value in requested.values())
    ):
        raise ValueError("explicit trusted device/binding/epoch UUID scope required")
    _transaction(connection)
    actor = connection.execute(
        select(col(User.is_active), col(User.is_superuser))
        .where(col(User.id) == actor_id)
        .with_for_update(read=True)
    ).one_or_none()
    if actor is None or not actor.is_active or not actor.is_superuser:
        raise FrontierUnavailable("fresh origin requires an active administrator")
    if connection.execute(
        select(queue_guard.c.id).with_for_update()
    ).scalars().all() != [1]:
        raise FrontierUnavailable("actual inbox singleton required")
    schema_map = connection.get_execution_options().get("schema_translate_map") or {}
    schema = schema_map.get(None) or inspect(connection).default_schema_name
    if not isinstance(schema, str) or not schema:
        raise FrontierUnavailable("explicit evidence schema required")
    tables = set(inspect(connection).get_table_names(schema=schema))
    required = (
        C_EVIDENCE_TABLES | A_EVIDENCE_TABLES | {"iot_stream_frontier", "alerts_event"}
    )
    if (
        not required <= tables
        or {name for name in tables if name.startswith("telemetry_")}
        != C_EVIDENCE_TABLES
    ):
        raise FrontierUnavailable("reviewed complete evidence schema required")
    quote = connection.dialect.identifier_preparer.quote
    qualified = {name: quote(schema) + "." + quote(name) for name in required}
    connection.exec_driver_sql(
        "LOCK TABLE "
        + ",".join(qualified[name] for name in sorted(required))
        + " IN SHARE ROW EXCLUSIVE MODE"
    )
    devices = connection.execute(
        select(col(Device.id), col(Device.warehouse_id), col(Device.kind))
        .where(col(Device.id).in_(selected))
        .order_by(col(Device.id))
        .with_for_update()
    ).all()
    if len(devices) != len(selected) or any(
        row.kind != "temperature_sensor" or row.warehouse_id != selected[row.id]
        for row in devices
    ):
        raise FrontierUnavailable("registered sensor scope/binding mismatch")
    warehouse_ids = set(
        connection.scalars(
            select(col(Warehouse.id)).where(
                col(Warehouse.id).in_(set(selected.values()))
            )
        )
    )
    if warehouse_ids != set(selected.values()):
        raise FrontierUnavailable("real B warehouse scope required")
    existing = (
        connection.execute(select(streams).order_by(streams.c.device_id))
        .mappings()
        .all()
    )
    ordered = tuple(sorted(requested.items(), key=lambda pair: pair[0].int))
    if existing:
        if len(existing) != len(selected) or any(
            row["device_id"] not in selected
            or not row["origin_verified"]
            or row["warehouse_id"] != selected[row["device_id"]]
            or row["stream_epoch"] != requested[row["device_id"]]
            for row in existing
        ):
            raise FrontierUnavailable("existing origin scope must not be rewritten")
        return FreshOriginResult(False, ordered)
    for name in sorted(required - {"iot_stream_frontier"}):
        if (
            connection.exec_driver_sql(
                "SELECT 1 FROM " + qualified[name] + " LIMIT 1"
            ).first()
            is not None
        ):
            raise FrontierUnavailable("prior A/B/C evidence forbids fresh origin")
    connection.execute(
        streams.insert(),
        [
            {
                "device_id": device,
                "warehouse_id": selected[device],
                "stream_epoch": epoch,
                "origin_verified": True,
                "admitted_seq": 0,
                "settled_through": 0,
                "evidence_revision": 0,
                "gap_reason": "",
            }
            for device, epoch in ordered
        ],
    )
    return FreshOriginResult(True, ordered)
