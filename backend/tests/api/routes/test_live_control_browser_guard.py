"""Run in the cached container: guards must fail before imports/migrations."""

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "control_browser_guard",
    Path(__file__).resolve().parents[3] / "scripts/check-live-control-browser.py",
)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class BrowserGuardTests(unittest.TestCase):
    def setUp(self):
        self.environment = {
            "CONTROL_BROWSER_SOURCE": "a" * 40,
            "DATABASE_URL": "postgresql://freshlink_test:fixture@test-db/freshlink_test",
            "PROJECT_NAME": "FreshLink-test",
            "FASTAPI_ENV": "development",
            "SMTP_HOST": "",
            "SENTRY_DSN": "",
        }

    def check(self, allowed=True, docker=True, **changes):
        with (
            patch.dict(os.environ, self.environment | changes, clear=True),
            patch.object(Path, "is_file", return_value=docker),
        ):
            if allowed:
                self.assertEqual(guard.preflight(), "a" * 40)
            else:
                with self.assertRaisesRegex(RuntimeError, "Refusing non-disposable"):
                    guard.preflight()

    def test_exact_disposable(self):
        self.check()

    def test_host_refused(self):
        self.check(False, docker=False)

    def test_wrong_host_refused(self):
        self.check(
            False,
            DATABASE_URL="postgresql://freshlink_test:x@freshlink-db-1/freshlink_test",
        )

    def test_wrong_database_refused(self):
        self.check(False, DATABASE_URL="postgresql://freshlink_test:x@test-db/app")

    def test_wrong_user_refused(self):
        self.check(False, DATABASE_URL="postgresql://postgres:x@test-db/freshlink_test")

    def test_wrong_project_refused(self):
        self.check(False, PROJECT_NAME="FreshLink")

    def test_production_refused(self):
        self.check(False, FASTAPI_ENV="production")

    def test_external_configuration_refused(self):
        for name in ("SMTP_HOST", "SENTRY_DSN"):
            with self.subTest(name=name):
                self.check(False, **{name: "unexpected"})

    def test_bad_source_refused(self):
        for value in ("", "main", "a" * 39, "g" * 40, "a" * 41):
            with self.subTest(value=value):
                self.check(False, CONTROL_BROWSER_SOURCE=value)


if __name__ == "__main__":
    unittest.main()
