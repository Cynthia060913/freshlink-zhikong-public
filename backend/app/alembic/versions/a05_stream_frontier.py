"""A05 candidate schema only: no origin seeding or recovery activation.

Empty tables never certify an old stream as complete. Upgrade and startup do
not fabricate an epoch, initialize historical data, or clear any evidence hold.
"""

import sqlalchemy as sa
from alembic import op

revision = "a05_stream_frontier"
down_revision = "a04_transport_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "iot_stream_frontier",
        sa.Column("device_id", sa.Uuid(), primary_key=True),
        sa.Column("stream_epoch", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("origin_verified", sa.Boolean(), nullable=False),
        sa.Column("admitted_seq", sa.BigInteger(), nullable=False),
        sa.Column("settled_through", sa.BigInteger(), nullable=False),
        sa.Column("evidence_revision", sa.BigInteger(), nullable=False),
        sa.Column("gap_reason", sa.String(64), nullable=False),
        sa.UniqueConstraint("device_id", "stream_epoch"),
        sa.CheckConstraint(
            "settled_through >= 0 AND admitted_seq >= settled_through "
            "AND admitted_seq <= 10000 AND evidence_revision >= 0",
            name="ck_iot_stream_frontier_bounds",
        ),
    )
    op.create_table(
        "iot_stream_admission",
        sa.Column("reception_id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("stream_epoch", sa.Uuid(), nullable=False),
        sa.Column("admission_seq", sa.BigInteger(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("observation_id", sa.Uuid()),
        sa.UniqueConstraint("device_id", "stream_epoch", "admission_seq"),
        sa.ForeignKeyConstraint(
            ["device_id", "stream_epoch"],
            ["iot_stream_frontier.device_id", "iot_stream_frontier.stream_epoch"],
        ),
        sa.CheckConstraint(
            "admission_seq BETWEEN 1 AND 10000",
            name="ck_iot_stream_admission_sequence",
        ),
        sa.CheckConstraint(
            "outcome IN ('pending','accepted_new','accepted_duplicate','invalid','conflict')",
            name="ck_iot_stream_admission_outcome",
        ),
        sa.CheckConstraint(
            "outcome NOT IN ('accepted_new','accepted_duplicate') OR observation_id IS NOT NULL",
            name="ck_iot_stream_admission_observation",
        ),
    )


def downgrade() -> None:
    op.drop_table("iot_stream_admission")
    op.drop_table("iot_stream_frontier")
