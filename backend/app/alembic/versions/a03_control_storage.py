"""A03 local command/attempt/receipt candidate; not deployed to app data."""

from alembic import op
import sqlalchemy as sa

revision = "a03_control_storage"
down_revision = "a02_heartbeat_storage"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "iot_command",
        sa.Column("command_id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("iot_device.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("lifetime_seconds", sa.Integer(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("payload_text", sa.String(4096), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_id", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("transport_accepted_at", sa.DateTime(timezone=True)),
        sa.Column("ack_fingerprint", sa.String(64)),
        sa.Column("ack_received_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.String(64)),
        sa.CheckConstraint("status IN ('queued','sent','acknowledged','failed','timeout')", name="ck_iot_command_status"),
        sa.CheckConstraint("expires_at > issued_at AND lifetime_seconds BETWEEN 1 AND 300", name="ck_iot_command_lifetime"),
        sa.CheckConstraint("attempt_count BETWEEN 0 AND 10", name="ck_iot_command_attempts"),
        sa.CheckConstraint("status NOT IN ('acknowledged','failed','timeout') OR terminal_at IS NOT NULL", name="ck_iot_command_terminal"),
        sa.CheckConstraint("status <> 'acknowledged' OR (ack_received_at IS NOT NULL AND ack_fingerprint IS NOT NULL)", name="ck_iot_command_ack"),
        sa.CheckConstraint("status NOT IN ('failed','timeout') OR failure_reason IS NOT NULL", name="ck_iot_command_failure"),
    )
    op.create_index("ix_iot_command_device_id", "iot_command", ["device_id"])
    op.create_index("uq_iot_command_active_device", "iot_command", ["device_id"], unique=True, postgresql_where=sa.text("status IN ('queued','sent')"))
    op.create_table(
        "iot_command_attempt",
        sa.Column("attempt_id", sa.Uuid(), primary_key=True),
        sa.Column("command_id", sa.Uuid(), sa.ForeignKey("iot_command.command_id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.CheckConstraint("outcome IN ('pending','accepted','transient','permanent')", name="ck_iot_command_attempt_outcome"),
    )
    op.create_index("ix_iot_command_attempt_command_id", "iot_command_attempt", ["command_id"])
    op.create_table(
        "iot_command_receipt",
        sa.Column("command_id", sa.Uuid(), sa.ForeignKey("iot_command.command_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("fingerprint", sa.String(64), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("cooling_enabled", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(128)),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.CheckConstraint("outcome IN ('accepted','late','conflict')", name="ck_iot_command_receipt_outcome"),
    )


def downgrade():
    op.drop_table("iot_command_receipt")
    op.drop_table("iot_command_attempt")
    op.drop_table("iot_command")
