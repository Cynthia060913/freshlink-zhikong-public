"""HTTP/auth regression on disposable C tables, not production migration."""

import csv
import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlmodel import Session, SQLModel

from app.api.deps import get_db
from app.core.security import create_access_token
from app.models import User
from app.modules.telemetry.http_candidate import router
from app.modules.telemetry.reception_audit_candidate import (
    router as reception_audit_router,
)
from app.modules.telemetry.snapshot_candidate import snapshots
from app.modules.telemetry.storage_candidate import ingest_observation, metadata

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
BASE = "/api/v1/telemetry/observations"


@dataclass(frozen=True)
class Reception:
    raw_text: str
    reception_id: UUID
    received_at: datetime
    authenticated_device_id: UUID = DEVICE
    warehouse_id: UUID = WAREHOUSE

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


class TelemetryHttpCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "candidate.db"
        self.engine = create_engine(f"sqlite:///{path}")
        SQLModel.metadata.create_all(self.engine, tables=[User.__table__])
        metadata.create_all(self.engine)
        self.user = User(email="c-reader@example.test", hashed_password="test")
        self.other = User(email="c-other@example.test", hashed_password="test")
        self.disabled = User(
            email="c-disabled@example.test", hashed_password="test", is_active=False
        )
        self.superuser = User(
            email="c-auditor@example.test", hashed_password="test", is_superuser=True
        )
        with Session(self.engine) as session:
            session.add_all([self.user, self.other, self.disabled, self.superuser])
            session.commit()
            for user in (self.user, self.other, self.disabled, self.superuser):
                session.refresh(user)
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.include_router(reception_audit_router, prefix="/api/v1")

        def test_db():
            # Production PostgreSQL accepts the template JWT's UUID text in
            # Session.get(User, sub); SQLite's UUID binder requires UUID.
            class SQLiteAuthSession(Session):
                def get(self, entity, ident, **kwargs):
                    if entity is User and isinstance(ident, str):
                        ident = UUID(ident)
                    return super().get(entity, ident, **kwargs)

            with SQLiteAuthSession(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = test_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        self.directory.cleanup()

    @staticmethod
    def headers(user: User) -> dict[str, str]:
        token = create_access_token(str(user.id), timedelta(minutes=10))
        return {"Authorization": f"Bearer {token}"}

    def ingest(
        self,
        *,
        temperature: int = 5,
        unit: str = "°C",
        sampled_at: str = "2026-10-18T00:00:00Z",
        received_at: datetime | None = None,
    ) -> tuple[UUID, str]:
        payload = {
            "schema_version": "1.0",
            "device_id": str(DEVICE),
            "message_id": str(uuid4()),
            "sampled_at": sampled_at,
            "metrics": [{"key": "temperature", "value": temperature, "unit": unit}],
        }
        raw = json.dumps(payload, ensure_ascii=False)
        result = ingest_observation(
            self.engine,
            Reception(raw, uuid4(), received_at or datetime.now(UTC)),
        )
        self.assertEqual(result.status, "accepted_new")
        assert result.observation_id is not None
        return result.observation_id, raw

    def test_real_jwt_gate_detail_snapshot_and_csv_same_members(self) -> None:
        hot_id, raw = self.ingest(temperature=11)
        bad_id, _ = self.ingest(unit="fahrenheit")
        self.assertEqual(self.client.get(BASE + "/").status_code, 401)
        self.assertEqual(
            self.client.get(
                BASE + "/", headers=self.headers(self.disabled)
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                BASE + "/", headers={"Authorization": "Bearer bad"}
            ).status_code,
            403,
        )
        params = {"warehouse_id": str(WAREHOUSE)}
        page = self.client.get(
            BASE + "/", params=params, headers=self.headers(self.user)
        )
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        data = page.json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(
            {item["observation_id"] for item in data["items"]},
            {str(hot_id), str(bad_id)},
        )
        bad = next(
            item for item in data["items"] if item["observation_id"] == str(bad_id)
        )
        self.assertNotEqual(bad["risk_state"], "normal")
        detail = self.client.get(BASE + f"/{hot_id}", headers=self.headers(self.user))
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.headers["Cache-Control"], "no-store")
        self.assertEqual(detail.json()["raw_text"], raw)
        self.assertEqual(len(detail.json()["receptions"]), 1)
        snapshot = data["snapshot_id"]
        csv_response = self.client.get(
            BASE + "/export.csv",
            params={**params, "snapshot_id": snapshot},
            headers=self.headers(self.user),
        )
        self.assertEqual(csv_response.status_code, 200, csv_response.text)
        self.assertEqual(csv_response.headers["Cache-Control"], "no-store")
        rows = list(csv.DictReader(StringIO(csv_response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [row["observation_id"] for row in rows],
            [item["observation_id"] for item in data["items"]],
        )
        self.assertEqual(len(rows[0]), 14)
        v2_params = {**params, "snapshot_id": snapshot, "export_schema": 2}
        v2_response = self.client.get(
            BASE + "/export.csv",
            params=v2_params,
            headers=self.headers(self.user),
        )
        self.assertEqual(v2_response.status_code, 200, v2_response.text)
        v2_rows = list(
            csv.DictReader(StringIO(v2_response.content.decode("utf-8-sig")))
        )
        self.assertEqual(len(v2_rows), 2)
        self.assertEqual(len(v2_rows[0]), 15)
        self.assertEqual(
            [row["observation_id"] for row in v2_rows],
            [item["observation_id"] for item in data["items"]],
        )
        self.assertEqual(
            [json.loads(row["batch_rule_refs_json"]) for row in v2_rows],
            [item["batch_rule_refs"] for item in data["items"]],
        )
        self.assertEqual(
            self.client.get(BASE + "/export.csv", params=v2_params).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                BASE + "/export.csv",
                params=v2_params,
                headers=self.headers(self.other),
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(
                BASE + "/export.csv",
                params={**v2_params, "export_schema": 3},
                headers=self.headers(self.user),
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(
                BASE + "/export.csv",
                params={**params, "snapshot_id": snapshot},
                headers=self.headers(self.other),
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(
                BASE + "/export.csv",
                params={"snapshot_id": snapshot},
                headers=self.headers(self.user),
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(
                BASE + f"/{uuid4()}", headers=self.headers(self.user)
            ).status_code,
            404,
        )

    def test_fixed_snapshot_late_row_cursor_and_capacity(self) -> None:
        first_id, _ = self.ingest()
        headers = self.headers(self.user)
        params = {"warehouse_id": str(WAREHOUSE), "limit": 1}
        first = self.client.get(BASE + "/", params=params, headers=headers).json()
        self.assertEqual(first["total"], 1)
        late_id, _ = self.ingest(temperature=12)
        old = self.client.get(
            BASE + "/",
            params={**params, "snapshot_id": first["snapshot_id"]},
            headers=headers,
        ).json()
        self.assertEqual(old["total"], 1)
        self.assertEqual(old["items"][0]["observation_id"], str(first_id))
        fresh = self.client.get(BASE + "/", params=params, headers=headers).json()
        self.assertEqual(fresh["total"], 2)
        next_page = self.client.get(
            BASE + "/",
            params={**params, "cursor": fresh["next_cursor"]},
            headers=headers,
        )
        self.assertEqual(next_page.status_code, 200, next_page.text)
        self.assertEqual(
            {
                fresh["items"][0]["observation_id"],
                next_page.json()["items"][0]["observation_id"],
            },
            {str(first_id), str(late_id)},
        )
        self.assertEqual(
            self.client.get(
                BASE + "/",
                params={**params, "cursor": fresh["next_cursor"]},
                headers=self.headers(self.other),
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(BASE + "/", params=params, headers=headers).status_code, 200
        )
        self.assertEqual(
            self.client.get(BASE + "/", params=params, headers=headers).status_code, 200
        )
        capacity = self.client.get(BASE + "/", params=params, headers=headers)
        self.assertEqual(capacity.status_code, 429, capacity.text)
        self.assertEqual(capacity.headers["Retry-After"], "30")

    def test_detail_exposes_linked_duplicate_and_conflict_without_replacing_raw(
        self,
    ) -> None:
        first_at = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
        observation_id, original_raw = self.ingest(received_at=first_at)
        duplicate = ingest_observation(
            self.engine,
            Reception(original_raw, uuid4(), first_at + timedelta(seconds=1)),
        )
        changed = json.loads(original_raw)
        changed["metrics"][0]["value"] = 15
        conflict = ingest_observation(
            self.engine,
            Reception(
                json.dumps(changed, ensure_ascii=False),
                uuid4(),
                first_at + timedelta(seconds=2),
            ),
        )
        self.assertEqual(duplicate.status, "accepted_duplicate")
        self.assertEqual(conflict.status, "conflict")
        self.assertEqual(duplicate.observation_id, observation_id)
        self.assertEqual(conflict.observation_id, observation_id)

        response = self.client.get(
            BASE + f"/{observation_id}", headers=self.headers(self.user)
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["raw_text"], original_raw)
        self.assertEqual(
            [(row["outcome"], row["reason"]) for row in body["receptions"]],
            [
                ("accepted_new", ""),
                ("accepted_duplicate", "duplicate_message"),
                ("conflict", "conflicting_message"),
            ],
        )
        self.assertEqual(body["receptions"][2]["received_at"], "2026-10-18T00:00:02Z")
        self.assertEqual(body["reception_reuse_conflict_count"], 0)

        claimant_device = uuid4()
        reused = ingest_observation(
            self.engine,
            Reception(
                json.dumps({**changed, "device_id": str(claimant_device)}),
                UUID(body["receptions"][0]["reception_id"]),
                first_at + timedelta(seconds=3),
                authenticated_device_id=claimant_device,
                warehouse_id=uuid4(),
            ),
        )
        self.assertEqual(reused.status, "conflict")
        refreshed = self.client.get(
            BASE + f"/{observation_id}", headers=self.headers(self.user)
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(refreshed.json()["raw_text"], original_raw)
        self.assertEqual(refreshed.json()["reception_reuse_conflict_count"], 1)
        self.assertEqual(len(refreshed.json()["receptions"]), 3)

    def test_detail_orders_mixed_precision_receipt_times_chronologically(self) -> None:
        first_at = datetime(2026, 10, 18, tzinfo=UTC)
        observation_id, raw = self.ingest(received_at=first_at)
        later = ingest_observation(
            self.engine,
            Reception(raw, uuid4(), first_at + timedelta(milliseconds=500)),
        )
        changed = json.loads(raw)
        changed["metrics"][0]["value"] = 15
        earlier = ingest_observation(
            self.engine,
            Reception(
                json.dumps(changed, ensure_ascii=False),
                uuid4(),
                first_at + timedelta(milliseconds=100),
            ),
        )
        self.assertEqual(later.status, "accepted_duplicate")
        self.assertEqual(earlier.status, "conflict")

        detail = self.client.get(
            BASE + f"/{observation_id}", headers=self.headers(self.user)
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(
            [item["received_at"] for item in detail.json()["receptions"]],
            [
                "2026-10-18T00:00:00Z",
                "2026-10-18T00:00:00.100000Z",
                "2026-10-18T00:00:00.500000Z",
            ],
        )
        self.assertEqual(
            [item["outcome"] for item in detail.json()["receptions"]],
            ["accepted_new", "conflict", "accepted_duplicate"],
        )

    def test_rejected_audit_is_privileged_bounded_and_keeps_orphan_raw(self) -> None:
        first_at = datetime(2026, 10, 18, tzinfo=UTC)
        original_id = uuid4()
        original_payload = {
            "schema_version": "1.0",
            "device_id": str(DEVICE),
            "message_id": str(uuid4()),
            "sampled_at": "2026-10-18T00:00:00Z",
            "metrics": [{"key": "temperature", "value": 5, "unit": "°C"}],
        }
        original_raw = json.dumps(original_payload, ensure_ascii=False)
        original = ingest_observation(
            self.engine, Reception(original_raw, original_id, first_at)
        )
        self.assertEqual(original.status, "accepted_new")

        invalid_payload = {**original_payload, "schema_version": "not-supported"}
        invalid_raw = json.dumps(invalid_payload, ensure_ascii=False)
        invalid = ingest_observation(
            self.engine,
            Reception(invalid_raw, uuid4(), first_at + timedelta(seconds=1)),
        )
        self.assertEqual(invalid.status, "invalid")
        self.assertIsNone(invalid.observation_id)

        conflict_payload = {
            **original_payload,
            "metrics": [{"key": "temperature", "value": 15, "unit": "°C"}],
        }
        conflict_raw = json.dumps(conflict_payload, ensure_ascii=False)
        conflict = ingest_observation(
            self.engine,
            Reception(conflict_raw, uuid4(), first_at + timedelta(seconds=2)),
        )
        self.assertEqual(conflict.status, "conflict")

        other_device = uuid4()
        other_warehouse = uuid4()
        reused_raw = json.dumps(
            {**original_payload, "device_id": str(other_device)}, ensure_ascii=False
        )
        reused = ingest_observation(
            self.engine,
            Reception(
                reused_raw,
                original_id,
                first_at + timedelta(seconds=3),
                authenticated_device_id=other_device,
                warehouse_id=other_warehouse,
            ),
        )
        self.assertEqual(reused.status, "conflict")
        self.assertEqual(reused.reason, "reception_id_reuse")

        path = "/api/v1/telemetry/receptions/rejected"
        scope = {"device_id": str(DEVICE), "warehouse_id": str(WAREHOUSE)}
        self.assertEqual(self.client.get(path, params=scope).status_code, 401)
        self.assertEqual(
            self.client.get(
                path, params=scope, headers=self.headers(self.user)
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                path, params=scope, headers=self.headers(self.disabled)
            ).status_code,
            400,
        )
        headers = self.headers(self.superuser)
        self.assertEqual(
            self.client.get(
                path, params={**scope, "limit": 51}, headers=headers
            ).status_code,
            422,
        )
        first = self.client.get(path, params={**scope, "limit": 1}, headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.headers["Cache-Control"], "no-store")
        self.assertIsNotNone(first.json()["next_after"])
        items = first.json()["items"]
        cursor = first.json()["next_after"]
        while cursor is not None:
            page = self.client.get(
                path,
                params={**scope, "limit": 1, "after": cursor},
                headers=headers,
            )
            self.assertEqual(page.status_code, 200, page.text)
            items.extend(page.json()["items"])
            cursor = page.json()["next_after"]
        self.assertEqual(len(items), 3)
        self.assertEqual(
            {(row["outcome"], row["reason"], row["raw_text"]) for row in items},
            {
                ("invalid", "invalid_schema_version", invalid_raw),
                ("conflict", "conflicting_message", conflict_raw),
                ("conflict", "reception_id_reuse", reused_raw),
            },
        )
        self.assertTrue(all(row["raw_text"] != original_raw for row in items))
        claimant_scope = {
            "device_id": str(other_device),
            "warehouse_id": str(other_warehouse),
        }
        claimant = self.client.get(path, params=claimant_scope, headers=headers)
        self.assertEqual(claimant.status_code, 200, claimant.text)
        self.assertEqual(len(claimant.json()["items"]), 1)
        self.assertEqual(claimant.json()["items"][0]["raw_text"], reused_raw)
        self.assertEqual(claimant.json()["items"][0]["original_device_id"], str(DEVICE))

    def test_latest_poll_uses_trusted_first_reception_without_new_snapshot(
        self,
    ) -> None:
        start = datetime(2026, 10, 18, tzinfo=UTC)
        old_id, old_raw = self.ingest(
            temperature=11,
            sampled_at="2026-10-18T01:00:00Z",
            received_at=start,
        )
        latest_id, _ = self.ingest(
            unit="°F",
            sampled_at="2026-10-17T23:00:00Z",
            received_at=start + timedelta(seconds=1),
        )
        path = BASE + "/latest"
        scope = {"device_id": str(DEVICE), "warehouse_id": str(WAREHOUSE)}
        self.assertEqual(self.client.get(path, params=scope).status_code, 401)
        headers = self.headers(self.user)
        self.assertEqual(self.client.get(path, headers=headers).status_code, 422)
        self.assertEqual(
            self.client.get(
                path, params={"device_id": str(uuid4())}, headers=headers
            ).json()["item"],
            None,
        )
        first = self.client.get(path, params=scope, headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.headers["Cache-Control"], "no-store")
        self.assertEqual(first.json()["item"]["observation_id"], str(latest_id))
        self.assertIsNone(first.json()["item"]["temperature_c"])
        self.assertEqual(first.json()["item"]["risk_state"], "unknown")
        self.assertEqual(first.json()["item"]["received_at"], "2026-10-18T00:00:01Z")
        self.assertTrue(first.json()["checked_at"].endswith("Z"))
        replay = ingest_observation(
            self.engine, Reception(old_raw, uuid4(), start + timedelta(seconds=2))
        )
        self.assertEqual(replay.status, "accepted_duplicate")
        self.assertEqual(replay.observation_id, old_id)
        for _ in range(3):
            polled = self.client.get(path, params=scope, headers=headers)
            self.assertEqual(polled.status_code, 200, polled.text)
            self.assertEqual(polled.json()["item"]["observation_id"], str(latest_id))
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(snapshots)), 0
            )
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE telemetry_observation_candidate")
        unavailable = self.client.get(path, params=scope, headers=headers)
        self.assertEqual(unavailable.status_code, 503, unavailable.text)
        self.assertEqual(unavailable.headers["Retry-After"], "30")

    def test_invalid_time_range_and_storage_error_are_explicit(self) -> None:
        headers = self.headers(self.user)
        invalid = self.client.get(
            BASE + "/",
            params={
                "sampled_from": "2026-10-18T01:00:00Z",
                "sampled_to": "2026-10-18T00:00:00Z",
            },
            headers=headers,
        )
        self.assertEqual(invalid.status_code, 422)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE telemetry_snapshot_candidate")
        unavailable = self.client.get(BASE + "/", headers=headers)
        self.assertEqual(unavailable.status_code, 503, unavailable.text)
        self.assertEqual(unavailable.headers["Retry-After"], "30")

    def test_time_and_quality_filters_match_csv_and_preserve_bad_unit(self) -> None:
        start = datetime(2026, 10, 18, 1, tzinfo=UTC)
        sampled_out_id, _ = self.ingest(
            unit="°F",
            sampled_at="2026-10-18T00:00:00Z",
            received_at=start,
        )
        bad_id, raw = self.ingest(
            temperature=12,
            unit="=SUM(1,1)",
            sampled_at="2026-10-18T01:00:00Z",
            received_at=start + timedelta(minutes=1),
        )
        sampled_end_id, _ = self.ingest(
            temperature=13,
            unit="°F",
            sampled_at="2026-10-18T02:00:00Z",
            received_at=start + timedelta(minutes=2),
        )
        received_end_id, _ = self.ingest(
            temperature=14,
            unit="°F",
            sampled_at="2026-10-18T01:30:00Z",
            received_at=start + timedelta(hours=1),
        )
        headers = self.headers(self.user)
        filters = {
            "sampled_from": "2026-10-18T01:00:00Z",
            "sampled_to": "2026-10-18T02:00:00Z",
            "received_from": "2026-10-18T01:00:00Z",
            "received_to": "2026-10-18T02:00:00Z",
            "quality_in": "invalid_unit:temperature",
        }
        page = self.client.get(BASE + "/", params=filters, headers=headers)
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.json()["total"], 1)
        item = page.json()["items"][0]
        self.assertEqual(item["observation_id"], str(bad_id))
        self.assertNotIn(
            str(sampled_out_id),
            [row["observation_id"] for row in page.json()["items"]],
        )
        self.assertNotIn(
            str(sampled_end_id),
            [row["observation_id"] for row in page.json()["items"]],
        )
        self.assertNotIn(
            str(received_end_id),
            [row["observation_id"] for row in page.json()["items"]],
        )
        self.assertIsNone(item["temperature_c"])
        self.assertEqual(item["risk_state"], "unknown")

        csv_response = self.client.get(
            BASE + "/export.csv",
            params={**filters, "snapshot_id": page.json()["snapshot_id"]},
            headers=headers,
        )
        self.assertEqual(csv_response.status_code, 200, csv_response.text)
        rows = list(csv.DictReader(StringIO(csv_response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["observation_id"], item["observation_id"])
        self.assertEqual(rows[0]["sampled_at"], item["sampled_at"])
        self.assertEqual(rows[0]["raw_temperature_unit"], "'=SUM(1,1)")
        self.assertEqual(rows[0]["temperature_c"], "")
        detail = self.client.get(BASE + f"/{bad_id}", headers=headers)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["raw_text"], raw)

        empty_filters = {**filters, "quality_in": "missing_metric:temperature"}
        empty_page = self.client.get(BASE + "/", params=empty_filters, headers=headers)
        self.assertEqual(empty_page.status_code, 200, empty_page.text)
        self.assertEqual(empty_page.json()["total"], 0)
        empty_csv = self.client.get(
            BASE + "/export.csv",
            params={**empty_filters, "snapshot_id": empty_page.json()["snapshot_id"]},
            headers=headers,
        )
        self.assertEqual(empty_csv.status_code, 200, empty_csv.text)
        self.assertEqual(
            len(list(csv.DictReader(StringIO(empty_csv.content.decode("utf-8-sig"))))),
            0,
        )
