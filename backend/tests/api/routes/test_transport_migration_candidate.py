"""A's frozen six-table candidate; no active revision or original DB changes."""

import importlib.util
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import settings
from app.modules.iot.event_dispatch_models import metadata as event_metadata
from app.modules.iot.telemetry_inbox_models import metadata as inbox_metadata

BACKEND = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "a04_transport_candidate", BACKEND / "migration_candidates/a04_transport_storage.py"
)
assert spec is not None and spec.loader is not None
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
TABLES = dict(inbox_metadata.tables) | dict(event_metadata.tables)


def active_revisions():
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "app/alembic"))
    return ScriptDirectory.from_config(config)


def migration_pg_enabled():
    return os.getenv("FRESHLINK_TRANSPORT_TEST_POSTGRES") == "1"


def test_generic_inbox_opt_in_does_not_enable_strict_migration_proof():
    with patch.dict(os.environ, {"FRESHLINK_INBOX_TEST_POSTGRES": "1"}, clear=True):
        assert not migration_pg_enabled()
        with patch.dict(os.environ, {"FRESHLINK_TRANSPORT_TEST_POSTGRES": "1"}):
            assert migration_pg_enabled()


def test_candidate_is_not_an_active_alembic_head():
    revisions = active_revisions()
    assert candidate.revision not in {
        item.revision for item in revisions.walk_revisions()
    }
    assert candidate.down_revision == "b01_alerts_storage"


def test_frozen_tables_match_owned_metadata_without_dynamic_create_all():
    operations = MagicMock()
    with patch.object(candidate, "op", operations):
        candidate.upgrade()
    actual = {}
    for call in operations.create_table.call_args_list:
        name, *items = call.args
        actual[name] = sa.Table(name, sa.MetaData(), *items)
    assert set(actual) == set(TABLES)
    for name, table in actual.items():
        model = TABLES[name]
        assert [
            (c.name, str(c.type), c.nullable, c.primary_key) for c in table.columns
        ] == [(c.name, str(c.type), c.nullable, c.primary_key) for c in model.columns]
        assert {
            str(c.sqltext)
            for c in table.constraints
            if isinstance(c, sa.CheckConstraint)
        } == {
            str(c.sqltext)
            for c in model.constraints
            if isinstance(c, sa.CheckConstraint)
        }
    assert {
        (
            call.args[0],
            call.args[1],
            tuple(call.args[2]),
            call.kwargs.get("unique", False),
        )
        for call in operations.create_index.call_args_list
    } == {
        (index.name, model.name, tuple(c.name for c in index.columns), index.unique)
        for model in TABLES.values()
        for index in model.indexes
    }
    assert str(operations.execute.call_args.args[0]) == (
        "INSERT INTO iot_telemetry_queue_guard (id) VALUES (1)"
    )


def test_downgrade_drops_only_the_six_a_tables():
    operations = MagicMock()
    with patch.object(candidate, "op", operations):
        candidate.downgrade()
    names = [call.args[0] for call in operations.drop_table.call_args_list]
    assert len(names) == 6 and set(names) == set(TABLES)


def test_actual_postgres_candidate_roundtrip_and_cleanup():
    if not migration_pg_enabled():
        pytest.skip(
            "Not a transport PG proof: enable FRESHLINK_TRANSPORT_TEST_POSTGRES=1 "
            "only in the guarded disposable Compose; strict CI checks actual execution"
        )
    url = make_url(str(settings.DATABASE_URL))
    if (
        not Path("/.dockerenv").is_file()
        or url.host != "test-db"
        or url.database != "freshlink_test"
        or url.username != "freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
        or settings.FASTAPI_ENV != "development"
        or os.getenv("SMTP_HOST") != ""
        or os.getenv("SENTRY_DSN") != ""
    ):
        raise RuntimeError("Refusing non-disposable transport migration proof")
    base = sa.create_engine(url, pool_pre_ping=True)
    schema = "a_transport_proof_" + uuid4().hex
    if not re.fullmatch(r"a_transport_proof_[0-9a-f]{32}", schema):
        raise RuntimeError("Invalid owned schema identity")
    created = False
    now = datetime.now(UTC)
    reception = uuid4()
    try:
        with base.begin() as connection:
            original_versions = list(
                connection.scalars(sa.text("SELECT version_num FROM alembic_version"))
            )
            revisions = active_revisions()
            heads = revisions.get_heads()
            assert len(heads) == 1 and original_versions == heads
            assert "b01_alerts_storage" in {
                item.revision for item in revisions.iterate_revisions(heads[0], "base")
            }
            original_tables = set(
                sa.inspect(connection).get_table_names(schema="public")
            )
            connection.execute(CreateSchema(schema))
        created = True
        with base.begin() as connection:
            connection.execute(
                sa.text("SELECT set_config('search_path', :schema, true)"),
                {"schema": schema},
            )
            with Operations.context(MigrationContext.configure(connection)):
                candidate.upgrade()
            inspector = sa.inspect(connection)
            assert set(inspector.get_table_names(schema=schema)) == set(TABLES)
            for name, model in TABLES.items():
                columns = inspector.get_columns(name, schema=schema)
                assert [
                    (
                        c["name"],
                        str(c["type"].compile(dialect=connection.dialect)),
                        c["nullable"],
                    )
                    for c in columns
                ] == [
                    (
                        c.name,
                        str(c.type.compile(dialect=connection.dialect)),
                        c.nullable,
                    )
                    for c in model.columns
                ]
                assert {
                    c["name"]
                    for c in inspector.get_check_constraints(name, schema=schema)
                } == {
                    c.name
                    for c in model.constraints
                    if isinstance(c, sa.CheckConstraint)
                }
                assert inspector.get_pk_constraint(name, schema=schema)[
                    "constrained_columns"
                ] == [c.name for c in model.primary_key.columns]
                assert {
                    (i["name"], tuple(i["column_names"]), bool(i["unique"]))
                    for i in inspector.get_indexes(name, schema=schema)
                } == {
                    (i.name, tuple(c.name for c in i.columns), i.unique)
                    for i in model.indexes
                }
            guard = inbox_metadata.tables["iot_telemetry_queue_guard"]
            assert connection.scalar(sa.select(guard.c.id)) == 1
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(guard.insert().values(id=2))
            inbox = inbox_metadata.tables["iot_telemetry_inbox"]
            row = {
                "reception_id": reception,
                "device_id": uuid4(),
                "warehouse_id": uuid4(),
                "received_at": now,
                "raw_text": "{}",
                "payload_bytes": 2,
                "payload_sha256": "a" * 64,
                "fingerprint": "b" * 64,
                "state": "queued",
                "attempt_count": 0,
                "next_attempt_at": now,
                "last_reason": "new",
                "manual_retry_count": 0,
            }
            connection.execute(inbox.insert().values(**row))
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    inbox.insert().values(
                        **(row | {"reception_id": uuid4(), "attempt_count": 11})
                    )
                )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    inbox.insert().values(
                        **(row | {"reception_id": uuid4(), "state": "leased"})
                    )
                )
        base.dispose()
        with base.begin() as connection:
            connection.execute(
                sa.text("SELECT set_config('search_path', :schema, true)"),
                {"schema": schema},
            )
            assert connection.scalar(sa.select(sa.func.count()).select_from(inbox)) == 1
            with Operations.context(MigrationContext.configure(connection)):
                candidate.downgrade()
                assert sa.inspect(connection).get_table_names(schema=schema) == []
                candidate.upgrade()
            assert connection.scalar(sa.select(guard.c.id)) == 1
        with base.connect() as connection:
            assert (
                list(
                    connection.scalars(
                        sa.text("SELECT version_num FROM alembic_version")
                    )
                )
                == original_versions
            )
            assert (
                set(sa.inspect(connection).get_table_names(schema="public"))
                == original_tables
            )
    finally:
        try:
            if created:
                # Only this proof's newly created, UUID-checked schema is removed.
                with base.begin() as connection:
                    connection.execute(DropSchema(schema, cascade=True))
                with base.connect() as connection:
                    assert not sa.inspect(connection).has_schema(schema)
        finally:
            base.dispose()
