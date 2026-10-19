"""Read-only schema gate for A-owned, disposable integration proofs.

This does not install a migration or create fallback B tables. Candidate B01
must already have run; publication and application deployment stay separate.
"""

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


def require_b01_candidate_schema(engine: Engine) -> None:
    """Fail closed before fixtures if the actual migrated B schema is absent."""
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != "b01_alerts_storage":
        raise RuntimeError(
            "This combination requires the actual B01 candidate migration"
        )
    if not B01_TABLES.issubset(inspect(engine).get_table_names()):
        raise RuntimeError(
            "Actual B01 tables are missing; never create substitute B DDL"
        )
