"""Local synthetic HTTP replies; no B service or credential is contacted."""

import io
import json
import unittest
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from uuid import UUID

from app.modules.telemetry.b_http_candidate import (
    BHttpCandidatePort,
    HttpCandidateReply,
    UrllibCandidateTransport,
)
from app.modules.telemetry.f03_body_candidate import (
    f03_digest_candidate,
    normalize_f03_body_candidate,
)

SOURCE = "77777777-7777-4777-8777-777777777777"


def _body() -> dict[str, Any]:
    return normalize_f03_body_candidate(
        {
            "schema_version": "1.0",
            "source_event_id": SOURCE,
            "kind": "temperature_high",
            "warehouse_id": "22222222-2222-4222-8222-222222222222",
            "batch_id": "55555555-5555-4555-8555-555555555555",
            "observation_ids": ["00000000-0000-4000-8000-000000000004"],
            "metric_code": "temperature",
            "sampled_at": "2026-10-14T00:01:00Z",
            "value": 11.0,
            "unit": "°C",
            "severity": "warning",
            "rule_snapshot_id": "88888888-8888-4888-8888-888888888888",
            "rule_version": "v1",
            "algorithm_version": "threshold-rule/0.2.0-rc2",
            "evidence": {"comparison": ">", "threshold": 8.0, "quality": ["ok"]},
        }
    )


def _reply(status_code: int, payload: dict[str, Any]) -> HttpCandidateReply:
    return HttpCandidateReply(status_code, json.dumps(payload).encode())


@dataclass
class FakeTransport:
    replies: list[HttpCandidateReply]
    calls: list[tuple[str, str, bytes | None]] = field(default_factory=list)

    def request(self, method: str, path: str, body: bytes | None = None):
        self.calls.append((method, path, body))
        return self.replies.pop(0)


class BHttpCandidatePortTests(unittest.TestCase):
    def test_lookup_and_send_verify_full_new_digest(self) -> None:
        body = _body()
        digest = f03_digest_candidate(body)
        transport = FakeTransport(
            [
                _reply(
                    200,
                    {
                        "source_event_id": SOURCE,
                        "content_digest": digest,
                        "status": "resolved",
                    },
                ),
                _reply(
                    201,
                    {
                        "source_event_id": SOURCE,
                        "status": "accepted_new",
                        "content_digest": digest,
                    },
                ),
            ]
        )
        port = BHttpCandidatePort(transport)
        prior = port.lookup(UUID(SOURCE))
        assert prior is not None
        self.assertEqual(prior.source_event_id, UUID(SOURCE))
        self.assertEqual(prior.body_sha256, digest.removeprefix("sha256:"))
        self.assertEqual(prior.alert_status, "resolved")
        receipt = port.send(body)
        self.assertEqual(receipt.status, "accepted_new")
        self.assertEqual(receipt.body_sha256, prior.body_sha256)
        self.assertEqual(
            transport.calls[0][:2],
            ("GET", f"/api/v1/candidates/by-source/{SOURCE}"),
        )
        self.assertEqual(transport.calls[1][:2], ("POST", "/api/v1/candidates"))
        sent = json.loads(transport.calls[1][2] or b"")
        self.assertIn("related_source_event_id", sent)
        self.assertIsNone(sent["related_source_event_id"])

    def test_lookup_not_found_retryable_and_auth_are_distinct(self) -> None:
        self.assertIsNone(
            BHttpCandidatePort(FakeTransport([_reply(404, {})])).lookup(UUID(SOURCE))
        )
        with self.assertRaises(ConnectionError):
            BHttpCandidatePort(FakeTransport([_reply(503, {})])).lookup(UUID(SOURCE))
        with self.assertRaises(PermissionError):
            BHttpCandidatePort(FakeTransport([_reply(401, {})])).lookup(UUID(SOURCE))
        unverified = BHttpCandidatePort(
            FakeTransport([_reply(200, {"source_event_id": SOURCE, "status": "bad"})])
        ).lookup(UUID(SOURCE))
        assert unverified is not None
        self.assertIsNone(unverified.alert_status)

    def test_post_five_way_and_framework_validation_error(self) -> None:
        expected = [
            (200, "accepted_duplicate"),
            (409, "conflict"),
            (422, "invalid"),
            (503, "retryable_failure"),
        ]
        digest = f03_digest_candidate(_body())
        for status_code, status in expected:
            with self.subTest(status=status):
                reply = _reply(
                    status_code,
                    {
                        "status": status,
                        "source_event_id": SOURCE,
                        "content_digest": digest,
                    },
                )
                self.assertEqual(
                    BHttpCandidatePort(FakeTransport([reply])).send(_body()).status,
                    status,
                )
        framework = _reply(422, {"detail": [{"type": "value_error"}]})
        receipt = BHttpCandidatePort(FakeTransport([framework])).send(_body())
        self.assertEqual(receipt.status, "invalid")
        self.assertEqual(receipt.source_event_id, UUID(SOURCE))

    def test_partial_body_or_unverifiable_success_never_reports_acceptance(
        self,
    ) -> None:
        transport = FakeTransport([_reply(201, {"status": "accepted_new"})])
        with self.assertRaisesRegex(ValueError, "complete normalized"):
            BHttpCandidatePort(transport).send(
                {k: v for k, v in _body().items() if k != "related_source_event_id"}
            )
        self.assertEqual(transport.calls, [])
        with self.assertRaises(ConnectionError):
            BHttpCandidatePort(transport).send(_body())
        with self.assertRaises(ConnectionError):
            BHttpCandidatePort(
                FakeTransport(
                    [
                        _reply(
                            201,
                            {
                                "status": "accepted_duplicate",
                                "source_event_id": SOURCE,
                            },
                        )
                    ]
                )
            ).send(_body())


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _trace):
        return False

    def read(self, _size: int) -> bytes:
        return b"{}"


class FakeOpener:
    def __init__(self, failure: Exception | None = None):
        self.failure = failure
        self.calls: list[tuple[Any, float]] = []

    def open(self, request, timeout: float):
        self.calls.append((request, timeout))
        if self.failure:
            raise self.failure
        return FakeResponse()


class UrllibCandidateTransportTests(unittest.TestCase):
    def test_origin_auth_and_timeout_are_explicit(self) -> None:
        opener = FakeOpener()
        transport = UrllibCandidateTransport(
            base_url="https://alerts.example.test",
            bearer=lambda: "private-value",
            opener=opener,
            timeout_s=3,
        )
        self.assertEqual(
            transport.request("GET", "/api/v1/candidates/by-source/one").status_code,
            200,
        )
        request, timeout = opener.calls[0]
        self.assertEqual(
            request.full_url,
            "https://alerts.example.test/api/v1/candidates/by-source/one",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer private-value")
        self.assertEqual(timeout, 3)
        with self.assertRaises(ValueError):
            UrllibCandidateTransport(
                base_url="http://alerts.example.test", bearer=lambda: "x"
            )
        with self.assertRaises(ValueError):
            UrllibCandidateTransport(
                base_url="https://user:pass@alerts.example.test", bearer=lambda: "x"
            )

    def test_redirect_and_network_failure_do_not_leak_acceptance(self) -> None:
        redirect = HTTPError(
            "https://alerts.example.test/api/v1/candidates",
            302,
            "redirect",
            {},
            io.BytesIO(b""),
        )
        path = "/api/v1/candidates"
        with self.assertRaises(ConnectionError):
            UrllibCandidateTransport(
                base_url="https://alerts.example.test",
                bearer=lambda: "x",
                opener=FakeOpener(redirect),
            ).request("POST", path, b"{}")
        with self.assertRaises(ConnectionError):
            UrllibCandidateTransport(
                base_url="https://alerts.example.test",
                bearer=lambda: "x",
                opener=FakeOpener(URLError("synthetic disconnect")),
            ).request("GET", "/api/v1/candidates/by-source/one")


if __name__ == "__main__":
    unittest.main()
