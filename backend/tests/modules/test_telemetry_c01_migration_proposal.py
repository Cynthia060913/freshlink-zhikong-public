"""Schema proposal checks only; never connect to or migrate a database."""

import ast
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import sqlalchemy as sa

import app.modules.telemetry.outbox_candidate  # noqa: F401
import app.modules.telemetry.snapshot_candidate  # noqa: F401
from app.modules.telemetry.recovery_progress_candidate import metadata as progress_meta
from app.modules.telemetry.storage_candidate import metadata as storage_meta

BACKEND = Path(__file__).resolve().parents[2]
PATH = BACKEND / "migration_candidates/c01_telemetry_storage.py"
SPEC = importlib.util.spec_from_file_location("c01_proposal_under_test", PATH)
assert SPEC is not None and SPEC.loader is not None
proposal = importlib.util.module_from_spec(SPEC)
alembic_stub = ModuleType("alembic")
alembic_stub.op = MagicMock()  # type: ignore[attr-defined]
with patch.dict(sys.modules, {"alembic": alembic_stub}):
    SPEC.loader.exec_module(proposal)


def _signature(table: sa.Table) -> tuple:
    columns = tuple(
        (
            column.name,
            type(column.type).__name__,
            str(column.type),
            column.nullable,
            column.primary_key,
            tuple(sorted(foreign.target_fullname for foreign in column.foreign_keys)),
        )
        for column in table.columns
    )
    unique = tuple(
        sorted(
            (
                constraint.name,
                tuple(column.name for column in constraint.columns),
            )
            for constraint in table.constraints
            if isinstance(constraint, sa.UniqueConstraint)
        )
    )
    return columns, unique


class C01MigrationProposalTests(unittest.TestCase):
    def test_proposal_parent_and_any_promotion_preserve_frozen_functions(self) -> None:
        self.assertEqual(proposal.revision, "c01_telemetry_storage")
        self.assertEqual(proposal.down_revision, "a05_stream_frontier")
        for path in (BACKEND / "app/alembic/versions").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "revision"
                    for target in node.targets
                ):
                    if ast.literal_eval(node.value) == proposal.revision:
                        self.assertEqual(path.name, "c01_telemetry_storage.py")
                        frozen = ast.parse(PATH.read_text(encoding="utf-8"))

                        def functions(parsed):
                            return [
                                ast.dump(item, include_attributes=False)
                                for item in parsed.body
                                if isinstance(item, ast.FunctionDef)
                            ]

                        self.assertEqual(functions(tree), functions(frozen))

    def test_all_ten_tables_columns_constraints_and_indexes_match_c_models(
        self,
    ) -> None:
        operations = MagicMock()
        with patch.object(proposal, "op", operations):
            proposal.upgrade()
        frozen = sa.MetaData()
        for call in operations.create_table.call_args_list:
            name, *items = call.args
            sa.Table(name, frozen, *items)
        expected = {**storage_meta.tables, **progress_meta.tables}
        self.assertEqual(len(expected), 10)
        self.assertEqual(set(frozen.tables), set(expected))
        for name, table in expected.items():
            with self.subTest(table=name):
                self.assertEqual(_signature(frozen.tables[name]), _signature(table))
        indexes = {
            (call.args[0], call.args[1], tuple(call.args[2]))
            for call in operations.create_index.call_args_list
        }
        expected_indexes = {
            (index.name, table.name, tuple(column.name for column in index.columns))
            for table in expected.values()
            for index in table.indexes
        }
        self.assertEqual(indexes, expected_indexes)
        operations.execute.assert_not_called()
        operations.bulk_insert.assert_not_called()

    def test_downgrade_drops_only_c_tables_in_dependency_order(self) -> None:
        operations = MagicMock()
        with patch.object(proposal, "op", operations):
            proposal.downgrade()
        self.assertEqual(
            [call.args[0] for call in operations.drop_table.call_args_list],
            [
                "telemetry_recovery_progress_candidate",
                "telemetry_snapshot_member_candidate",
                "telemetry_snapshot_candidate",
                "telemetry_candidate_outbox_conflict_candidate",
                "telemetry_candidate_outbox_candidate",
                "telemetry_evaluation_candidate",
                "telemetry_reception_reuse_candidate",
                "telemetry_reception_candidate",
                "telemetry_quality_tag_candidate",
                "telemetry_observation_candidate",
            ],
        )
        self.assertEqual(
            [call.args[0] for call in operations.drop_index.call_args_list],
            ["ix_c_candidate_quality_tag", "ix_c_candidate_history"],
        )
        operations.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
