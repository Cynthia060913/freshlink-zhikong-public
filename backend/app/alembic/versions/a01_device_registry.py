"""FreshLink A-01 device registry; no warehouse table or heartbeat yet."""
from alembic import op
import sqlalchemy as sa

revision = "a01_device_registry"
down_revision = "fe56fa70289e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "iot_device",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=True),
        sa.Column("is_simulated", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_iot_device_code", "iot_device", ["code"], unique=True)


def downgrade():
    op.drop_index("ix_iot_device_code", table_name="iot_device")
    op.drop_table("iot_device")
