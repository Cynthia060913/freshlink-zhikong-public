"""Frozen A transport DDL candidate; NOT in Alembic's active versions directory.

The proposed parent must be confirmed with B/C before promotion. Importing this
file does not connect, register metadata, create tables or change any revision.
"""

import sqlalchemy as sa
from alembic import op

revision = "a04_transport_storage"
down_revision = "b01_alerts_storage"  # Proposed order, not an activated chain.
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "iot_telemetry_queue_guard",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.CheckConstraint("id = 1", name="ck_iot_telemetry_guard_singleton"),
    )
    # Capacity checks lock this singleton; an empty table is not equivalent.
    op.execute(sa.text("INSERT INTO iot_telemetry_queue_guard (id) VALUES (1)"))
    op.create_table(
        "iot_telemetry_inbox",
        sa.Column("reception_id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_text", sa.String(), nullable=False),
        sa.Column("payload_bytes", sa.Integer(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_id", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_reason", sa.String(64), nullable=False),
        sa.Column("manual_retry_count", sa.Integer(), nullable=False),
        sa.Column("last_retry_actor", sa.Uuid()),
        sa.CheckConstraint(
            "state IN ('queued','leased','blocked','exhausted')",
            name="ck_iot_telemetry_inbox_state",
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 10 AND manual_retry_count >= 0",
            name="ck_iot_telemetry_inbox_attempts",
        ),
        sa.CheckConstraint(
            "payload_bytes BETWEEN 1 AND 32768 AND octet_length(raw_text) = payload_bytes",
            name="ck_iot_telemetry_inbox_bytes",
        ),
        sa.CheckConstraint(
            "(state = 'leased' AND lease_id IS NOT NULL AND lease_until IS NOT NULL) "
            "OR (state <> 'leased' AND lease_id IS NULL AND lease_until IS NULL)",
            name="ck_iot_telemetry_inbox_lease",
        ),
    )
    op.create_index(
        "ix_iot_telemetry_due", "iot_telemetry_inbox", ["state", "next_attempt_at"]
    )
    op.create_table(
        "iot_telemetry_ingest_receipt",
        sa.Column("reception_id", sa.Uuid(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("observation_id", sa.Uuid()),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('accepted_new','accepted_duplicate','invalid','conflict')",
            name="ck_iot_telemetry_receipt_outcome",
        ),
        sa.CheckConstraint(
            "outcome NOT IN ('accepted_new','accepted_duplicate') OR observation_id IS NOT NULL",
            name="ck_iot_telemetry_receipt_stored",
        ),
    )
    op.create_table(
        "iot_telemetry_ingress_audit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", sa.Uuid()),
        sa.Column("reception_id", sa.Uuid()),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("payload_bytes", sa.BigInteger(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.Uuid()),
        sa.CheckConstraint("payload_bytes >= 0", name="ck_iot_telemetry_audit_bytes"),
    )
    op.create_table(
        "iot_event_delivery_checkpoint",
        sa.Column("source_event_id", sa.Uuid(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_id", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_reason", sa.String(64), nullable=False),
        sa.Column("target_event_id", sa.Uuid()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("manual_retry_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "state IN ('queued','leased','blocked','exhausted','delivered')",
            name="ck_iot_event_dispatch_state",
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 10 AND manual_retry_count >= 0",
            name="ck_iot_event_dispatch_attempt",
        ),
        sa.CheckConstraint(
            "(state = 'leased' AND lease_id IS NOT NULL AND lease_until IS NOT NULL) OR "
            "(state <> 'leased' AND lease_id IS NULL AND lease_until IS NULL)",
            name="ck_iot_event_dispatch_lease",
        ),
        sa.CheckConstraint(
            "state <> 'delivered' OR (target_event_id IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_iot_event_dispatch_receipt",
        ),
    )
    op.create_index(
        "ix_iot_event_dispatch_due",
        "iot_event_delivery_checkpoint",
        ["state", "next_attempt_at"],
    )
    op.create_table(
        "iot_event_delivery_retry_audit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cycle", sa.Integer(), nullable=False),
        sa.CheckConstraint("cycle >= 1", name="ck_iot_event_dispatch_retry_cycle"),
    )


def downgrade() -> None:
    # Normal migration reversal, never invoked against user data by our proof.
    for table in (
        "iot_event_delivery_retry_audit",
        "iot_event_delivery_checkpoint",
        "iot_telemetry_ingress_audit",
        "iot_telemetry_ingest_receipt",
        "iot_telemetry_inbox",
        "iot_telemetry_queue_guard",
    ):
        op.drop_table(table)
