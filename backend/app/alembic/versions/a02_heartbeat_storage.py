"""A02 persistent heartbeat/replay/event outbox; local candidate, not deployed."""

from alembic import op
import sqlalchemy as sa

revision = "a02_heartbeat_storage"
down_revision = "a01_device_registry"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "iot_device",
        sa.Column(
            "connectivity", sa.String(16), nullable=False, server_default="unknown"
        ),
    )
    op.add_column(
        "iot_device",
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "iot_device", sa.Column("last_heartbeat_message_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "iot_device", sa.Column("offline_source_event_id", sa.Uuid(), nullable=True)
    )
    op.create_check_constraint(
        "ck_iot_device_connectivity",
        "iot_device",
        "connectivity IN ('unknown', 'online', 'offline')",
    )
    op.create_check_constraint(
        "ck_iot_device_heartbeat_history",
        "iot_device",
        "connectivity = 'unknown' OR (last_seen_at IS NOT NULL AND last_sent_at IS NOT NULL AND last_heartbeat_message_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_iot_device_offline_reference",
        "iot_device",
        "(connectivity = 'offline' AND offline_source_event_id IS NOT NULL) OR (connectivity <> 'offline' AND offline_source_event_id IS NULL)",
    )
    op.create_table(
        "iot_heartbeat_message",
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("iot_device.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("device_id", "message_id"),
        sa.CheckConstraint(
            "outcome IN ('accepted', 'out_of_order')", name="ck_iot_heartbeat_outcome"
        ),
    )
    op.create_table(
        "iot_heartbeat_conflict",
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("iot_device.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("device_id", "message_id", "fingerprint"),
    )
    op.create_table(
        "iot_connectivity_event",
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("iot_device.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("warehouse_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("related_source_event_id", sa.Uuid(), nullable=True),
        sa.Column("delivery_state", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("source_event_id"),
        sa.CheckConstraint(
            "kind IN ('offline', 'recovered')", name="ck_iot_connectivity_event_kind"
        ),
        sa.CheckConstraint(
            "delivery_state IN ('pending', 'held_unbound', 'delivered', 'failed')",
            name="ck_iot_connectivity_event_delivery",
        ),
    )
    op.create_index(
        "ix_iot_connectivity_event_device_id", "iot_connectivity_event", ["device_id"]
    )


def downgrade():
    op.drop_table("iot_connectivity_event")
    op.drop_table("iot_heartbeat_conflict")
    op.drop_table("iot_heartbeat_message")
    op.drop_constraint("ck_iot_device_offline_reference", "iot_device", type_="check")
    op.drop_constraint("ck_iot_device_heartbeat_history", "iot_device", type_="check")
    op.drop_constraint("ck_iot_device_connectivity", "iot_device", type_="check")
    for name in (
        "offline_source_event_id",
        "last_heartbeat_message_id",
        "last_sent_at",
        "connectivity",
    ):
        op.drop_column("iot_device", name)
