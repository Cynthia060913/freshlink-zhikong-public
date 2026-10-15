"""Root wiring/authentication checks against B's actual HTTP routers.

Use in-memory SQLite only; this is not PostgreSQL migration or MQTT evidence.
Run with unittest to avoid the unrelated global PostgreSQL pytest fixture.
"""

import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_current_user, get_db
from app.api.main import api_router
from app.core.config import settings
from app.models import User
from app.modules.iot.control_models import DeviceCommand
from app.modules.iot.models import Device, DeviceConnectivityEvent
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session

# Mount the production root router with its production prefix. Frontend build
# artifacts are deliberately absent from this backend-only source mount.
app = FastAPI()
app.include_router(api_router, prefix=settings.API_V1_STR)


class RootWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(self.engine)
        self.actor = User(
            email="reader@example.com", hashed_password="unused", is_active=True
        )
        self.session = Session(self.engine)
        app.dependency_overrides[get_db] = lambda: self.session
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        app.dependency_overrides.clear()
        self.session.close()
        self.engine.dispose()

    def login(self, *, administrator: bool = False) -> None:
        self.actor.is_superuser = administrator
        self.session.add(self.actor)
        self.session.commit()
        self.session.refresh(self.actor)
        app.dependency_overrides[get_current_user] = lambda: self.actor

    def warehouse(self) -> str:
        response = self.client.post(
            "/api/v1/warehouses/",
            json={"code": uuid.uuid4().hex, "name": "Binding proof"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return str(response.json()["id"])

    def device(self, *, online: bool = False) -> uuid.UUID:
        row = Device(
            code=uuid.uuid4().hex, name="Binding proof", kind="cooling_actuator"
        )
        if online:
            row.connectivity = "online"
            row.last_seen_at = datetime.now(UTC)
            row.last_sent_at = row.last_seen_at
            row.last_heartbeat_message_id = uuid.uuid4()
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row.id

    def bind(self, identity: uuid.UUID, warehouse: str):
        return self.client.post(
            f"/api/v1/devices/{identity}/binding", json={"warehouse_id": warehouse}
        )

    def test_openapi_contains_all_b_groups_with_one_prefix(self) -> None:
        paths = app.openapi()["paths"]
        for path in (
            "/api/v1/warehouses/",
            "/api/v1/warehouses/{warehouse_id}/batches",
            "/api/v1/batches/{batch_id}/rule-snapshots",
            "/api/v1/candidates",
            "/api/v1/device-events",
            "/api/v1/candidates/by-source/{source_event_id}",
            "/api/v1/alert-events/{event_id}/notification-attempts",
            "/api/v1/devices/{device_id}/binding",
            "/api/v1/devices/{device_id}/commands",
        ):
            self.assertIn(path, paths)
        self.assertFalse(any("/api/v1/api/v1/" in path for path in paths))

    def test_anonymous_read_requires_login(self) -> None:
        self.assertEqual(self.client.get("/api/v1/warehouses/").status_code, 401)

    def test_ordinary_user_reads_warehouses(self) -> None:
        self.login()
        response = self.client.get("/api/v1/warehouses/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_ordinary_user_cannot_ingest_candidate(self) -> None:
        self.login()
        self.assertEqual(
            self.client.post("/api/v1/candidates", json={}).status_code, 403
        )

    def test_ordinary_user_cannot_ingest_device_event(self) -> None:
        self.login()
        self.assertEqual(
            self.client.post("/api/v1/device-events", json={}).status_code, 403
        )

    def test_administrator_retains_warehouse_write(self) -> None:
        self.login(administrator=True)
        response = self.client.post(
            "/api/v1/warehouses/", json={"code": "review-warehouse", "name": "Review"}
        )
        self.assertEqual(response.status_code, 201, response.text)

    def test_anonymous_ingestion_requires_login(self) -> None:
        self.assertEqual(
            self.client.post("/api/v1/candidates", json={}).status_code, 401
        )

    def test_ordinary_user_lookup_is_not_found_not_invalid(self) -> None:
        self.login()
        response = self.client.get(f"/api/v1/candidates/by-source/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)
        self.assertIn("not_found", response.json()["detail"])

    def test_binding_anonymous_and_readonly_cannot_write(self) -> None:
        identity = self.device()
        self.assertEqual(self.bind(identity, str(uuid.uuid4())).status_code, 401)
        self.login()
        self.assertEqual(self.bind(identity, str(uuid.uuid4())).status_code, 403)
        self.assertIsNone(self.session.get(Device, identity).warehouse_id)

    def test_binding_invalid_and_extra_fields_are_rejected(self) -> None:
        self.login(administrator=True)
        identity = self.device()
        for body in (
            {},
            {"warehouse_id": None},
            {"warehouse_id": "bad-uuid"},
            {"warehouse_id": str(uuid.uuid4()), "connectivity": "online"},
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    self.client.post(
                        f"/api/v1/devices/{identity}/binding", json=body
                    ).status_code,
                    422,
                )
        self.assertEqual(self.bind("bad-device", str(uuid.uuid4())).status_code, 422)

    def test_binding_unknown_device_or_warehouse_does_not_mutate(self) -> None:
        self.login(administrator=True)
        warehouse = self.warehouse()
        self.assertEqual(
            self.bind(uuid.uuid4(), warehouse).json()["detail"]["reason_code"],
            "device_unknown",
        )
        identity = self.device()
        response = self.bind(identity, str(uuid.uuid4()))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["reason_code"], "warehouse_unknown")
        self.assertIsNone(self.session.get(Device, identity).warehouse_id)

    def test_real_warehouse_binding_is_idempotent_without_rewriting_history(
        self,
    ) -> None:
        self.login(administrator=True)
        warehouse = self.warehouse()
        identity = self.device()
        event = DeviceConnectivityEvent(
            source_event_id=uuid.uuid4(),
            device_id=identity,
            occurred_at=datetime.now(UTC),
            kind="offline",
            reason="heartbeat_timeout",
            delivery_state="held_unbound",
        )
        self.session.add(event)
        self.session.commit()
        source_id = event.source_event_id
        for _ in range(2):
            response = self.bind(identity, warehouse)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["warehouse_id"], warehouse)
            self.assertEqual(response.json()["connectivity"], "unknown")
        self.session.expire_all()
        saved_event = self.session.get(DeviceConnectivityEvent, source_id)
        self.assertIsNone(saved_event.warehouse_id)
        self.assertEqual(saved_event.delivery_state, "held_unbound")
        self.assertEqual(
            len(self.session.exec(select(DeviceConnectivityEvent)).all()), 1
        )
        self.login()
        self.assertEqual(
            self.client.get(f"/api/v1/devices/{identity}").json()["warehouse_id"],
            warehouse,
        )

    def test_binding_to_another_real_warehouse_conflicts(self) -> None:
        self.login(administrator=True)
        warehouse, other = self.warehouse(), self.warehouse()
        identity = self.device()
        self.assertEqual(self.bind(identity, warehouse).status_code, 200)
        response = self.bind(identity, other)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["reason_code"], "binding_conflict")
        self.assertEqual(
            str(self.session.get(Device, identity).warehouse_id), warehouse
        )

    def test_bridge_propagates_read_failure_and_binding_returns_retryable(self) -> None:
        self.login(administrator=True)
        identity = self.device()
        with patch(
            "app.modules.iot.warehouse_bridge.get_warehouse",
            side_effect=OperationalError("read", {}, RuntimeError("fixture")),
        ):
            response = self.bind(identity, str(uuid.uuid4()))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "30")
        self.assertEqual(
            response.json()["detail"], {"reason_code": "storage_unavailable"}
        )
        self.assertIsNone(self.session.get(Device, identity).warehouse_id)
        self.assertFalse(warehouse_lookup_for_session(self.session)(uuid.uuid4()))
        with patch(
            "app.modules.iot.warehouse_bridge.get_warehouse",
            side_effect=ValueError("fixture"),
        ):
            with self.assertRaises(ValueError):
                warehouse_lookup_for_session(self.session)(uuid.uuid4())

    def test_binding_failed_commit_rolls_back(self) -> None:
        self.login(administrator=True)
        warehouse = self.warehouse()
        identity = self.device()
        with patch.object(
            self.session,
            "commit",
            side_effect=OperationalError("commit", {}, RuntimeError("fixture")),
        ):
            response = self.bind(identity, warehouse)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "30")
        self.assertIsNone(self.session.get(Device, identity).warehouse_id)

    def test_binding_committed_refresh_failure_can_retry_same_body(self) -> None:
        self.login(administrator=True)
        warehouse = self.warehouse()
        identity = self.device()
        with patch.object(
            self.session,
            "refresh",
            side_effect=OperationalError("refresh", {}, RuntimeError("fixture")),
        ):
            response = self.bind(identity, warehouse)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "30")
        with Session(self.engine) as independent:
            self.assertEqual(
                str(independent.get(Device, identity).warehouse_id), warehouse
            )
        self.assertEqual(self.bind(identity, warehouse).status_code, 200)

    def test_real_binding_supplies_control_lookup_without_override(self) -> None:
        from app.modules.iot.control_routes import control_warehouse_lookup

        self.login(administrator=True)
        warehouse = self.warehouse()
        identity = self.device()
        self.assertEqual(self.bind(identity, warehouse).status_code, 200)
        self.assertNotIn(control_warehouse_lookup, app.dependency_overrides)
        lookup = control_warehouse_lookup(self.session)
        self.assertTrue(lookup(self.session.get(Device, identity).warehouse_id))
        self.assertFalse(lookup(uuid.uuid4()))
        # SQLite drops timezone info. Actual control queue/time checks run in
        # the PostgreSQL/MQTT proof, not a timezone-coercing SQLite substitute.

    def test_control_missing_real_warehouse_cannot_queue(self) -> None:
        self.login(administrator=True)
        identity = self.device(online=True)
        row = self.session.get(Device, identity)
        row.warehouse_id = uuid.uuid4()  # Explicit old corrupt/missing reference.
        self.session.add(row)
        self.session.commit()
        response = self.client.post(
            f"/api/v1/devices/{identity}/commands",
            json={"command_id": str(uuid.uuid4()), "enabled": True},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["reason_code"], "device_unbound")
        self.assertEqual(self.session.exec(select(DeviceCommand)).all(), [])
