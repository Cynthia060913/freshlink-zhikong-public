"""Run inactive C01 DDL in a private PG schema, never the application DB."""

import importlib.util
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select

import app.modules.telemetry.outbox_candidate  # noqa: F401
import app.modules.telemetry.snapshot_candidate  # noqa: F401
from app.modules.iot.models import Device
from app.modules.telemetry.recovery_progress_candidate import metadata as progress_meta
from app.modules.telemetry.storage_candidate import metadata as c_meta
from tests.api.routes import test_stream_frontier as checks


@unittest.skipUnless(
    os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") == "1", "strict owned PG only"
)
class C01ProposalPostgresTests(unittest.TestCase):
    def test_real_proposal_upgrade_downgrade_preserves_a_rows(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "migration_candidates/c01_telemetry_storage.py"
        )
        spec = importlib.util.spec_from_file_location("c01_pg_proposal", path)
        assert spec is not None and spec.loader is not None
        proposal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(proposal)
        with contextmanager(checks.store.__wrapped__)() as store:
            expected = {**c_meta.tables, **progress_meta.tables}
            self.assertEqual(len(expected), 10)
            try:
                for _ in range(2):
                    with store.engine.begin() as connection:
                        with patch.object(
                            proposal,
                            "op",
                            Operations(MigrationContext.configure(connection)),
                        ):
                            proposal.upgrade()
                        inspector = inspect(connection)
                        actual = set(inspector.get_table_names(schema=store.schema))
                        self.assertTrue(set(expected).issubset(actual))
                        for name, model in expected.items():
                            columns = inspector.get_columns(name, schema=store.schema)
                            self.assertEqual(
                                [column["name"] for column in columns],
                                list(model.columns.keys()),
                            )
                            self.assertEqual(
                                {
                                    column["name"]: column["nullable"]
                                    for column in columns
                                },
                                {
                                    column.name: column.nullable
                                    for column in model.columns
                                },
                            )
                        self.assertEqual(
                            connection.scalar(select(Device.__table__.c.id)),
                            store.device_id,
                        )
                    with store.engine.begin() as connection:
                        with patch.object(
                            proposal,
                            "op",
                            Operations(MigrationContext.configure(connection)),
                        ):
                            proposal.downgrade()
                        actual = set(
                            inspect(connection).get_table_names(schema=store.schema)
                        )
                        self.assertTrue(set(expected).isdisjoint(actual))
                        self.assertEqual(
                            connection.scalar(select(Device.__table__.c.id)),
                            store.device_id,
                        )
                    self.assertEqual(checks.state(store)["stream_epoch"], store.epoch)
            finally:
                # Only C's known test-schema objects; never CASCADE or public.
                progress_meta.drop_all(store.engine)
                c_meta.drop_all(store.engine)


if __name__ == "__main__":
    unittest.main()
