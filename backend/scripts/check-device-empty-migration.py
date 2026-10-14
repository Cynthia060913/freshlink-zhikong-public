"""Check migrations in a newly created disposable database, never the app DB."""

import logging
import os
import re
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.core.config import settings

logger = logging.getLogger(__name__)


def main():
    database = "freshlink_a01_" + uuid.uuid4().hex
    assert re.fullmatch(r"freshlink_a01_[0-9a-f]{32}", database)
    current_url = make_url(str(settings.DATABASE_URL))
    assert database != current_url.database
    admin_url = current_url.set(drivername="postgresql", database="postgres")
    scratch_url = current_url.set(drivername="postgresql+psycopg", database=database)
    created = False
    with psycopg.connect(
        admin_url.render_as_string(hide_password=False), autocommit=True
    ) as admin:
        try:
            if admin.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (database,)
            ).fetchone():
                raise RuntimeError("Disposable name collision; nothing removed")
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
            )
            created = True
            env = dict(os.environ)
            env["DATABASE_URL"] = scratch_url.render_as_string(hide_password=False)
            outcome = subprocess.run(
                ["alembic", "upgrade", "head"],
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if outcome.returncode != 0:
                # Do not echo environment or connection strings in failure output.
                raise RuntimeError("Empty-database Alembic upgrade failed")
            with psycopg.connect(
                current_url.set(
                    drivername="postgresql", database=database
                ).render_as_string(hide_password=False)
            ) as scratch:
                revision = scratch.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()[0]
                if revision != "a01_device_registry":
                    raise RuntimeError(
                        "Unexpected migration head; update the versioned check"
                    )
                tables = {
                    row[0]
                    for row in scratch.execute(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    )
                }
                if not {"user", "item", "iot_device"}.issubset(tables):
                    raise RuntimeError("Empty migration missing expected tables")
                columns = {
                    row[0]: row[1]
                    for row in scratch.execute(
                        "SELECT column_name,data_type FROM information_schema.columns "
                        "WHERE table_name='iot_device' AND table_schema='public'"
                    )
                }
                if columns.get("created_at") != "timestamp with time zone":
                    raise RuntimeError("Device timestamp lacks PostgreSQL timezone")
            logger.info(
                "PASS fresh empty PostgreSQL database upgraded to A01; tables/timezone verified"
            )
        finally:
            if created:
                # Only the UUID database created by this invocation can be removed.
                assert re.fullmatch(r"freshlink_a01_[0-9a-f]{32}", database)
                admin.execute(
                    sql.SQL("DROP DATABASE {}").format(sql.Identifier(database))
                )
                logger.info(
                    "PASS disposable migration database removed; app database untouched"
                )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    main()
