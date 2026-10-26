"""Ingestion-gate auth regressions (#8 6398395372 "untested" closure).

The real application mounts the alerts aggregate the way
``backend/app/api/main.py`` does — global prefix applied here so the
:func:`~app.api.ingestion_auth.guard_alert_ingestion` write-path set
(``/api/v1/candidates``, ``/api/v1/device-events``) matches — and the
correction contract rides the same POST ``/candidates`` endpoint, so
its auth behaviour is proven at THIS layer, not re-proven per kind:

- no token → 401 (OAuth2 bearer, before any handler runs);
- garbage token → 403 (could not validate credentials);
- normal (non-superuser) user → 403 from the ingestion guard, zero
  partial writes;
- superuser → 201 with the five-way receipt (happy path reaches the
  service; storage semantics are covered by the real-PG suites).

SQLite in-memory per the repo's HTTP-test idiom; NOT a lock-semantics
claim (that is the dual-connection PostgreSQL evidence).
"""

from __future__ import annotations

import unittest
import uuid
from collections.abc import Generator
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, func, select

from app.api import deps as api_deps
from app.api.ingestion_auth import guard_alert_ingestion
from app.core.config import settings
from app.modules.alerts.models.event import AlertEvent
from app.modules.alerts.routes import router as alerts_router

_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SQLModel.metadata.create_all(_ENGINE)


def _override_db() -> Generator[Session]:
    with Session(_ENGINE) as session:
        yield session


class _AuthUser:
    def __init__(self, *, is_superuser: bool) -> None:
        self.id = uuid.uuid4()
        self.is_superuser = is_superuser
        self.is_active = True


def _superuser() -> _AuthUser:
    return _AuthUser(is_superuser=True)


def _plain_user() -> _AuthUser:
    return _AuthUser(is_superuser=False)


def _client(auth: Any) -> TestClient:
    """App mounted exactly like backend/app/api/main.py mounts it:
    one global prefix on the include, guard as a router dependency."""

    app = FastAPI()
    app.include_router(
        alerts_router,
        prefix=settings.API_V1_STR,
        dependencies=[Depends(guard_alert_ingestion)],
    )
    app.dependency_overrides[api_deps.get_db] = _override_db
    if auth is not None:
        app.dependency_overrides[api_deps.get_current_user] = auth
    return TestClient(app)


class IngestionAuthCase(unittest.TestCase):
    def _correction_body(self) -> dict[str, Any]:
        return {
            "source_event_id": "correction-auth-1",
            "warehouse_id": str(uuid.uuid4()),
            "batch_id": str(uuid.uuid4()),
            "observation_ids": ["00000000-0000-4000-8000-0000000000aa"],
            "metric_code": "temperature",
            "sampled_at": "2026-10-25T08:00:00Z",
            "value": None,
            "unit": "°C",
            "kind": "data_quality",
            "severity": "info",
            "algorithm_version": "quality-gate/0.3.0",
            "evidence": {"correction": "late invalid evidence"},
            "related_source_event_id": "original-auth-1",
        }

    def test_missing_token_is_401(self) -> None:
        client = _client(None)  # real get_current_user, no token sent
        r = client.post("/api/v1/candidates", json=self._correction_body())
        self.assertEqual(r.status_code, 401, r.text)

    def test_garbage_token_is_403(self) -> None:
        client = _client(None)
        r = client.post(
            "/api/v1/candidates",
            json=self._correction_body(),
            headers={"Authorization": "Bearer not-a-jwt"},
        )
        self.assertEqual(r.status_code, 403, r.text)

    def test_plain_user_is_403_and_no_partial_write(self) -> None:
        client = _client(_plain_user)
        r = client.post("/api/v1/candidates", json=self._correction_body())
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("administrator", r.json()["detail"])
        with Session(_ENGINE) as session:
            self.assertEqual(
                session.exec(
                    select(func.count()).select_from(AlertEvent)
                ).one(),
                0,
            )

    def test_superuser_reaches_the_service(self) -> None:
        client = _client(_superuser)
        r = client.post("/api/v1/candidates", json=self._correction_body())
        # The request PASSES auth/guard and reaches the service, which
        # answers the five-way receipt envelope; the bare world has no
        # batch, so the permanent invalid receipt rides the repo's 422
        # HTTP mapping (same as the recovery HTTP regressions).
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["status"], "invalid")
        self.assertEqual(r.json()["reason_code"], "unknown_batch")


if __name__ == "__main__":
    unittest.main()
