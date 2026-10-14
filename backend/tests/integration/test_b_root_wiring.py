"""Root wiring/authentication checks against B's actual HTTP routers.

Use in-memory SQLite only; this is not PostgreSQL migration or MQTT evidence.
Run with unittest to avoid the unrelated global PostgreSQL pytest fixture.
"""

import unittest
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.deps import get_current_user, get_db
from app.api.main import api_router
from app.core.config import settings
from app.models import User

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
        app.dependency_overrides[get_current_user] = lambda: self.actor

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
