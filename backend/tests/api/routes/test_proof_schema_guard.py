"""The current A probe must not replace missing B migration with test DDL."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from alembic.script import ScriptDirectory
from sqlalchemy.exc import OperationalError

from app.modules.iot.proof_schema_guard import (
    A04_TABLES,
    B01_TABLES,
    require_a04_candidate_schema,
    require_b01_candidate_schema,
)

CURRENT_HEAD = ScriptDirectory(
    str(Path(__file__).resolve().parents[3] / "app/alembic")
).get_current_head()


class B01ProofGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = MagicMock()
        self.connection = self.engine.connect.return_value.__enter__.return_value
        self.connection.scalars.return_value = [CURRENT_HEAD]

    def test_wrong_revision_is_rejected_before_schema_lookup(self) -> None:
        self.connection.scalars.return_value = ["a03_control_storage"]
        with patch("app.modules.iot.proof_schema_guard.inspect") as inspect_mock:
            with self.assertRaisesRegex(RuntimeError, "actual B01"):
                require_b01_candidate_schema(self.engine)
            inspect_mock.assert_not_called()

    def test_missing_table_is_not_silently_created(self) -> None:
        with patch("app.modules.iot.proof_schema_guard.inspect") as inspect_mock:
            inspect_mock.return_value.get_table_names.return_value = [
                "alerts_warehouse"
            ]
            with self.assertRaisesRegex(RuntimeError, "never create substitute"):
                require_b01_candidate_schema(self.engine)
        self.engine.begin.assert_not_called()

    def test_actual_revision_and_all_six_tables_are_required(self) -> None:
        with patch("app.modules.iot.proof_schema_guard.inspect") as inspect_mock:
            inspect_mock.return_value.get_table_names.return_value = list(B01_TABLES)
            require_b01_candidate_schema(self.engine)
        self.assertEqual(
            str(self.connection.scalars.call_args.args[0]),
            "SELECT version_num FROM alembic_version",
        )

    def test_multiple_empty_unknown_and_stale_database_heads_are_rejected(self) -> None:
        for revisions in (
            [],
            ["b01_alerts_storage"],
            ["unknown"],
            [CURRENT_HEAD, "b01_alerts_storage"],
        ):
            with self.subTest(revisions=revisions):
                self.connection.scalars.return_value = revisions
                with self.assertRaisesRegex(RuntimeError, "actual B01"):
                    require_b01_candidate_schema(self.engine)

    def test_source_fork_and_unrelated_head_are_rejected_without_database_read(
        self,
    ) -> None:
        with patch("app.modules.iot.proof_schema_guard.ScriptDirectory") as factory:
            script = factory.return_value
            for heads in ([], ["one", "two"]):
                script.get_heads.return_value = heads
                with self.assertRaisesRegex(RuntimeError, "one actual B01"):
                    require_b01_candidate_schema(self.engine)
            script.get_heads.return_value = ["unrelated"]
            script.walk_revisions.return_value = []
            with self.assertRaisesRegex(RuntimeError, "actual B01"):
                require_b01_candidate_schema(self.engine)
        self.engine.connect.assert_not_called()

    def test_storage_failure_is_not_relabelled_as_success(self) -> None:
        self.connection.scalars.side_effect = OperationalError("synthetic", {}, None)
        with self.assertRaises(OperationalError):
            require_b01_candidate_schema(self.engine)


class A04ProofGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = MagicMock()
        self.connection = self.engine.connect.return_value.__enter__.return_value

    def test_actual_tables_and_exact_seed_are_read_only(self) -> None:
        self.connection.scalars.return_value = [1]
        with (
            patch(
                "app.modules.iot.proof_schema_guard.require_b01_candidate_schema"
            ) as b,
            patch("app.modules.iot.proof_schema_guard.inspect") as tables,
        ):
            tables.return_value.get_table_names.return_value = list(A04_TABLES)
            require_a04_candidate_schema(self.engine)
            b.assert_called_once_with(self.engine)
        self.assertEqual(
            str(self.connection.scalars.call_args.args[0]),
            "SELECT id FROM iot_telemetry_queue_guard",
        )
        self.engine.begin.assert_not_called()

    def test_missing_table_or_seed_never_triggers_fallback_ddl(self) -> None:
        with (
            patch("app.modules.iot.proof_schema_guard.require_b01_candidate_schema"),
            patch("app.modules.iot.proof_schema_guard.inspect") as tables,
        ):
            tables.return_value.get_table_names.return_value = []
            with self.assertRaisesRegex(RuntimeError, "never create substitute"):
                require_a04_candidate_schema(self.engine)
            self.engine.connect.assert_not_called()
            tables.return_value.get_table_names.return_value = list(A04_TABLES)
            for seeds in ([], [2], [1, 1]):
                self.connection.scalars.return_value = seeds
                with self.assertRaisesRegex(RuntimeError, "singleton"):
                    require_a04_candidate_schema(self.engine)
        self.engine.begin.assert_not_called()

    def test_absent_a04_ancestry_is_rejected_even_with_fake_tables(self) -> None:
        with (
            patch("app.modules.iot.proof_schema_guard.require_b01_candidate_schema"),
            patch("app.modules.iot.proof_schema_guard.ScriptDirectory") as script,
            patch("app.modules.iot.proof_schema_guard.inspect") as tables,
        ):
            script.return_value.walk_revisions.return_value = []
            with self.assertRaisesRegex(RuntimeError, "ancestry"):
                require_a04_candidate_schema(self.engine)
            tables.assert_not_called()
            self.engine.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
