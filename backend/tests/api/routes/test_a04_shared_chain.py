"""Actual candidate A03 -> B01 -> A04, in an owned disposable PG schema only."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import (
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlmodel import Session

from app.core.config import settings
from app.models import User
from app.modules.iot.models import Device
from app.modules.iot.telemetry_inbox_models import queue_guard


def test_existing_a03_rows_survive_actual_candidate_upgrade_and_schema_check():
    if os.getenv("FRESHLINK_TRANSPORT_TEST_POSTGRES") != "1":
        pytest.skip("requires strict owned transport PG entry")
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
        raise RuntimeError("refusing non-disposable A04 migration proof")
    base = create_engine(url)
    schema = "a04_chain_test_" + uuid4().hex
    # The upstream UUID extension lives in public. Keep its functions visible,
    # but create an empty private version table so Alembic cannot accidentally
    # adopt public's already-upgraded revision through search_path fallback.
    scoped = url.update_query_dict({"options": "-csearch_path=" + schema + ",public"})
    private = create_engine(scoped)
    created = False

    def alembic(*arguments):
        environment = dict(os.environ)
        environment["DATABASE_URL"] = scoped.render_as_string(hide_password=False)
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            cwd=Path(__file__).resolve().parents[3],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    try:
        with base.begin() as connection:
            public_before = (
                connection.scalar(
                    text("SELECT version_num FROM public.alembic_version")
                ),
                connection.scalar(select(func.count()).select_from(User)),
                connection.scalar(select(func.count()).select_from(Device)),
            )
            connection.execute(CreateSchema(schema))
            created = True
            Table(
                "alembic_version",
                MetaData(),
                Column("version_num", String(32), primary_key=True),
                schema=schema,
            ).create(connection)
        alembic("upgrade", "a03_control_storage")
        with Session(private) as session:
            actor = User(email="a04-proof@example.test", hashed_password="fixture-only")
            device = Device(
                code="a04-proof", name="preserved A03 row", kind="temperature_sensor"
            )
            session.add(actor)
            session.add(device)
            session.commit()
            actor_id, device_id = actor.id, device.id
        alembic("upgrade", "head")
        alembic("check")
        with Session(private) as session:
            assert session.get(User, actor_id).email == "a04-proof@example.test"
            assert session.get(Device, device_id).name == "preserved A03 row"
        with private.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == ("a04_transport_storage")
            assert connection.scalar(select(queue_guard.c.id)) == 1
        names = set(inspect(private).get_table_names(schema=schema))
        assert "alerts_event" in names and "iot_event_delivery_checkpoint" in names
        assert "iot_stream_frontier" not in names
        assert not any(name.startswith("telemetry_") for name in names)
    finally:
        private.dispose()
        if created:
            with base.begin() as connection:
                # Drop only confirmed owned tables and schema; never CASCADE.
                for table in reversed(
                    inspect(connection).get_sorted_table_and_fkc_names(schema=schema)
                ):
                    name, _ = table
                    if name is not None:
                        Table(
                            name, MetaData(), schema=schema, autoload_with=connection
                        ).drop(connection)
                # B01 owns one trigger function as well as its tables. Remove
                # that exact object only inside our confirmed random schema.
                connection.execute(
                    text(
                        f'DROP FUNCTION IF EXISTS "{schema}".alerts_snapshot_is_append_only()'
                    )
                )
                connection.execute(DropSchema(schema))
                public_after = (
                    connection.scalar(
                        text("SELECT version_num FROM public.alembic_version")
                    ),
                    connection.scalar(select(func.count()).select_from(User)),
                    connection.scalar(select(func.count()).select_from(Device)),
                )
                assert public_after == public_before
        base.dispose()
