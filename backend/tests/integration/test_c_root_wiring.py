"""A-owned shared-router checks; only synthetic disposable SQLite tables.

Reuse C's unchanged JWT, snapshot, CSV and failure tests against the actual
shared root. This does not install formal B/C migrations or prove full E1.
"""

import warnings

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from app.api.main import api_router
from app.core.config import settings
from app.modules.telemetry import http_candidate, reception_audit_candidate
from tests.modules import test_telemetry_http_candidate as c_http_checks


class SharedTelemetryRootTests(c_http_checks.TelemetryHttpCandidateTests):
    def setUp(self) -> None:
        super().setUp()
        dependency_overrides = dict(self.client.app.dependency_overrides)
        self.client.close()
        shared_app = FastAPI()
        shared_app.include_router(api_router, prefix=settings.API_V1_STR)
        shared_app.dependency_overrides.update(dependency_overrides)
        self.client = TestClient(shared_app)

    def test_shared_paths_are_unique_and_have_one_prefix(self) -> None:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "error", message="Duplicate Operation ID.*", category=UserWarning
            )
            paths = self.client.app.openapi()["paths"]
        for suffix in ("/", "/export.csv", "/latest", "/{observation_id}"):
            self.assertIn(c_http_checks.BASE + suffix, paths)
        self.assertIn("/api/v1/telemetry/receptions/rejected", paths)
        self.assertFalse(any("/api/v1/api/v1/" in path for path in paths))
        operations = [
            item["get"]["operationId"]
            for path, item in paths.items()
            if path.startswith(c_http_checks.BASE)
        ]
        self.assertEqual(len(operations), 4)
        self.assertEqual(len(set(operations)), 4)

    def test_shared_read_router_does_not_accept_public_writes(self) -> None:
        response = self.client.post(
            c_http_checks.BASE + "/",
            json={},
            headers=self.headers(self.user),
        )
        self.assertEqual(response.status_code, 405)

    def test_missing_formal_tables_fail_closed_without_auto_creation(self) -> None:
        empty = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.client.app.dependency_overrides[http_candidate._engine] = lambda: empty
        try:
            response = self.client.get(
                c_http_checks.BASE + "/", headers=self.headers(self.user)
            )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers.get("retry-after"), "30")
            self.assertEqual(
                response.json()["detail"], {"reason_code": "storage_unavailable"}
            )
            self.assertEqual(inspect(empty).get_table_names(), [])
        finally:
            self.client.app.dependency_overrides.pop(http_candidate._engine)
            empty.dispose()

    def test_missing_audit_tables_fail_closed_without_auto_creation(self) -> None:
        empty = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.client.app.dependency_overrides[reception_audit_candidate._engine] = (
            lambda: empty
        )
        try:
            response = self.client.get(
                "/api/v1/telemetry/receptions/rejected",
                params={
                    "device_id": str(c_http_checks.DEVICE),
                    "warehouse_id": str(c_http_checks.WAREHOUSE),
                },
                headers=self.headers(self.superuser),
            )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers.get("retry-after"), "30")
            self.assertEqual(
                response.json()["detail"], {"reason_code": "storage_unavailable"}
            )
            self.assertEqual(inspect(empty).get_table_names(), [])
        finally:
            self.client.app.dependency_overrides.pop(reception_audit_candidate._engine)
            empty.dispose()
