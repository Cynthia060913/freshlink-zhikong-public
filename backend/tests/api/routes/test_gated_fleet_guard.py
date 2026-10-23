"""Proof opt-in must reject original targets before business imports/DDL."""

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "gated_fleet_guard",
    Path(__file__).resolve().parents[3] / "scripts/check-gated-fleet.py",
)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GatedFleetGuardTests(unittest.TestCase):
    def test_exact_owned_environment(self):
        with (
            patch.dict(os.environ, self.environment(), clear=True),
            patch.object(Path, "is_file", return_value=True),
        ):
            guard.preflight()

    @staticmethod
    def environment():
        return {
            "FRESHLINK_GATED_FLEET_PROOF": "1",
            "PROJECT_NAME": "FreshLink-test",
            "FASTAPI_ENV": "development",
            "DATABASE_URL": "postgresql://freshlink_test:fixture@test-db/freshlink_test",
        }

    def test_unsafe_environments_refused_before_imports(self):
        cases = [
            {"FRESHLINK_GATED_FLEET_PROOF": ""},
            {"PROJECT_NAME": "FreshLink"},
            {"FASTAPI_ENV": "production"},
            {"SMTP_HOST": "unexpected"},
            {"SMTP_PASSWORD": "unexpected"},
            {"SENTRY_DSN": "unexpected"},
            {
                "DATABASE_URL": "postgresql://freshlink_test:x@freshlink-db-1/freshlink_test"
            },
            {"DATABASE_URL": "postgresql://postgres:x@test-db/freshlink_test"},
            {"DATABASE_URL": "postgresql://freshlink_test:x@test-db/app"},
        ]
        for changes in cases:
            with (
                self.subTest(changes=tuple(changes)),
                patch.dict(os.environ, self.environment() | changes, clear=True),
                patch.object(Path, "is_file", return_value=True),
            ):
                with self.assertRaises(RuntimeError):
                    guard.preflight()

    def test_host_execution_refused(self):
        with (
            patch.dict(os.environ, self.environment(), clear=True),
            patch.object(Path, "is_file", return_value=False),
        ):
            with self.assertRaises(RuntimeError):
                guard.preflight()
