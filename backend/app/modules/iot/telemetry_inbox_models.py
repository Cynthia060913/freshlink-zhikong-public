"""A-owned durable transport tables, not registered in shared app metadata.

No import-time DDL. A reviewed migration must eventually create these tables
on the agreed shared chain. Only isolated tests currently create this metadata.
Raw business observations, quality and evaluation remain owned by C.
"""

from sqlalchemy import (
    BigInteger,
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

queue_guard = Table(
    "iot_telemetry_queue_guard",
    metadata,
    Column("id", Integer, primary_key=True),
    CheckConstraint("id = 1", name="ck_iot_telemetry_guard_singleton"),
)

inbox = Table(
    "iot_telemetry_inbox",
    metadata,
    Column("reception_id", Uuid(as_uuid=True), primary_key=True),
    Column("device_id", Uuid(as_uuid=True), nullable=False),
    Column("warehouse_id", Uuid(as_uuid=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("raw_text", String, nullable=False),
    Column("payload_bytes", Integer, nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("state", String(16), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("lease_id", Uuid(as_uuid=True)),
    Column("lease_until", DateTime(timezone=True)),
    Column("last_reason", String(64), nullable=False),
    Column("manual_retry_count", Integer, nullable=False),
    Column("last_retry_actor", Uuid(as_uuid=True)),
    CheckConstraint(
        "state IN ('queued','leased','blocked','exhausted')",
        name="ck_iot_telemetry_inbox_state",
    ),
    CheckConstraint(
        "attempt_count BETWEEN 0 AND 10 AND manual_retry_count >= 0",
        name="ck_iot_telemetry_inbox_attempts",
    ),
    CheckConstraint(
        "payload_bytes BETWEEN 1 AND 32768 AND octet_length(raw_text) = payload_bytes",
        name="ck_iot_telemetry_inbox_bytes",
    ),
    CheckConstraint(
        "(state = 'leased' AND lease_id IS NOT NULL AND lease_until IS NOT NULL) "
        "OR (state <> 'leased' AND lease_id IS NULL AND lease_until IS NULL)",
        name="ck_iot_telemetry_inbox_lease",
    ),
)
Index("ix_iot_telemetry_due", inbox.c.state, inbox.c.next_attempt_at)

receipts = Table(
    "iot_telemetry_ingest_receipt",
    metadata,
    Column("reception_id", Uuid(as_uuid=True), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("observation_id", Uuid(as_uuid=True)),
    Column("reason", String(64), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "outcome IN ('accepted_new','accepted_duplicate','invalid','conflict')",
        name="ck_iot_telemetry_receipt_outcome",
    ),
    CheckConstraint(
        "outcome NOT IN ('accepted_new','accepted_duplicate') OR observation_id IS NOT NULL",
        name="ck_iot_telemetry_receipt_stored",
    ),
)

audit = Table(
    "iot_telemetry_ingress_audit",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("device_id", Uuid(as_uuid=True)),
    Column("reception_id", Uuid(as_uuid=True)),
    Column("reason", String(64), nullable=False),
    Column("payload_bytes", BigInteger, nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("actor_id", Uuid(as_uuid=True)),
    CheckConstraint("payload_bytes >= 0", name="ck_iot_telemetry_audit_bytes"),
)
