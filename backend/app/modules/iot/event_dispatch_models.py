"""A delivery bookkeeping only; no import-time/shared metadata DDL.

The source event remains in iot_connectivity_event. These tables require a
reviewed migration on the unique shared chain; tests use private schemas only.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
)

metadata = MetaData()
checkpoints = Table(
    "iot_event_delivery_checkpoint",
    metadata,
    Column("source_event_id", Uuid(as_uuid=True), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("state", String(16), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("lease_id", Uuid(as_uuid=True)),
    Column("lease_until", DateTime(timezone=True)),
    Column("last_reason", String(64), nullable=False),
    Column("target_event_id", Uuid(as_uuid=True)),
    Column("completed_at", DateTime(timezone=True)),
    Column("manual_retry_count", Integer, nullable=False),
    CheckConstraint(
        "state IN ('queued','leased','blocked','exhausted','delivered')",
        name="ck_iot_event_dispatch_state",
    ),
    CheckConstraint(
        "attempt_count BETWEEN 0 AND 10 AND manual_retry_count >= 0",
        name="ck_iot_event_dispatch_attempt",
    ),
    CheckConstraint(
        "(state = 'leased' AND lease_id IS NOT NULL AND lease_until IS NOT NULL) OR "
        "(state <> 'leased' AND lease_id IS NULL AND lease_until IS NULL)",
        name="ck_iot_event_dispatch_lease",
    ),
    CheckConstraint(
        "state <> 'delivered' OR (target_event_id IS NOT NULL AND completed_at IS NOT NULL)",
        name="ck_iot_event_dispatch_receipt",
    ),
)
Index("ix_iot_event_dispatch_due", checkpoints.c.state, checkpoints.c.next_attempt_at)

retry_audit = Table(
    "iot_event_delivery_retry_audit",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("source_event_id", Uuid(as_uuid=True), nullable=False),
    Column("actor_id", Uuid(as_uuid=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("cycle", Integer, nullable=False),
    CheckConstraint("cycle >= 1", name="ck_iot_event_dispatch_retry_cycle"),
)
