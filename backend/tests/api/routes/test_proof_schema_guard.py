"""The current A probe must not replace missing B migration with test DDL."""

import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError

from app.modules.iot.proof_schema_guard import B01_TABLES, require_b01_candidate_schema


class B01ProofGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = MagicMock()
        self.connection = self.engine.connect.return_value.__enter__.return_value
        self.connection.scalar.return_value = "b01_alerts_storage"

    def test_wrong_revision_is_rejected_before_schema_lookup(self) -> None:
        self.connection.scalar.return_value = "a03_control_storage"
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
        self.connection.execute.assert_not_called()
        self.engine.begin.assert_not_called()

    def test_actual_revision_and_all_six_tables_are_required(self) -> None:
        with patch("app.modules.iot.proof_schema_guard.inspect") as inspect_mock:
            inspect_mock.return_value.get_table_names.return_value = list(B01_TABLES)
            require_b01_candidate_schema(self.engine)
        self.assertEqual(
            str(self.connection.scalar.call_args.args[0]),
            "SELECT version_num FROM alembic_version",
        )
        self.connection.execute.assert_not_called()

    def test_storage_failure_is_not_relabelled_as_success(self) -> None:
        self.connection.scalar.side_effect = OperationalError("synthetic", {}, None)
        with self.assertRaises(OperationalError):
            require_b01_candidate_schema(self.engine)


if __name__ == "__main__":
    unittest.main()
