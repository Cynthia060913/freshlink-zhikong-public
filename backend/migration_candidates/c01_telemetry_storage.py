"""Frozen C ten-table successor proposal; NOT an active Alembic revision.

The proposed parent is A05, which has not been published on main. The
integration owner must review the single live head before copying this into
app/alembic/versions and registering both C metadata collections in env.py.
Importing this proposal never creates tables or changes an application DB.
"""

import sqlalchemy as sa
from alembic import op

revision = "c01_telemetry_storage"
down_revision = "a05_stream_frontier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telemetry_observation_candidate",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("raw_text", sa.String(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("sampled_at_utc", sa.DateTime(timezone=True)),
        sa.Column("first_received_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("normalized", sa.JSON(), nullable=False),
        sa.Column("quality", sa.JSON(), nullable=False),
        sa.Column("risk", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "device_id", "message_id", name="uq_c_candidate_device_message"
        ),
    )
    op.create_index(
        "ix_c_candidate_history",
        "telemetry_observation_candidate",
        ["warehouse_id", "sampled_at_utc", "id"],
    )
    op.create_table(
        "telemetry_quality_tag_candidate",
        sa.Column(
            "observation_id",
            sa.Uuid(),
            sa.ForeignKey("telemetry_observation_candidate.id"),
            primary_key=True,
        ),
        sa.Column("tag", sa.String(128), primary_key=True),
    )
    op.create_index(
        "ix_c_candidate_quality_tag",
        "telemetry_quality_tag_candidate",
        ["tag", "observation_id"],
    )
    op.create_table(
        "telemetry_reception_candidate",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "observation_id",
            sa.Uuid(),
            sa.ForeignKey("telemetry_observation_candidate.id"),
        ),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("received_at_utc", sa.String(35), nullable=False),
        sa.Column("raw_text", sa.String(), nullable=False),
        sa.Column("receipt_sha256", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False),
    )
    op.create_table(
        "telemetry_reception_reuse_candidate",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "claimed_reception_id",
            sa.Uuid(),
            sa.ForeignKey("telemetry_reception_candidate.id"),
            nullable=False,
        ),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("received_at_utc", sa.String(35), nullable=False),
        sa.Column("raw_text", sa.String(), nullable=False),
        sa.Column("receipt_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "claimed_reception_id",
            "receipt_sha256",
            name="uq_c_candidate_claimed_receipt_digest",
        ),
    )
    op.create_table(
        "telemetry_evaluation_candidate",
        sa.Column(
            "observation_id",
            sa.Uuid(),
            sa.ForeignKey("telemetry_observation_candidate.id"),
            primary_key=True,
        ),
        sa.Column("state", sa.String(24), nullable=False),
    )
    op.create_table(
        "telemetry_candidate_outbox_candidate",
        sa.Column("source_event_id", sa.Uuid(), primary_key=True),
        sa.Column("related_source_event_id", sa.Uuid()),
        sa.Column("frozen_device_id", sa.Uuid()),
        sa.Column("frozen_warehouse_id", sa.Uuid()),
        sa.Column("frozen_stream_epoch", sa.Uuid()),
        sa.Column("frozen_admitted_seq", sa.Integer()),
        sa.Column("frozen_settled_through", sa.Integer()),
        sa.Column("frozen_evidence_revision", sa.Integer()),
        sa.Column(
            "observation_id",
            sa.Uuid(),
            sa.ForeignKey("telemetry_observation_candidate.id"),
            nullable=False,
        ),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until_utc", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(80)),
    )
    op.create_table(
        "telemetry_candidate_outbox_conflict_candidate",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_event_id",
            sa.Uuid(),
            sa.ForeignKey("telemetry_candidate_outbox_candidate.source_event_id"),
            nullable=False,
        ),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("first_detected_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_event_id",
            "observation_id",
            "body_sha256",
            name="uq_c_candidate_outbox_conflict",
        ),
    )
    op.create_table(
        "telemetry_snapshot_candidate",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("filters_sha256", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
    )
    op.create_table(
        "telemetry_snapshot_member_candidate",
        sa.Column(
            "snapshot_id",
            sa.Uuid(),
            sa.ForeignKey("telemetry_snapshot_candidate.id"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column(
            "observation_id",
            sa.Uuid(),
            sa.ForeignKey("telemetry_observation_candidate.id"),
        ),
        sa.Column("projection", sa.JSON(), nullable=False),
    )
    op.create_table(
        "telemetry_recovery_progress_candidate",
        sa.Column("original_source_event_id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("anomaly_sampled_at_utc", sa.String(35), nullable=False),
        sa.Column("comparison", sa.String(2), nullable=False),
        sa.Column("threshold_c", sa.Float(), nullable=False),
        sa.Column("min_samples", sa.Integer(), nullable=False),
        sa.Column("max_gap_seconds", sa.Integer(), nullable=False),
        sa.Column("last_sampled_at_utc", sa.String(35)),
        sa.Column("last_received_at_utc", sa.String(35)),
        sa.Column("last_received_observation_id", sa.Uuid()),
        sa.Column("last_matching_at_utc", sa.String(35)),
        sa.Column("observation_ids", sa.JSON(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("telemetry_recovery_progress_candidate")
    op.drop_table("telemetry_snapshot_member_candidate")
    op.drop_table("telemetry_snapshot_candidate")
    op.drop_table("telemetry_candidate_outbox_conflict_candidate")
    op.drop_table("telemetry_candidate_outbox_candidate")
    op.drop_table("telemetry_evaluation_candidate")
    op.drop_table("telemetry_reception_reuse_candidate")
    op.drop_table("telemetry_reception_candidate")
    op.drop_index(
        "ix_c_candidate_quality_tag", table_name="telemetry_quality_tag_candidate"
    )
    op.drop_table("telemetry_quality_tag_candidate")
    op.drop_index(
        "ix_c_candidate_history", table_name="telemetry_observation_candidate"
    )
    op.drop_table("telemetry_observation_candidate")
