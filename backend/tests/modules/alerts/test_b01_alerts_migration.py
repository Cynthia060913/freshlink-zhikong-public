"""B01 base migration regressions (``b01_alerts_storage``).

The migration must chain on the UNIQUE published A chain
(``a03_control_storage``) — no parallel head, no production create_all —
and deliver the six ``alerts_*`` tables without touching any A table
or its data.

What is verified here and where:

- **Chain containment, not "final head"** — B1 must chain on the
  UNIQUE published A chain (``a03_control_storage``) and every alembic
  head must descend from ``b01_alerts_storage``. The tests deliberately
  do NOT require the whole-repo head to equal B1: a legitimate C
  successor revision must keep every B regression green. The step
  upgrade target is pinned to B1 (never ``head``), and a synthetic
  successor probe proves a C revision chained on B1 neither breaks the
  B assertions nor lets the fixed-target upgrade overshoot.
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
- **PostgreSQL live checks — dedicated throwaway DB** — only with the
  explicit opt-in flag ``B01_LIVE_PG_TESTS=1`` passed by the CONTROLLED
  TEST ENTRY (the CI ``test-backend`` job env; developers may export
  it). Default OFF → skip, reported untested. with the flag set, a
  non-PG or unreachable server FAILS instead of a green skip. The
  checks create a RANDOM per-run database on that server, refuse to
  reuse an existing name (nothing is dropped on collision), migrate
  ONLY their own database up to B1 over the real PG chain, assert the
  PG-only pieces for real, and drop exactly what this run created in
  ``addClassCleanup`` teardown (registered right after each resource,
  so mid-setup failures still clean up). The configured application
  database is never written to; the append-only trigger probe runs
  inside one transaction with an explicit rollback, verified by
  counting zero leftover rows.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
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


def _ancestry(script: ScriptDirectory, revision_id: str) -> set[str]:
    """All revisions from ``revision_id`` down to the chain base."""

    seen: set[str] = set()
    rev = script.get_revision(revision_id)
    while rev is not None and rev.revision != "base":
        seen.add(rev.revision)
        if rev.down_revision is None:
            break
        rev = script.get_revision(rev.down_revision)
    return seen


class ChainContainmentCase(unittest.TestCase):
    """B1 must live on the unique chain — but NOT be forced to remain
    the final head: a legitimate C successor keeps everything green."""

    def test_every_head_descends_from_b01(self) -> None:
        script = ScriptDirectory(str(_BACKEND_DIR / "app" / "alembic"))
        heads = list(script.get_heads())
        # The unique chain must have EXACTLY ONE head: several heads
        # that all happen to descend from B01 would still be a fork.
        self.assertEqual(
            len(heads), 1, f"chain must have exactly one head, got {heads}"
        )
        for head in heads:
            self.assertIn(
                _HEAD,
                _ancestry(script, head),
                f"head {head} does not descend from b01_alerts_storage",
            )

    def test_b01_chains_on_the_published_a_chain_end(self) -> None:
        script = ScriptDirectory(str(_BACKEND_DIR / "app" / "alembic"))
        revision = script.get_revision(_HEAD)
        assert revision is not None
        self.assertEqual(revision.down_revision, _CHAIN_END)
        # …and that A chain end itself descends from the base chain.
        self.assertIn("e2412789c190", _ancestry(script, _CHAIN_END))


# A test-only successor that could never be published: it exists ONLY
# inside a temp copy of the alembic script directory.
_SUCCESSOR_ID = "c01_successor_probe"
_SUCCESSOR_SRC = '''"""Synthetic C-successor probe (test fixture, never published)."""

revision = "c01_successor_probe"
down_revision = "b01_alerts_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: only the chain shape is under test."""


def downgrade() -> None:
    """No-op: only the chain shape is under test."""
'''


class SuccessorChainCompatCase(_SqliteMigrationCase):
    """With a legitimate C successor chained on B1, the B-step
    regressions still pass: heads() stops being B1 (the old "only head
    == b01" assertion would have failed), B's fixed-target upgrade
    stops exactly at B1, and the successor neither breaks B's schema
    nor survives B1's downgrade."""

    def setUp(self) -> None:
        super().setUp()
        self.script_dir = Path(tempfile.mkdtemp(prefix="b01-successor-"))
        shutil.copytree(
            _BACKEND_DIR / "app" / "alembic", self.script_dir, dirs_exist_ok=True
        )
        versions = self.script_dir / "versions"
        versions.mkdir(exist_ok=True)
        (versions / f"{_SUCCESSOR_ID}.py").write_text(_SUCCESSOR_SRC, encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.script_dir, ignore_errors=True)
        super().tearDown()

    def _successor_cfg(self) -> Config:
        cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(self.script_dir))
        return cfg

    def test_b_steps_stay_green_with_a_successor_revision(self) -> None:
        script = ScriptDirectory(str(self.script_dir))
        # Single head is now the successor, NOT b01 — the old
        # "heads == [b01]" assertion would break exactly here.
        self.assertEqual(list(script.get_heads()), [_SUCCESSOR_ID])
        self.assertIn(_HEAD, _ancestry(script, _SUCCESSOR_ID))

        cfg = self._successor_cfg()
        command.stamp(cfg, _CHAIN_END)
        # Fixed target: upgrade stops at B1 even though a successor
        # exists — it must not run past B's own revision.
        command.upgrade(cfg, _HEAD)
        insp = inspect(self._engine())
        self.assertTrue(set(_ALERTS_TABLES).issubset(set(insp.get_table_names())))
        with self._engine().connect() as conn:
            version = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
        self.assertEqual(version, _HEAD)

        # The successor can still be reached by name/head afterwards…
        command.upgrade(cfg, "head")
        with self._engine().connect() as conn:
            version = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
        self.assertEqual(version, _SUCCESSOR_ID)
        # …B's six tables survive it…
        self.assertTrue(
            set(_ALERTS_TABLES).issubset(set(inspect(self._engine()).get_table_names()))
        )
        # …and downgrading back to B1 (and then a03) still works.
        command.downgrade(cfg, _HEAD)
        with self._engine().connect() as conn:
            version = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
        self.assertEqual(version, _HEAD)
        command.downgrade(cfg, _CHAIN_END)
        self.assertFalse(
            set(_ALERTS_TABLES) & set(inspect(self._engine()).get_table_names())
        )


class EmptyStepUpgradeCase(_SqliteMigrationCase):
    def test_stamp_and_upgrade_creates_all_six_tables(self) -> None:
        cfg = _alembic_cfg()
        command.stamp(cfg, _CHAIN_END)
        command.upgrade(cfg, _HEAD)
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
        command.upgrade(cfg, _HEAD)
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
        command.upgrade(cfg, _HEAD)

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
        command.upgrade(cfg, _HEAD)
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
        command.upgrade(cfg, _HEAD)

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


_LIVE_PG_FLAG = "B01_LIVE_PG_TESTS"
_LIVE_PG_DB_PREFIX = "b01_live_pg_test"


def _dedicated_db_name() -> str:
    """Random, traceable, per-run database name.

    Never a fixed name: a name collision with a database that this run
    did not create must be detectable (and refused), not reused.
    """

    return f"{_LIVE_PG_DB_PREFIX}_{os.getpid()}_{uuid.uuid4().hex[:12]}"


def _server_reachable(url: object) -> bool:
    # Pass the URL OBJECT to create_engine: str(URL) renders the
    # password as ``***`` and would make reachability checks fail.
    try:
        engine = create_engine(
            url,  # type: ignore[arg-type]
            connect_args={"connect_timeout": 3},
            isolation_level="AUTOCOMMIT",
        )
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        finally:
            engine.dispose()
    except Exception:
        return False


class PostgresLiveChecksCase(unittest.TestCase):
    """Real-PG checks on a SELF-CREATED, DEDICATED throwaway database.

    Explicit opt-in (``B01_LIVE_PG_TESTS=1`` passed by the CONTROLLED
    TEST ENTRY — the CI ``test-backend`` job env; developers may export
    it themselves). Default is OFF and the checks skip (reported
    untested). With the flag set, an unavailable/non-PG server is a
    FAILURE, never a green skip.

    The case creates a RANDOM per-run database on that server, refuses
    to reuse a database name that already exists (nothing is dropped
    on a collision), migrates ONLY its own database over the real PG
    chain up to B1, and drops exactly the database this run created in
    class cleanup. All resources (engines, settings, database) are
    registered with ``addClassCleanup`` immediately after acquisition,
    so a mid-setup exception still cleans up. The configured
    application database itself is never written to.
    """

    engine: Engine
    admin_engine: Engine
    _saved_url: object

    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get(_LIVE_PG_FLAG) != "1":
            raise unittest.SkipTest(
                "B01 live PG checks not opted in (B01_LIVE_PG_TESTS != 1)"
            )
        url = make_url(str(settings.DATABASE_URL))
        if url.get_backend_name() != "postgresql":
            raise AssertionError(
                "B01_LIVE_PG_TESTS=1 but the configured DATABASE_URL is not "
                "PostgreSQL — refusing to report a green skip"
            )
        admin_url = url.set(database="postgres")
        if not _server_reachable(admin_url):
            raise AssertionError(
                "B01_LIVE_PG_TESTS=1 but no PostgreSQL server is reachable "
                "— refusing to report a green skip"
            )
        cls._saved_url = settings.DATABASE_URL
        cls.addClassCleanup(cls._restore_settings, cls)
        cls.admin_engine = create_engine(
            admin_url,
            connect_args={"connect_timeout": 3},
            isolation_level="AUTOCOMMIT",
        )
        cls.addClassCleanup(cls._dispose_engine, cls.admin_engine)
        name = _dedicated_db_name()
        with cls.admin_engine.connect() as conn:
            existing = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": name},
            ).scalar()
        if existing:
            raise RuntimeError(f"refusing to reuse an existing database named {name}")
        with cls.admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        # Registered ONLY after our own database was created: the drop
        # can never touch a database this run did not create.
        cls.addClassCleanup(cls._drop_dedicated_db, cls, name)
        dedicated = url.set(database=name)
        cls.engine = create_engine(dedicated)
        cls.addClassCleanup(cls._dispose_engine, cls.engine)
        # settings needs a STRING; render explicitly unmasked. In-memory
        # only — never printed, logged, or written anywhere.
        setattr(  # noqa: B010
            settings,
            "DATABASE_URL",
            dedicated.render_as_string(hide_password=False),
        )
        # Real full chain (base → … → b01) on the dedicated PG DB; the
        # target is pinned to B1 so a future C successor never makes
        # these checks run past B's revision.
        command.upgrade(_alembic_cfg(), _HEAD)

    @staticmethod
    def _restore_settings(cls: type[PostgresLiveChecksCase]) -> None:
        setattr(settings, "DATABASE_URL", cls._saved_url)  # noqa: B010

    @staticmethod
    def _dispose_engine(engine: Engine) -> None:
        engine.dispose()

    @staticmethod
    def _drop_dedicated_db(cls: type[PostgresLiveChecksCase], name: str) -> None:
        with cls.admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))

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
        # Everything inside ONE transaction with an EXPLICIT rollback
        # on failure: if the UPDATE raises (it must), nothing survives.
        raised: Exception | None = None
        conn = self.engine.connect()
        try:
            conn.begin()
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
            conn.commit()
        except IntegrityError as exc:
            raised = exc
            conn.rollback()
        finally:
            conn.rollback()  # idempotent no-op once already rolled back
            conn.close()
        self.assertIsNotNone(raised, "UPDATE on the snapshot table was not blocked")
        # Rollback guarantee, verified: zero leftover synthetic rows.
        with self.engine.connect() as check:
            leftovers = check.execute(
                text("SELECT count(*) FROM alerts_warehouse WHERE code = 'wh-b01-pg'")
            ).scalar()
        self.assertEqual(leftovers, 0)


def _fake_admin_engine(
    executed: list[str],
    exists: bool,
    create_error: Exception | None = None,
) -> mock.MagicMock:
    """Admin-engine double that records SQL and can fail on CREATE."""

    engine = mock.MagicMock(name="admin_engine")

    @contextmanager
    def connect() -> Iterator[mock.MagicMock]:
        conn = mock.MagicMock(name="conn")

        def execute(_statement: object, _parameters: object = None) -> mock.MagicMock:
            sql = str(_statement)
            executed.append(sql)
            if "CREATE DATABASE" in sql and create_error is not None:
                raise create_error
            result = mock.MagicMock()
            if "FROM pg_database" in sql:
                result.scalar.return_value = 1 if exists else None
            return result

        conn.execute.side_effect = execute
        yield conn

    engine.connect = connect
    return engine


class LivePgGuardCase(unittest.TestCase):
    """Mock regressions for the dedicated-DB safety guards.

    Proves, without any real server, that: opt-in OFF never connects;
    a pre-existing same-named database is refused and NOT dropped or
    migrated; a failed CREATE never leads to a DROP of an unknown
    database.
    """

    def setUp(self) -> None:
        self._env = os.environ.pop(_LIVE_PG_FLAG, None)
        self._saved = settings.DATABASE_URL
        # Clearly fake test-only credentials; never a real account.
        setattr(  # noqa: B010
            settings,
            "DATABASE_URL",
            "postgresql://b01_guard_test:b01_guard_test@localhost:5432/app",
        )
        self._module = sys.modules[__name__]

    def tearDown(self) -> None:
        PostgresLiveChecksCase.doClassCleanups()
        for name in ("engine", "admin_engine", "_saved_url"):
            if name in PostgresLiveChecksCase.__dict__:
                delattr(PostgresLiveChecksCase, name)
        setattr(settings, "DATABASE_URL", self._saved)  # noqa: B010
        if self._env is None:
            os.environ.pop(_LIVE_PG_FLAG, None)
        else:
            os.environ[_LIVE_PG_FLAG] = self._env

    def test_opt_in_off_never_connects_or_creates(self) -> None:
        os.environ.pop(_LIVE_PG_FLAG, None)
        with mock.patch.object(self._module, "create_engine") as engine_factory:
            with self.assertRaises(unittest.SkipTest):
                PostgresLiveChecksCase.setUpClass()
        engine_factory.assert_not_called()

    def test_existing_same_named_db_is_refused_and_never_dropped(self) -> None:
        os.environ[_LIVE_PG_FLAG] = "1"
        executed: list[str] = []
        fake = _fake_admin_engine(executed, exists=True)
        with (
            mock.patch.object(self._module, "create_engine", return_value=fake),
            mock.patch.object(self._module, "_server_reachable", return_value=True),
        ):
            with self.assertRaises(RuntimeError):
                PostgresLiveChecksCase.setUpClass()
            PostgresLiveChecksCase.doClassCleanups()
        executed_upper = " ".join(executed).upper()
        self.assertIn("FROM PG_DATABASE", executed_upper)
        self.assertNotIn("DROP DATABASE", executed_upper)
        self.assertNotIn("CREATE DATABASE", executed_upper)

    def test_failed_create_never_drops_an_unknown_db(self) -> None:
        os.environ[_LIVE_PG_FLAG] = "1"
        executed: list[str] = []
        fake = _fake_admin_engine(
            executed, exists=False, create_error=RuntimeError("create failed")
        )
        with (
            mock.patch.object(self._module, "create_engine", return_value=fake),
            mock.patch.object(self._module, "_server_reachable", return_value=True),
        ):
            with self.assertRaises(RuntimeError):
                PostgresLiveChecksCase.setUpClass()
            PostgresLiveChecksCase.doClassCleanups()
        executed_upper = " ".join(executed).upper()
        self.assertIn("CREATE DATABASE", executed_upper)
        self.assertNotIn("DROP DATABASE", executed_upper)


if __name__ == "__main__":
    unittest.main()
