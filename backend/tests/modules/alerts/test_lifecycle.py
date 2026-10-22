"""HTTP + service regressions for the alert lifecycle routes.

Covers the B2 disposition path: read status/history as any active
user, acknowledge (open -> acknowledged) as superuser only, idempotent
repeat without a second transition row, rejection on a resolved alert
(409, nothing written), recovery-driven resolution reflected in the
same history, and 404 for unknown alerts.

The aggregated B router is mounted on a private FastAPI instance; auth
overrides mirror ``test_alerts_routes`` (success path) plus an explicit
deny override for the permission test.
"""

from __future__ import annotations

import unittest
import uuid
from collections.abc import Generator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import deps as api_deps
from app.modules.alerts.models import CandidateKind
from app.modules.alerts.routes import router as alerts_router
from tests.modules.alerts.test_pr10_boundary_regressions import _rc2

_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SQLModel.metadata.create_all(_ENGINE)


class _NoopUser:
    id: uuid.UUID | None = None
    is_superuser: bool = True


def _override_db() -> Generator[Session]:
    with Session(_ENGINE) as session:
        yield session


def _override_current_user() -> _NoopUser:
    return _NoopUser()


def _deny_superuser() -> None:
    raise HTTPException(status_code=403, detail="superuser required")


def _client(*, deny_ack: bool = False) -> TestClient:
    app = FastAPI()
    app.include_router(alerts_router)
    app.dependency_overrides[api_deps.get_db] = _override_db
    app.dependency_overrides[api_deps.get_current_user] = _override_current_user
    if deny_ack:
        app.dependency_overrides[api_deps.get_current_active_superuser] = (
            _deny_superuser
        )
    else:
        app.dependency_overrides[api_deps.get_current_active_superuser] = (
            _override_current_user
        )
    return TestClient(app)


class _IsolatedCase(unittest.TestCase):
    def setUp(self) -> None:
        with _ENGINE.begin() as conn:
            for table in reversed(SQLModel.metadata.sorted_tables):
                conn.exec_driver_sql(f'DELETE FROM "{table.name}"')


def _create_alert(client: TestClient, *, source: str = "anom-life-1") -> dict[str, Any]:
    """Warehouse + batch + one temperature_high candidate → alert event."""

    wid = client.post(
        "/warehouses/", json={"code": f"WH-{source}", "name": "W"}
    ).json()["id"]
    bid = client.post(
        f"/warehouses/{wid}/batches", json={"code": f"B-{source}", "product": "x"}
    ).json()["id"]
    candidate = _rc2(
        source_event_id=source,
        warehouse_id=uuid.UUID(wid),
        batch_id=uuid.UUID(bid),
    )
    r = client.post("/candidates", json=candidate.model_dump(mode="json"))
    assert r.status_code == 201, r.text
    return {"warehouse_id": wid, "batch_id": bid, "event_id": r.json()["event_id"]}


def _send_recovery(
    client: TestClient,
    ids: dict[str, Any],
    *,
    source: str,
    related: str = "anom-life-1",
) -> Any:
    recovery = _rc2(
        source_event_id=source,
        warehouse_id=uuid.UUID(ids["warehouse_id"]),
        batch_id=uuid.UUID(ids["batch_id"]),
        kind=CandidateKind.TEMPERATURE_RECOVERED,
        sampled_at="2026-10-14T00:05:00Z",
        value=7.0,
        related_source_event_id=related,
    )
    return client.post("/candidates", json=recovery.model_dump(mode="json"))


class LifecycleRouteCase(_IsolatedCase):
    def test_status_starts_open_with_empty_history(self) -> None:
        client = _client()
        ids = _create_alert(client)
        r = client.get(f"/alerts/{ids['event_id']}/status")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "open")
        self.assertEqual(body["transitions"], [])
        self.assertFalse(body["transition_created"])

    def test_acknowledge_then_idempotent_repeat(self) -> None:
        client = _client()
        ids = _create_alert(client)

        r = client.post(f"/alerts/{ids['event_id']}/acknowledge")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "acknowledged")
        self.assertTrue(body["transition_created"])
        self.assertEqual(len(body["transitions"]), 1)
        transition = body["transitions"][0]
        self.assertEqual(transition["from_status"], "open")
        self.assertEqual(transition["to_status"], "acknowledged")

        # Repeat: idempotent, acknowledged, but NO second row.
        r2 = client.post(f"/alerts/{ids['event_id']}/acknowledge")
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["status"], "acknowledged")
        self.assertFalse(r2.json()["transition_created"])
        self.assertEqual(len(r2.json()["transitions"]), 1)

    def test_acknowledge_resolved_returns_409_and_writes_nothing(self) -> None:
        client = _client()
        ids = _create_alert(client)
        self.assertEqual(
            client.post(f"/alerts/{ids['event_id']}/acknowledge").status_code, 200
        )
        rec = _send_recovery(client, ids, source="rec-life-1")
        self.assertEqual(rec.status_code, 201, rec.text)

        before = client.get(f"/alerts/{ids['event_id']}/status").json()
        self.assertEqual(before["status"], "resolved")
        self.assertEqual(len(before["transitions"]), 2)

        r = client.post(f"/alerts/{ids['event_id']}/acknowledge")
        self.assertEqual(r.status_code, 409, r.text)
        after = client.get(f"/alerts/{ids['event_id']}/status").json()
        self.assertEqual(after["transitions"], before["transitions"])

    def test_full_history_open_ack_recovered_resolved(self) -> None:
        client = _client()
        ids = _create_alert(client)
        client.post(f"/alerts/{ids['event_id']}/acknowledge")
        rec = _send_recovery(client, ids, source="rec-life-2")
        self.assertEqual(rec.status_code, 201, rec.text)

        body = client.get(f"/alerts/{ids['event_id']}/status").json()
        self.assertEqual(body["status"], "resolved")
        history = [(t["from_status"], t["to_status"]) for t in body["transitions"]]
        self.assertEqual(
            history, [("open", "acknowledged"), ("acknowledged", "resolved")]
        )
        # Resolution traceability: the transition points at the
        # recovery candidate that caused it.
        self.assertEqual(
            body["transitions"][1]["trigger_source_event_id"], "rec-life-2"
        )

    def test_unknown_alert_404_on_read_and_acknowledge(self) -> None:
        client = _client()
        missing = "00000000-0000-0000-0000-000000000000"
        self.assertEqual(client.get(f"/alerts/{missing}/status").status_code, 404)
        self.assertEqual(client.post(f"/alerts/{missing}/acknowledge").status_code, 404)

    def test_normal_user_reads_but_cannot_acknowledge(self) -> None:
        admin = _client()
        ids = _create_alert(admin)

        reader = _client(deny_ack=True)
        r = reader.get(f"/alerts/{ids['event_id']}/status")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "open")

        denied = reader.post(f"/alerts/{ids['event_id']}/acknowledge")
        self.assertEqual(denied.status_code, 403, denied.text)
        # No row written by the denied attempt.
        after = admin.get(f"/alerts/{ids['event_id']}/status").json()
        self.assertEqual(after["transitions"], [])


class AlertListCase(_IsolatedCase):
    def test_list_shows_alert_kinds_only_with_current_status(self) -> None:
        client = _client()
        acked = _create_alert(client, source="anom-list-1")
        resolved = _create_alert(client, source="anom-list-2")
        client.post(f"/alerts/{acked['event_id']}/acknowledge")
        rec = _send_recovery(
            client, resolved, source="rec-list-1", related="anom-list-2"
        )
        self.assertEqual(rec.status_code, 201, rec.text)

        r = client.get("/alerts/")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["count"], 2)
        by_id = {row["alert_event_id"]: row for row in body["data"]}
        self.assertEqual(by_id[acked["event_id"]]["status"], "acknowledged")
        self.assertEqual(by_id[resolved["event_id"]]["status"], "resolved")
        # The recovery candidate itself is traceability, not an alert.
        self.assertEqual(
            {row["source_event_id"] for row in body["data"]},
            {"anom-list-1", "anom-list-2"},
        )

    def test_list_filters_by_state_and_severity(self) -> None:
        client = _client()
        acked = _create_alert(client, source="anom-filter-1")
        resolved = _create_alert(client, source="anom-filter-2")
        client.post(f"/alerts/{acked['event_id']}/acknowledge")
        rec = _send_recovery(
            client, resolved, source="rec-filter-1", related="anom-filter-2"
        )
        self.assertEqual(rec.status_code, 201, rec.text)

        def ids_for(**query: str) -> set[str]:
            r = client.get("/alerts/", params=query)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["count"], len(r.json()["data"]))
            return {row["alert_event_id"] for row in r.json()["data"]}

        self.assertEqual(ids_for(state="open"), set())
        self.assertEqual(ids_for(state="acknowledged"), {acked["event_id"]})
        self.assertEqual(ids_for(state="resolved"), {resolved["event_id"]})
        self.assertEqual(
            ids_for(severity="warning"), {acked["event_id"], resolved["event_id"]}
        )
        self.assertEqual(ids_for(severity="critical"), set())
        self.assertEqual(ids_for(state="resolved", severity="critical"), set())

    def test_list_paginates_with_total_count(self) -> None:
        client = _client()
        _create_alert(client, source="anom-page-1")
        _create_alert(client, source="anom-page-2")

        r = client.get("/alerts/", params={"skip": 1, "limit": 1})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(len(body["data"]), 1)

    def test_list_rejects_unknown_state(self) -> None:
        client = _client()
        self.assertEqual(
            client.get("/alerts/", params={"state": "closed"}).status_code, 422
        )


if __name__ == "__main__":
    unittest.main()
