"""End-to-end HTTP tests for the alerts routes.

Mount the B module's aggregated router on a private FastAPI
instance so the tests do not depend on A wiring the alerts router
into ``app/api/main.py``. Auth is bypassed by overriding
``get_current_active_superuser`` and ``get_current_user`` with no-op
dependencies — the goal is to verify the routing → service → model
chain, not to retest FastAPI's auth machinery.

A class-level shared in-memory SQLite engine keeps the schema
stable across the multiple requests a single test exercises.
"""

from __future__ import annotations

import unittest
import uuid
from collections.abc import Generator
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import deps as api_deps
from app.modules.alerts.routes import router as alerts_router

_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


def _init_schema() -> None:
    """Create all tables on the shared engine.

    SQLite does not have a native UUID type, but SQLAlchemy's
    Uuid() coerces on read/write so the rest of the code can keep
    using UUID objects. The PostgreSQL alembic migration (deferred
    until PR #4 merges) preserves the native Uuid type.
    """

    SQLModel.metadata.create_all(_ENGINE)


_init_schema()


class _NoopUser:
    id: uuid.UUID | None = None


def _override_db() -> Generator[Session]:
    with Session(_ENGINE) as session:
        yield session


def _override_current_user() -> _NoopUser:
    return _NoopUser()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(alerts_router)
    app.dependency_overrides[api_deps.get_db] = _override_db
    app.dependency_overrides[api_deps.get_current_user] = _override_current_user
    app.dependency_overrides[api_deps.get_current_active_superuser] = (
        _override_current_user
    )
    return TestClient(app)


class _IsolatedCase(unittest.TestCase):
    """Each test starts from a clean table set."""

    def setUp(self) -> None:
        # Truncate all tables rather than dropping — keeps the
        # schema cached and exercises the same path the service uses.
        with _ENGINE.begin() as conn:
            for table in reversed(SQLModel.metadata.sorted_tables):
                conn.exec_driver_sql(f'DELETE FROM "{table.name}"')


class WarehouseRouteCase(_IsolatedCase):
    def test_post_then_get_then_list(self) -> None:
        client = _client()
        r = client.post(
            "/warehouses/",
            json={"code": "WH-001", "name": "Cold Room A"},
        )
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertIn("id", body)
        wid = body["id"]
        self.assertEqual(body["code"], "wh-001")

        r2 = client.get(f"/warehouses/{wid}")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["name"], "Cold Room A")

        r3 = client.get("/warehouses/")
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(len(r3.json()), 1)

    def test_duplicate_code_returns_409(self) -> None:
        client = _client()
        first = client.post("/warehouses/", json={"code": "WH-DUP", "name": "x"})
        self.assertEqual(first.status_code, 201, first.text)
        r = client.post("/warehouses/", json={"code": "wh-dup", "name": "y"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("already exists", r.json()["detail"])

    def test_missing_warehouse_returns_404(self) -> None:
        client = _client()
        r = client.get("/warehouses/00000000-0000-0000-0000-000000000000")
        self.assertEqual(r.status_code, 404)


class BatchRouteCase(_IsolatedCase):
    def test_post_batch_under_warehouse(self) -> None:
        client = _client()
        wid = client.post("/warehouses/", json={"code": "WH", "name": "W"}).json()["id"]
        r = client.post(
            f"/warehouses/{wid}/batches",
            json={"code": "B-001", "product": "frozen-tuna"},
        )
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertIn("id", body)
        self.assertEqual(body["warehouse_id"], wid)

        r2 = client.get(f"/batches/{body['id']}")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["product"], "frozen-tuna")

    def test_batch_under_missing_warehouse_returns_404(self) -> None:
        client = _client()
        r = client.post(
            "/warehouses/00000000-0000-0000-0000-000000000000/batches",
            json={"code": "B", "product": "x"},
        )
        self.assertEqual(r.status_code, 404)


class RuleSnapshotRouteCase(_IsolatedCase):
    def _setup(self, client: TestClient) -> dict[str, Any]:
        wid = client.post("/warehouses/", json={"code": "WH", "name": "W"}).json()["id"]
        bid = client.post(
            f"/warehouses/{wid}/batches",
            json={"code": "B-001", "product": "x"},
        ).json()["id"]
        return {"wid": wid, "bid": bid}

    def _payload(self) -> dict[str, Any]:
        return {
            "version_id": "11111111-1111-1111-1111-111111111111",
            "rules": [
                {
                    "metric_code": "temperature_high",
                    "candidate_kind": "threshold_high",
                    "severity": "warning",
                    "threshold_low": None,
                    "threshold_high": 8.0,
                }
            ],
            "effective_start_at": "2026-10-11T09:00:00Z",
            "effective_end_at": None,
        }

    def test_post_snapshot_then_get_current(self) -> None:
        client = _client()
        ids = self._setup(client)
        r = client.post(f"/batches/{ids['bid']}/rule-snapshots", json=self._payload())
        self.assertEqual(r.status_code, 201, r.text)
        snap = r.json()
        # Public DTO must not carry is_current / superseded_by.
        self.assertNotIn("is_current", snap)
        self.assertNotIn("superseded_by", snap)
        self.assertEqual(snap["batch_id"], ids["bid"])

        r2 = client.get(f"/batches/{ids['bid']}/rule-snapshots/current")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["id"], snap["id"])

        r3 = client.get(f"/batches/{ids['bid']}/rule-snapshots")
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(len(r3.json()), 1)

    def test_second_snapshot_does_not_mutate_first(self) -> None:
        """Two inserts, two rows; GET /current returns the second.
        The first row remains a separate, addressable historical
        snapshot — neither row carries a stored "is_current" flag
        or a "superseded_by" link.
        """
        client = _client()
        ids = self._setup(client)
        first = client.post(
            f"/batches/{ids['bid']}/rule-snapshots", json=self._payload()
        ).json()
        # Different version_id + different rule set → different
        # signature → service creates a new row (no mutation of the
        # first; both rows survive as immutable history).
        second_payload = self._payload()
        second_payload["version_id"] = "22222222-2222-2222-2222-222222222222"
        second_payload["rules"][0]["threshold_high"] = 9.0
        # Same metric + overlapping interval → must explicitly
        # supersede the first snapshot (F02 overlap rejection).
        second_payload["supersedes_snapshot_ids"] = [first["id"]]
        second = client.post(
            f"/batches/{ids['bid']}/rule-snapshots", json=second_payload
        ).json()

        self.assertNotEqual(first["id"], second["id"])
        # No stored flag fields.
        for row in (first, second):
            self.assertNotIn("is_current", row)
            self.assertNotIn("superseded_by", row)

        current = client.get(f"/batches/{ids['bid']}/rule-snapshots/current").json()
        self.assertEqual(current["id"], second["id"])

        history = client.get(f"/batches/{ids['bid']}/rule-snapshots").json()
        self.assertEqual(len(history), 2)
        by_id = {row["id"]: row for row in history}
        # The first row's content is preserved (append-only).
        self.assertEqual(by_id[first["id"]]["signature"], first["signature"])
        self.assertEqual(by_id[first["id"]]["rules"], first["rules"])
        self.assertEqual(by_id[first["id"]]["created_at"], first["created_at"])

        # And the second row is reachable by id.
        r = client.get(f"/rule-snapshots/{second['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["id"], second["id"])

    def test_duplicate_signature_returns_409(self) -> None:
        client = _client()
        ids = self._setup(client)
        client.post(f"/batches/{ids['bid']}/rule-snapshots", json=self._payload())
        r = client.post(f"/batches/{ids['bid']}/rule-snapshots", json=self._payload())
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("signature", r.json()["detail"])

    def test_current_snapshot_for_missing_batch_is_404(self) -> None:
        """get_current_snapshot verifies the batch exists; a missing
        batch must surface as 404, never as an unhandled 500
        (PR #10 review comment 6110998037, item 2)."""
        client = _client()
        r = client.get(
            "/batches/00000000-0000-0000-0000-000000000000/rule-snapshots/current"
        )
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("not found", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
