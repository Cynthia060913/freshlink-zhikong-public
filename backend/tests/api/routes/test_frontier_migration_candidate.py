"""A05 schema equivalence/lineage; actual PG upgrade lives in shared-chain proof."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import sqlalchemy as sa
from alembic.script import ScriptDirectory

from app.modules.iot.stream_frontier_models import metadata

BACKEND = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "a05_migration_under_test", BACKEND / "app/alembic/versions/a05_stream_frontier.py"
)
assert SPEC is not None and SPEC.loader is not None
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)


def test_a05_has_one_explicit_parent_and_unique_active_head():
    script = ScriptDirectory(str(BACKEND / "app/alembic"))
    assert script.get_heads() == ["c01_telemetry_storage"]
    assert (
        script.get_revision("c01_telemetry_storage").down_revision == candidate.revision
    )
    assert candidate.down_revision == "a04_transport_storage"
    lineage = {revision.revision for revision in script.walk_revisions()}
    assert {
        "a03_control_storage",
        "b01_alerts_storage",
        "a04_transport_storage",
    } <= lineage


def test_a05_ddl_matches_exact_a_metadata_and_does_not_seed_origins():
    operations = MagicMock()
    with patch.object(candidate, "op", operations):
        candidate.upgrade()
    actual = sa.MetaData()
    for call in operations.create_table.call_args_list:
        name, *items = call.args
        sa.Table(name, actual, *items)
    assert set(actual.tables) == set(metadata.tables)
    for name, table in actual.tables.items():
        model = metadata.tables[name]
        assert [
            (c.name, str(c.type), c.nullable, c.primary_key) for c in table.columns
        ] == [(c.name, str(c.type), c.nullable, c.primary_key) for c in model.columns]
        for kind in (sa.CheckConstraint, sa.UniqueConstraint, sa.ForeignKeyConstraint):

            def signatures(item):
                if isinstance(item, sa.CheckConstraint):
                    return str(item.sqltext)
                if isinstance(item, sa.ForeignKeyConstraint):
                    return tuple(
                        (entry.parent.name, entry.target_fullname)
                        for entry in item.elements
                    )
                return tuple(column.name for column in item.columns)

            assert {
                signatures(c) for c in table.constraints if isinstance(c, kind)
            } == {signatures(c) for c in model.constraints if isinstance(c, kind)}
        assert all(c.server_default is None for c in table.columns)
    operations.execute.assert_not_called()
    operations.bulk_insert.assert_not_called()


def test_a05_downgrade_only_drops_two_empty_candidate_tables_in_reverse_order():
    operations = MagicMock()
    with patch.object(candidate, "op", operations):
        candidate.downgrade()
    assert [call.args for call in operations.drop_table.call_args_list] == [
        ("iot_stream_admission",),
        ("iot_stream_frontier",),
    ]
    operations.execute.assert_not_called()
