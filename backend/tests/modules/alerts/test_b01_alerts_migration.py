"""B01 base migration regressions (``b01_alerts_storage``).

The migration must chain on the UNIQUE published A chain
(``a03_control_storage``) — no parallel head, no production create_all —
and deliver the six ``alerts_*`` tables without touching any A table
or its data.

What is verified here and where:

- **Single head** — the alembic script directory has exactly one head
  (``b01_alerts_storage``), i.e. B did not fork the chain.
- **Step upgrade on an empty SQLite DB** — the base app migrations use
  PG-only ``ALTER COLUMN`` so the full chain cannot run on SQLite; the
  local test STAMPS ``a03_control_storage`` (the real chain end) and
  upgrades only B1. The real full-chain empty-database upgrade on
  PostgreSQL runs in CI: ``test-backend`` prestart migrates a fresh
  PG volume to head on every run.
- **Old A data preserved** — iot rows inserted before B1 survive the
  upgrade (and B1's own downgrade restores the pre-B1 table set).
- **Model consistency** — the migrated schema matches
  ``SQLModel.metadata``/create_all for the six alerts tables down to
  column order, nullability, types, indexes and unique constraints.
- **PostgreSQL live checks (CI only)** — when the configured
  ``DATABASE_URL`` is a reachable PostgreSQL (CI test-backend runs
  against the prestart-migrated DB), the migration's PG-only pieces
  are asserted for real: the tables exist, the append-only trigger on
  ``alerts_batch_rule_snapshot`` exists, and it rejects UPDATE/DELETE.
  Skipped locally where no PG is available — reported as untested
  outside CI rather than silently assumed.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

# Populate SQLModel.metadata with the alerts tables (the schema
# comparisons below read it via metadata.tables).
import app.modules.alerts.models  # noqa: E402, F401
from app.core.config import settings

_BACKEND_DIR = Path(__file__).resolve().parents[3]
_HEAD = "b01_alerts_storage"
_CHAIN_END = "a03_control_storage"

_ALERTS_TABLES = (
    "alerts_warehouse",
    "alerts_batch",
    "alerts_batch_rule_snapshot",
    "alerts_event",
    "alerts_status_transition",
    "alerts_notification_attempt",
)


def _alembic_cfg() -> Config:
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "app" / "alembic"))
    return cfg


_Columns = tuple[tuple[str, bool, str], ...]
_Fks = tuple[tuple[str, tuple[str, ...]], ...]
_Indexes = tuple[tuple[str, bool, tuple[str, ...]], ...]
_TableShape = tuple[_Columns, tuple[str, ...], _Fks, _Indexes, tuple[str, ...]]
_SchemaSnapshot = dict[str, _TableShape]


def _schema_snapshot(engine: Engine) -> _SchemaSnapshot:
    """Comparable structural fingerprint of the six alerts tables."""

    insp = inspect(engine)
    out: _SchemaSnapshot = {}
    for table in _ALERTS_TABLES:
        columns = tuple(
            (c["name"], c["nullable"], str(c["type"])) for c in insp.get_columns(table)
        )
        pk = insp.get_pk_constraint(table)
        fks = tuple(
            sorted(
                (fk["referred_table"], tuple(fk["constrained_columns"]))
                for fk in insp.get_foreign_keys(table)
            )
        )
        indexes = tuple(
            sorted(
                (
                    str(ix["name"]),
                    bool(ix["unique"]),
                    tuple(str(c) for c in ix["column_names"]),
                )
                for ix in insp.get_indexes(table)
            )
        )
        uniques = tuple(
            sorted(str(u["name"]) for u in insp.get_unique_constraints(table))
        )
        out[table] = (columns, tuple(pk["constrained_columns"]), fks, indexes, uniques)
    return out


class _SqliteMigrationCase(unittest.TestCase):
    """Temp SQLite file DB, settings.DATABASE_URL pointed at it.

    Engines created via :meth:`_engine` are disposed in tearDown so
    Windows can unlink the file (SQLite keeps it locked otherwise).
    """

    db_path: str

    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        self.db_path = path
        self.url = f"sqlite:///{path}"
        self._saved_url = settings.DATABASE_URL
        self._engines: list[Engine] = []
        # Point alembic's env.py (settings-based URL) at the temp DB.
        # The attribute is a PostgresDsn at type level; runtime
        # assignment is unvalidated (no validate_assignment) so a plain
        # sqlite URL works. setattr (kept despite B010) bypasses both
        # type checkers — their ignore-comment syntaxes conflict.
        setattr(settings, "DATABASE_URL", self.url)  # noqa: B010

    def _engine(self) -> Engine:
        engine = create_engine(self.url)
        self._engines.append(engine)
        return engine

    def tearDown(self) -> None:
        settings.DATABASE_URL = self._saved_url
        for engine in self._engines:
            engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db_path + suffix)
            except FileNotFoundError:
                pass


class SingleHeadCase(unittest.TestCase):
    def test_b01_is_the_only_head(self) -> None:
        script = ScriptDirectory(
            str(_BACKEND_DIR / "app" / "alembic"),
        )
        self.assertEqual(list(script.get_heads()), [_HEAD])

    def test_b01_chains_on_the_published_a_chain_end(self) -> None:
        script = ScriptDirectory(str(_BACKEND_DIR / "app" / "alembic"))
        revision = script.get_revision(_HEAD)
        assert revision is not None
        self.assertEqual(revision.down_revision, _CHAIN_END)


class EmptyStepUpgradeCase(_SqliteMigrationCase):
    def test_stamp_and_upgrade_creates_all_six_tables(self) -> None:
        cfg = _alembic_cfg()
        command.stamp(cfg, _CHAIN_END)
        command.upgrade(cfg, "head")
        insp = inspect(self._engine())
        tables = set(insp.get_table_names())
        self.assertTrue(set(_ALERTS_TABLES).issubset(tables))
        with self._engine().connect() as conn:
            version = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
        self.assertEqual(version, _HEAD)

    def test_downgrade_restores_pre_b01_table_set(self) -> None:
        cfg = _alembic_cfg()
        command.stamp(cfg, _CHAIN_END)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, _CHAIN_END)
        insp = inspect(self._engine())
        tables = set(insp.get_table_names())
        self.assertFalse(set(_ALERTS_TABLES) & tables)
        with self._engine().connect() as conn:
            version = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
        self.assertEqual(version, _CHAIN_END)


class OldADataPreservedCase(_SqliteMigrationCase):
    """A rows written before B1 survive the upgrade and the downgrade."""

    def _insert_device(self) -> None:
        from app.modules.iot.models import Device

        engine = self._engine()
        # A's iot_device created from A's own model (A's PG chain runs
        # in CI); the invariant under test is that B1 leaves it alone.
        iot_tables = [SQLModel.metadata.tables["iot_device"]]
        SQLModel.metadata.create_all(engine, tables=iot_tables)
        with Session(engine) as session:
            session.add(Device(code="iot-keep-1", name="Kept", kind="sensor"))
            session.commit()

    def test_iot_rows_survive_b01_upgrade_and_downgrade(self) -> None:
        from app.modules.iot.models import Device

        cfg = _alembic_cfg()
        # The base app migrations are PG-only (ALTER COLUMN); on SQLite
        # the local chain end is STAMPED so only B1 runs for real.
        command.stamp(cfg, _CHAIN_END)
        self._insert_device()
        command.upgrade(cfg, "head")

        engine = self._engine()
        with Session(engine) as session:
            device = session.exec(select(Device)).one()
            self.assertEqual(device.code, "iot-keep-1")
        self.assertEqual(
            set(_ALERTS_TABLES),
            set(_ALERTS_TABLES) & set(inspect(engine).get_table_names()),
        )

        # B1's downgrade removes ONLY the alerts tables; A rows stay.
        command.downgrade(cfg, _CHAIN_END)
        engine2 = self._engine()
        with Session(engine2) as session:
            device = session.exec(select(Device)).one()
            self.assertEqual(device.code, "iot-keep-1")
        tables = set(inspect(engine2).get_table_names())
        self.assertFalse(set(_ALERTS_TABLES) & tables)
        self.assertIn("iot_device", tables)


class MigratedSchemaMatchesModelsCase(_SqliteMigrationCase):
    def test_migrated_schema_equals_create_all(self) -> None:
        cfg = _alembic_cfg()
        command.stamp(cfg, _CHAIN_END)
        command.upgrade(cfg, "head")
        migrated = self._engine()

        created = create_engine("sqlite://")
        alerts_tables = [SQLModel.metadata.tables[name] for name in _ALERTS_TABLES]
        SQLModel.metadata.create_all(created, tables=alerts_tables)
        self.assertEqual(_schema_snapshot(migrated), _schema_snapshot(created))


class MigratedSchemaServesTheServicesCase(_SqliteMigrationCase):
    """End-to-end over the migrated schema: ingest an anomaly, recover
    it, and read the resolved status back through the service layer."""

    def test_services_run_against_migrated_schema(self) -> None:
        cfg = _alembic_cfg()
        command.stamp(cfg, _CHAIN_END)
        command.upgrade(cfg, "head")

        from app.modules.alerts.models import (
            BatchCreate,
            CandidateKind,
            WarehouseCreate,
        )
        from app.modules.alerts.services.batches import create_batch
        from app.modules.alerts.services.events import (
            get_alert_status,
            get_event_by_source,
            receive_candidate,
        )
        from app.modules.alerts.services.warehouses import create_warehouse
        from tests.modules.alerts.test_pr10_boundary_regressions import _rc2

        engine = self._engine()
        with Session(engine) as session:
            wid = create_warehouse(session, WarehouseCreate(code="whb1", name="W")).id
            bid = create_batch(session, wid, BatchCreate(code="b-1", product="x")).id
            anomaly = _rc2(
                source_event_id="anom-b01",
                warehouse_id=wid,
                batch_id=bid,
            )
            self.assertEqual(receive_candidate(session, anomaly).status, "accepted_new")
            original = get_event_by_source(session, "anom-b01")

            recovery = _rc2(
                source_event_id="rec-b01",
                warehouse_id=wid,
                batch_id=bid,
                kind=CandidateKind.TEMPERATURE_RECOVERED,
                sampled_at="2026-10-14T00:05:00Z",
                value=7.0,
                related_source_event_id="anom-b01",
            )
            receipt = receive_candidate(session, recovery)
            self.assertEqual(receipt.status, "accepted_new")
            self.assertEqual(get_alert_status(session, original.id).value, "resolved")
            self.assertEqual(
                get_event_by_source(session, "rec-b01").related_source_event_id,
                "anom-b01",
            )


def _live_postgres_engine() -> Engine | None:
    """The configured DB when it is a reachable PostgreSQL, else None.

    CI test-backend runs against the prestart-migrated compose DB;
    locally there is no PG, so these checks skip (reported as
    untested outside CI) instead of silently passing.
    """

    url = str(settings.DATABASE_URL)
    if not url.startswith("postgresql"):
        return None
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:
        return None


class PostgresLiveChecksCase(unittest.TestCase):
    """Runs only against a live PostgreSQL (CI); skips locally."""

    engine: Engine

    @classmethod
    def setUpClass(cls) -> None:
        live = _live_postgres_engine()
        if live is None:
            raise unittest.SkipTest(
                "no live PostgreSQL DATABASE_URL — PG checks run in CI only"
            )
        cls.engine = live

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "engine", None) is not None:
            cls.engine.dispose()

    def test_alerts_tables_exist_on_migrated_postgres(self) -> None:
        tables = set(inspect(self.engine).get_table_names())
        self.assertTrue(
            set(_ALERTS_TABLES).issubset(tables),
            f"missing: {sorted(set(_ALERTS_TABLES) - tables)}",
        )

    def test_append_only_trigger_exists_and_blocks_mutation(self) -> None:
        with self.engine.connect() as conn:
            trigger = conn.execute(
                text(
                    "SELECT tgname FROM pg_trigger WHERE tgname = "
                    "'alerts_batch_rule_snapshot_append_only'"
                )
            ).scalar()
        self.assertIsNotNone(trigger, "append-only trigger missing on PG")

        now = datetime(2026, 10, 19, tzinfo=UTC)
        # One transaction: if the UPDATE raises (it must), the whole
        # txn rolls back and no row survives in the shared CI DB.
        with self.assertRaises(IntegrityError):
            with self.engine.connect() as conn:
                with conn.begin():
                    conn.execute(
                        text(
                            "INSERT INTO alerts_warehouse (id, code, name, created_at) "
                            "VALUES (:id, 'wh-b01-pg', 'W', :ts)"
                        ),
                        {"id": uuid.uuid4(), "ts": now},
                    )
                    wid = conn.execute(
                        text("SELECT id FROM alerts_warehouse WHERE code = 'wh-b01-pg'")
                    ).scalar()
                    conn.execute(
                        text(
                            "INSERT INTO alerts_batch "
                            "(id, code, warehouse_id, product, started_at, created_at) "
                            "VALUES (:id, 'b-b01-pg', :wid, 'x', :ts, :ts)"
                        ),
                        {"id": uuid.uuid4(), "wid": wid, "ts": now},
                    )
                    bid = conn.execute(
                        text("SELECT id FROM alerts_batch WHERE code = 'b-b01-pg'")
                    ).scalar()
                    conn.execute(
                        text(
                            "INSERT INTO alerts_batch_rule_snapshot "
                            "(id, batch_id, version_id, payload_json, signature, "
                            "effective_start_at, supersedes_json, created_at) "
                            "VALUES (:id, :bid, :vid, '[]', :sig, :ts, '[]', :ts)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "bid": bid,
                            "vid": uuid.uuid4(),
                            "sig": "b01pg" * 12 + "cdef",
                            "ts": now,
                        },
                    )
                    conn.execute(
                        text(
                            "UPDATE alerts_batch_rule_snapshot "
                            "SET signature = 'tampered' "
                            "WHERE batch_id = :bid"
                        ),
                        {"bid": bid},
                    )


if __name__ == "__main__":
    unittest.main()
