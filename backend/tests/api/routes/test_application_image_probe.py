"""Synthetic negative regressions; never reported as a live image HTTP proof."""

import ast
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

spec = importlib.util.spec_from_file_location(
    "application_image_probe",
    Path(__file__).resolve().parents[3] / "scripts/check-application-image.py",
)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ApplicationImageProbeTests(unittest.TestCase):
    def run_probe(self, broken=None):
        counts = {}

        def handler(request):
            key = (request.method, request.url.path)
            counts[key] = counts.get(key, 0) + 1
            if broken is not None:
                response = broken(request, counts[key])
                if response is not None:
                    return response
            if request.url.path == "/":
                return httpx.Response(
                    200,
                    text='<script src="/assets/main-a1.js"></script>',
                    headers={"content-type": "text/html"},
                )
            if request.url.path == "/assets/main-a1.js":
                return httpx.Response(
                    200,
                    text="// synthetic bundle for negative regression",
                    headers={"content-type": "text/javascript"},
                )
            if request.url.path.endswith("health-check/"):
                return httpx.Response(200, json=True)
            if request.url.path.endswith("access-token"):
                return httpx.Response(
                    200, json={"access_token": "synthetic-unit-token-only"}
                )
            if request.url.path.endswith("users/me"):
                return httpx.Response(
                    200, json={"is_active": True, "is_superuser": True}
                )
            if request.url.path == "/api/v1/devices/":
                if request.method == "POST":
                    return httpx.Response(
                        201 if counts[key] == 1 else 409,
                        json={
                            "id": "synthetic-device",
                            "connectivity": "unknown",
                            "is_simulated": True,
                        },
                    )
                if "authorization" not in request.headers:
                    return httpx.Response(401, json={"detail": "synthetic anonymous"})
                return httpx.Response(200, json={"count": 0 if counts[key] == 2 else 1})
            return httpx.Response(200, json={"id": "synthetic-device"})

        with httpx.Client(
            base_url="http://application:8000", transport=httpx.MockTransport(handler)
        ) as client:
            return probe.prove(client, "a" * 40)

    def test_complete_synthetic_boundary_does_not_leak_token(self):
        result = self.run_probe()
        self.assertIn("not_E1", result["status"])
        self.assertNotIn("synthetic-unit-token", json.dumps(result))
        self.assertIn("second computer", result["not_verified"])

    def test_wrong_origin_refused_before_requests(self):
        with httpx.Client(base_url="http://example.com") as client:
            with self.assertRaisesRegex(RuntimeError, "origin"):
                probe.prove(client, "a" * 40)

    def test_failed_responses_never_pass(self):
        bad = [
            ("/", "GET", 1, 302, {}, "frontend HTML"),
            ("/assets/main-a1.js", "GET", 1, 404, {}, "JavaScript response"),
            ("/api/v1/utils/health-check/", "GET", 1, 200, False, "server health"),
            ("/api/v1/devices/", "GET", 1, 200, {}, "anonymous device refusal"),
            ("/api/v1/login/access-token", "POST", 1, 200, {}, "real login"),
            ("/api/v1/users/me", "GET", 1, 200, {"is_active": False}, "admin readback"),
            ("/api/v1/devices/", "GET", 2, 200, {"count": 7}, "owned empty fixture DB"),
            (
                "/api/v1/devices/",
                "POST",
                1,
                201,
                {"connectivity": "online"},
                "register simulated",
            ),
            ("/api/v1/devices/", "POST", 2, 201, {}, "duplicate refusal"),
            (
                "/api/v1/devices/",
                "GET",
                3,
                200,
                {"count": 2},
                "duplicate did not increase",
            ),
            (
                "/api/v1/devices/synthetic-device",
                "GET",
                1,
                200,
                {"id": "wrong"},
                "saved device detail",
            ),
        ]
        for path, method, nth, status, value, reason in bad:
            with self.subTest(path=path, method=method, nth=nth):

                def broken(
                    request,
                    count,
                    *,
                    path=path,
                    method=method,
                    nth=nth,
                    status=status,
                    value=value,
                ):
                    if (
                        request.url.path == path
                        and request.method == method
                        and count == nth
                    ):
                        return httpx.Response(status, json=value)
                    return None

                with self.assertRaisesRegex(RuntimeError, reason):
                    self.run_probe(broken)

    def test_missing_script_reference_fails(self):
        def broken(request, _count):
            if request.url.path == "/":
                return httpx.Response(
                    200,
                    text="<html>no built JS</html>",
                    headers={"content-type": "text/html"},
                )
            return None

        with self.assertRaisesRegex(RuntimeError, "script reference"):
            self.run_probe(broken)

    def test_host_preflight_refused(self):
        with patch.object(Path, "is_file", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "container"):
                probe.preflight()

    def test_proof_does_not_depend_on_removable_assert_statements(self):
        tree = ast.parse(Path(probe.__file__).read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))

    def test_uncontrolled_environment_refused(self):
        valid = {
            "IMAGE_PROOF_SOURCE": "a" * 40,
            "IMAGE_CANDIDATE_PROOF": "1",
            "DATABASE_URL": "postgresql://freshlink_test:synthetic@test-db/freshlink_test",
            "PROJECT_NAME": "FreshLink-test",
            "FASTAPI_ENV": "development",
        }
        with (
            patch.object(Path, "is_file", return_value=True),
            patch.dict(probe.os.environ, valid, clear=True),
        ):
            self.assertEqual(probe.preflight(), "a" * 40)
        for key, value in [
            ("IMAGE_CANDIDATE_PROOF", "0"),
            ("IMAGE_PROOF_SOURCE", ""),
            ("DATABASE_URL", "postgresql://real:synthetic@production/real"),
            ("SMTP_HOST", "smtp.example.com"),
            ("SENTRY_DSN", "synthetic"),
        ]:
            with (
                self.subTest(key=key),
                patch.object(Path, "is_file", return_value=True),
                patch.dict(probe.os.environ, valid | {key: value}, clear=True),
            ):
                with self.assertRaises(RuntimeError):
                    probe.preflight()


if __name__ == "__main__":
    unittest.main()
