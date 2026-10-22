"""B01 alert storage: warehouse/batch/rule-snapshot/event/transition/attempt.

Chains on the unique published A chain (a03_control_storage) — no
parallel head, no production create_all. Creates the six append-only
``alerts_*`` tables backing the B module (F01–F04):

- ``alerts_warehouse`` / ``alerts_batch`` — C-side inventory anchors;
- ``alerts_batch_rule_snapshot`` — immutable rule snapshots (F02);
  on PostgreSQL a BEFORE UPDATE/DELETE trigger enforces append-only
  at the DB level, as promised in the PR #10 round-2 review of the
  snapshot model docstring;
- ``alerts_event`` — ingested candidates + device events (F03);
- ``alerts_status_transition`` — append-only alert lifecycle (S0 f98
  §3 decision 3); current status = latest ``to_status``;
- ``alerts_notification_attempt`` — per-send audit rows (F04).

Schema mirrors SQLModel metadata exactly (verified by
tests/modules/alerts/test_b01_alerts_migration.py against
create_all), including the ``alertstatus`` enum type whose labels are
the enum member NAMES (OPEN/ACKNOWLEDGED/RESOLVED) as SQLAlchemy's
Enum type derives them. The migration is hand-written so history stays
independent of later model edits.
"""

import sqlalchemy as sa
from alembic import op

revision = "b01_alerts_storage"
down_revision = "a03_control_storage"
branch_labels = None
depends_on = None

_APPEND_ONLY_FUNCTION = """
CREATE FUNCTION alerts_snapshot_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'alerts_batch_rule_snapshot is append-only (F02): UPDATE and DELETE are rejected; corrections are new snapshots'
        USING ERRCODE = 'check_violation';
END
$$ LANGUAGE plpgsql
"""

_APPEND_ONLY_TRIGGER = """
CREATE TRIGGER alerts_batch_rule_snapshot_append_only
    BEFORE UPDATE OR DELETE ON alerts_batch_rule_snapshot
    FOR EACH ROW EXECUTE FUNCTION alerts_snapshot_is_append_only()
"""


def upgrade():
    op.create_table(
        "alerts_warehouse",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("location", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_alerts_warehouse_code", "alerts_warehouse", ["code"], unique=True
    )
    op.create_table(
        "alerts_batch",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column(
            "warehouse_id",
            sa.Uuid(),
            sa.ForeignKey("alerts_warehouse.id"),
            nullable=False,
        ),
        sa.Column("product", sa.String(120), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "warehouse_id", "code", name="uq_alerts_batch_warehouse_code"
        ),
    )
    op.create_index("ix_alerts_batch_code", "alerts_batch", ["code"])
    op.create_index("ix_alerts_batch_warehouse_id", "alerts_batch", ["warehouse_id"])
    op.create_table(
        "alerts_batch_rule_snapshot",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Uuid(),
            sa.ForeignKey("alerts_batch.id"),
            nullable=False,
        ),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("rule_version", sa.String(64)),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("effective_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_end_at", sa.DateTime(timezone=True)),
        sa.Column("supersedes_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "batch_id",
            "signature",
            name="uq_alerts_batch_rule_snapshot_batch_signature",
        ),
    )
    op.create_index(
        "ix_alerts_batch_rule_snapshot_batch_id",
        "alerts_batch_rule_snapshot",
        ["batch_id"],
    )
    op.create_index(
        "ix_alerts_batch_rule_snapshot_version_id",
        "alerts_batch_rule_snapshot",
        ["version_id"],
    )
    op.create_index(
        "ix_alerts_batch_rule_snapshot_rule_version",
        "alerts_batch_rule_snapshot",
        ["rule_version"],
    )
    op.create_index(
        "ix_alerts_batch_rule_snapshot_created_at",
        "alerts_batch_rule_snapshot",
        ["created_at"],
    )
    op.create_table(
        "alerts_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_event_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("alerts_batch.id")),
        sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("alerts_warehouse.id")),
        sa.Column("device_id", sa.String(64)),
        sa.Column("metric_code", sa.String(64)),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float()),
        sa.Column("unit", sa.String(8)),
        sa.Column("severity", sa.String(32)),
        sa.Column("observation_ids_json", sa.String(8192)),
        sa.Column("rule_snapshot_id", sa.Uuid()),
        sa.Column("rule_version", sa.String(64)),
        sa.Column("algorithm_version", sa.String(64)),
        sa.Column("evidence_json", sa.String()),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("related_source_event_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_alerts_event_source_event_id",
        "alerts_event",
        ["source_event_id"],
        unique=True,
    )
    op.create_index("ix_alerts_event_batch_id", "alerts_event", ["batch_id"])
    op.create_index("ix_alerts_event_warehouse_id", "alerts_event", ["warehouse_id"])
    op.create_index(
        "ix_alerts_event_rule_snapshot_id", "alerts_event", ["rule_snapshot_id"]
    )
    op.create_table(
        "alerts_status_transition",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "alert_event_id",
            sa.Uuid(),
            sa.ForeignKey("alerts_event.id"),
            nullable=False,
        ),
        sa.Column(
            "from_status",
            sa.Enum("OPEN", "ACKNOWLEDGED", "RESOLVED", name="alertstatus"),
            nullable=False,
        ),
        sa.Column(
            "to_status",
            sa.Enum("OPEN", "ACKNOWLEDGED", "RESOLVED", name="alertstatus"),
            nullable=False,
        ),
        sa.Column("trigger_source_event_id", sa.String(128)),
        sa.Column("reason", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_alerts_status_transition_alert_event_id",
        "alerts_status_transition",
        ["alert_event_id"],
    )
    op.create_table(
        "alerts_notification_attempt",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "alert_event_id",
            sa.Uuid(),
            sa.ForeignKey("alerts_event.id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.String(1000)),
        sa.Column("server_response", sa.String(1000)),
        sa.Column("retry_after_s", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_alerts_notification_attempt_alert_event_id",
        "alerts_notification_attempt",
        ["alert_event_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_APPEND_ONLY_FUNCTION)
        op.execute(_APPEND_ONLY_TRIGGER)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS alerts_batch_rule_snapshot_append_only ON alerts_batch_rule_snapshot"
        )
        op.execute("DROP FUNCTION IF EXISTS alerts_snapshot_is_append_only()")
    op.drop_table("alerts_notification_attempt")
    op.drop_table("alerts_status_transition")
    op.drop_table("alerts_event")
    op.drop_table("alerts_batch_rule_snapshot")
    op.drop_table("alerts_batch")
    op.drop_table("alerts_warehouse")
    if bind.dialect.name == "postgresql":
        sa.Enum(name="alertstatus").drop(bind, checkfirst=True)
