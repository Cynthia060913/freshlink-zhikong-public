"""Same assertions on an in-memory database or explicitly isolated test PG.

Opt-in PG: FRESHLINK_GROUP_TEST_POSTGRES=1, test-db/freshlink_test only.
Each test owns an uncommitted transaction; the HTTP reader cannot commit it.
"""

from __future__ import annotations

import os
import unittest
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import deps
from app.core.config import settings
from app.modules.alerts.models import (
    AlertEvent,
    AlertStatus,
    AlertStatusTransition,
    Batch,
    Warehouse,
)
from app.modules.alerts.routes import router

if os.getenv("FRESHLINK_GROUP_TEST_POSTGRES") == "1":
    url = make_url(str(settings.DATABASE_URL))
    if (
        url.host != "test-db"
        or url.database != "freshlink_test"
        or url.username != "freshlink_test"
    ):
        raise RuntimeError("grouping tests require owned Docker test-db/freshlink_test")
    ENGINE = create_engine(url)
else:
    ENGINE = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(ENGINE)


class GroupingCase(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = ENGINE.connect()
        self.transaction = self.connection.begin()
        self.session = Session(self.connection)
        self.now = datetime.now(UTC)
        self.w1 = Warehouse(code=f"g-{uuid.uuid4().hex}", name="一号冷库")
        self.w2 = Warehouse(code=f"g-{uuid.uuid4().hex}", name="二号冷库")
        self.session.add_all([self.w1, self.w2])
        self.session.flush()
        self.b1 = Batch(warehouse_id=self.w1.id, code="apple-1", product="苹果")
        self.b2 = Batch(warehouse_id=self.w1.id, code="apple-2", product="苹果")
        self.b3 = Batch(warehouse_id=self.w2.id, code="apple-1", product="苹果")
        self.session.add_all([self.b1, self.b2, self.b3])
        self.session.flush()
        self.app = FastAPI()
        self.app.include_router(router)

        def database() -> Generator[Session]:
            yield self.session

        self.app.dependency_overrides[deps.get_db] = database
        self.app.dependency_overrides[deps.get_current_user] = lambda: object()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.session.close()
        if self.transaction.is_active:
            self.transaction.rollback()
        self.connection.close()

    def record(
        self,
        batch: Batch | None,
        *,
        state: AlertStatus = AlertStatus.OPEN,
        kind: str = "temperature_high",
        unassigned: bool = False,
    ) -> AlertEvent:
        row = AlertEvent(
            source_event_id=f"group-{uuid.uuid4().hex}",
            kind=kind,
            warehouse_id=None
            if unassigned
            else (batch.warehouse_id if batch else self.w1.id),
            batch_id=batch.id if batch else None,
            metric_code="temperature",
            sampled_at=self.now,
            value=9.2,
            unit="°C",
            severity="warning",
            content_digest="sha256:" + "a" * 64,
            payload_json="{}",
            created_at=self.now,
        )
        self.session.add(row)
        self.session.flush()
        if state != AlertStatus.OPEN:
            self.session.add(
                AlertStatusTransition(
                    alert_event_id=row.id,
                    from_status=AlertStatus.OPEN,
                    to_status=AlertStatus.ACKNOWLEDGED,
                    created_at=self.now,
                )
            )
            if state == AlertStatus.RESOLVED:
                self.session.add(
                    AlertStatusTransition(
                        alert_event_id=row.id,
                        from_status=AlertStatus.ACKNOWLEDGED,
                        to_status=state,
                        created_at=self.now + timedelta(seconds=1),
                    )
                )
            self.session.flush()
        return row

    def groups(self, **params: object) -> dict:
        result = self.client.get("/alerts/groups", params=params)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_full_aggregation_not_limited_to_recent_page_and_state_counts(self) -> None:
        for _ in range(107):
            self.record(self.b1)
        self.record(self.b1, state=AlertStatus.ACKNOWLEDGED)
        self.record(self.b1, state=AlertStatus.RESOLVED)
        self.record(self.b2, kind="data_quality")
        self.record(self.b3)
        self.record(self.b1, kind="temperature_recovered")
        self.record(None, kind="device_offline")
        groups = self.groups()
        self.assertEqual(groups["count"], 3)
        group = groups["data"][0]
        self.assertEqual(
            (
                group["open_count"],
                group["acknowledged_count"],
                group["resolved_count"],
                group["total_count"],
            ),
            (107, 1, 1, 109),
        )
        self.assertEqual(group["warehouse_name"], "一号冷库")
        self.assertEqual(group["batch_code"], "apple-1")
        for item in groups["data"]:
            details = self.client.get(
                "/alerts/",
                params={
                    "warehouse_id": item["warehouse_id"],
                    "batch_id": item["batch_id"],
                    "limit": 100,
                },
            ).json()
            self.assertEqual(details["count"], item["total_count"])
            for state in ("open", "acknowledged", "resolved"):
                filtered = self.client.get(
                    "/alerts/",
                    params={
                        "warehouse_id": item["warehouse_id"],
                        "batch_id": item["batch_id"],
                        "state": state,
                    },
                ).json()
                self.assertEqual(filtered["count"], item[f"{state}_count"])

    def test_same_batch_code_other_warehouse_never_leaks(self) -> None:
        one = self.record(self.b1)
        self.record(self.b3)
        response = self.client.get(
            "/alerts/",
            params={"warehouse_id": str(self.w1.id), "batch_id": str(self.b1.id)},
        )
        self.assertEqual(
            [x["alert_event_id"] for x in response.json()["data"]], [str(one.id)]
        )
        self.assertEqual(
            self.client.get(
                "/alerts/",
                params={"warehouse_id": str(self.w2.id), "batch_id": str(self.b1.id)},
            ).json()["count"],
            0,
        )

    def test_null_scope_is_explicit_not_all_records(self) -> None:
        one = self.record(None)
        two = self.record(None, unassigned=True)
        self.record(self.b1)
        self.assertEqual(self.groups()["count"], 3)
        for wid, expected in ((str(self.w1.id), one.id), (None, two.id)):
            scope = {"warehouse_id": wid} if wid else {"unassigned_warehouse": "true"}
            response = self.client.get(
                "/alerts/", params={**scope, "unassigned_batch": "true"}
            ).json()
            self.assertEqual(response["count"], 1)
            self.assertEqual(response["data"][0]["alert_event_id"], str(expected))

    def test_pagination_total_empty_and_out_of_range(self) -> None:
        self.assertEqual(self.groups()["data"], [])
        for batch in (self.b1, self.b2, self.b3):
            self.record(batch)
        ids = []
        for skip in range(3):
            page = self.groups(skip=skip, limit=1)
            self.assertEqual(page["count"], 3)
            ids.append(page["data"][0]["batch_id"])
        self.assertEqual(len(set(ids)), 3)
        ordered = sorted(
            (self.b1, self.b2, self.b3),
            key=lambda batch: (str(batch.warehouse_id), str(batch.id)),
        )
        expected = [str(batch.id) for batch in ordered]
        self.assertEqual(ids, expected)
        self.assertEqual([row["batch_id"] for row in self.groups()["data"]], expected)
        self.assertEqual(self.groups(skip=100)["count"], 3)
        self.assertEqual(self.groups(skip=100)["data"], [])

    def test_query_validation_rejects_ambiguous_scope_and_unbounded_page(self) -> None:
        for params in (
            {"warehouse_id": str(self.w1.id), "unassigned_warehouse": "true"},
            {"batch_id": str(self.b1.id), "unassigned_batch": "true"},
            {"warehouse_id": "bad-id"},
        ):
            self.assertEqual(
                self.client.get("/alerts/", params=params).status_code, 422
            )
        for params in ({"limit": 101}, {"limit": 0}, {"skip": -1}):
            self.assertEqual(
                self.client.get("/alerts/groups", params=params).status_code, 422
            )

    def test_grouping_and_drilldown_execute_no_writes(self) -> None:
        self.record(self.b1, state=AlertStatus.ACKNOWLEDGED)
        statements: list[str] = []

        def before(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _many: bool,
        ) -> None:
            statements.append(statement.strip().split()[0].upper())

        event.listen(self.connection, "before_cursor_execute", before)
        try:
            self.groups()
            self.client.get("/alerts/", params={"batch_id": str(self.b1.id)})
        finally:
            event.remove(self.connection, "before_cursor_execute", before)
        self.assertTrue(statements)
        self.assertTrue(
            all(word in {"SELECT", "WITH"} for word in statements), statements
        )
        self.assertTrue(self.transaction.is_active)

    def test_group_reads_require_auth_and_have_no_write_method(self) -> None:
        self.app.dependency_overrides.pop(deps.get_current_user)
        self.assertEqual(self.client.get("/alerts/groups").status_code, 401)
        self.assertEqual(self.client.post("/alerts/groups").status_code, 405)

    def test_storage_failure_returns_retryable_not_zero_groups(self) -> None:
        with patch(
            "app.modules.alerts.routes.lifecycle.list_alert_groups",
            side_effect=OperationalError("owned probe", {}, Exception("offline")),
        ):
            response = self.client.get("/alerts/groups")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "30")
        self.assertNotIn("data", response.json())


def tearDownModule() -> None:
    ENGINE.dispose()


if __name__ == "__main__":
    unittest.main()
