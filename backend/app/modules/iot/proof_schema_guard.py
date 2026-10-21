"""Read-only schema gate for A-owned, disposable integration proofs.

This does not install a migration or create fallback B tables. Candidate B01
must already have run; publication and application deployment stay separate.
"""

from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

B01_TABLES = frozenset(
    {
        "alerts_warehouse",
        "alerts_batch",
        "alerts_batch_rule_snapshot",
        "alerts_event",
        "alerts_status_transition",
        "alerts_notification_attempt",
    }
)

A04_TABLES = frozenset(
    {
        "iot_telemetry_queue_guard",
        "iot_telemetry_inbox",
        "iot_telemetry_ingest_receipt",
        "iot_telemetry_ingress_audit",
        "iot_event_delivery_checkpoint",
        "iot_event_delivery_retry_audit",
    }
)


def require_b01_candidate_schema(engine: Engine) -> None:
    """Fail closed before fixtures if the actual migrated B schema is absent."""
    script = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "alembic"))
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError("This combination requires one actual B01 descendant head")
    ancestry = {revision.revision for revision in script.walk_revisions(head=heads[0])}
    if "b01_alerts_storage" not in ancestry:
        raise RuntimeError(
            "This combination requires the actual B01 candidate migration"
        )
    with engine.connect() as connection:
        revisions = list(
            connection.scalars(text("SELECT version_num FROM alembic_version"))
        )
    if revisions != heads:
        raise RuntimeError(
            "This combination requires the actual B01 descendant at current source head"
        )
    if not B01_TABLES.issubset(inspect(engine).get_table_names()):
        raise RuntimeError(
            "Actual B01 tables are missing; never create substitute B DDL"
        )


def require_a04_candidate_schema(engine: Engine) -> None:
    """Read-only proof gate: never replace an absent migration with test DDL."""
    require_b01_candidate_schema(engine)
    script = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "alembic"))
    ancestry = {revision.revision for revision in script.walk_revisions()}
    if "a04_transport_storage" not in ancestry:
        raise RuntimeError("This proof requires actual A04 migration ancestry")
    if not A04_TABLES.issubset(inspect(engine).get_table_names()):
        raise RuntimeError("Actual A04 tables missing; never create substitute A DDL")
    with engine.connect() as connection:
        seeds = list(
            connection.scalars(text("SELECT id FROM iot_telemetry_queue_guard"))
        )
    if seeds != [1]:
        raise RuntimeError("Actual A04 queue guard singleton missing")
