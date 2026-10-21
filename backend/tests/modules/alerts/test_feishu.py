"""Feishu custom-bot transport regressions (B3 second channel).

All HTTP interaction is synthetic: a deterministic ``open_fn`` captures
the outgoing request and returns a canned response or raises. No
network, no real webhook URL, no real secret anywhere in this file.
Covers: URL validation (official host/path only, no leaks), official
signing, the four-way outcome classification including the "HTTP 200
is not success" business-code check, bounded-timeout semantics, and
the no-redirect rule.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from typing import Any

from app.modules.alerts.notifications.feishu import (
    FeishuWebhookConfig,
    FeishuWebhookTransport,
)
from app.modules.alerts.notifications.transport import (
    DeliveryOutcome,
    NotificationEnvelope,
    NotificationKind,
)

_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/00000000-0000"
_SECRET = "test-secret-not-a-real-credential"
_FIXED_NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)


class _FakeResponse:
    def __init__(
        self,
        body: bytes = b'{"code":0,"msg":"success"}',
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body


class _Capture:
    """Deterministic open_fn: records the request, returns/raises canned."""

    def __init__(
        self,
        response: _FakeResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or _FakeResponse()
        self.error = error
        self.calls: list[tuple[urllib.request.Request, float]] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> Any:
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        return self.response

    @property
    def payload(self) -> dict[str, Any]:
        assert self.calls, "no request captured"
        return json.loads(self.calls[0][0].data.decode("utf-8"))


def _envelope() -> NotificationEnvelope:
    return NotificationEnvelope(
        source_event_id=uuid.UUID("77777777-7777-4777-8777-777777777777"),
        kind=NotificationKind.APP_PUSH,
        subject="温度超上限",
        body_text="一号冷库 batch-01 温度 11.0°C 超过阈值 8.0°C。",
    )


def _transport(
    capture: _Capture, *, secret: str = "", url: str = _URL
) -> FeishuWebhookTransport:
    return FeishuWebhookTransport(
        FeishuWebhookConfig(webhook_url=url, secret=secret, timeout_seconds=3.0),
        open_fn=capture,
        clock=lambda: _FIXED_NOW,
    )


def _http_error(
    code: int, headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        _URL,
        code,
        "error",
        hdrs=headers or {},
        fp=None,  # type: ignore[arg-type]
    )


class ConfigValidationCase(unittest.TestCase):
    def test_official_url_accepted(self) -> None:
        cfg = FeishuWebhookConfig(webhook_url=_URL)
        cfg.validate()  # must not raise

    def test_non_https_rejected_without_echoing_url(self) -> None:
        cfg = FeishuWebhookConfig(webhook_url=_URL.replace("https", "http"))
        with self.assertRaises(ValueError) as ctx:
            cfg.validate()
        self.assertNotIn(_URL, str(ctx.exception))

    def test_foreign_host_and_wrong_path_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FeishuWebhookConfig(
                webhook_url="https://evil.example.com/open-apis/bot/v2/hook/x"
            ).validate()
        with self.assertRaises(ValueError):
            FeishuWebhookConfig(
                webhook_url="https://open.feishu.cn/other/path"
            ).validate()

    def test_query_string_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FeishuWebhookConfig(webhook_url=f"{_URL}?token=leak").validate()

    def test_repr_and_str_never_leak_credentials(self) -> None:
        cfg = FeishuWebhookConfig(webhook_url=_URL, secret=_SECRET)
        rendered = f"{cfg!r} {cfg}"
        self.assertNotIn(_URL, rendered)
        self.assertNotIn(_SECRET, rendered)

    def test_from_env_reads_variables(self) -> None:
        cfg = FeishuWebhookConfig.from_env(
            {
                "FEISHU_WEBHOOK_URL": _URL,
                "FEISHU_WEBHOOK_SECRET": _SECRET,
                "FEISHU_WEBHOOK_TIMEOUT_S": "2.5",
            }
        )
        self.assertEqual(cfg.webhook_url, _URL)
        self.assertEqual(cfg.secret, _SECRET)
        self.assertEqual(cfg.timeout_seconds, 2.5)


class SigningCase(unittest.TestCase):
    def test_signature_matches_official_algorithm(self) -> None:
        capture = _Capture()
        _transport(capture, secret=_SECRET).send(_envelope())
        payload = capture.payload
        self.assertEqual(payload["timestamp"], str(int(_FIXED_NOW.timestamp())))
        string_to_sign = f"{int(_FIXED_NOW.timestamp())}\n{_SECRET}"
        expected = base64.b64encode(
            hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode("utf-8")
        self.assertEqual(payload["sign"], expected)

    def test_signed_secret_never_appears_in_body(self) -> None:
        capture = _Capture()
        _transport(capture, secret=_SECRET).send(_envelope())
        self.assertNotIn(_SECRET, json.dumps(capture.payload, ensure_ascii=False))

    def test_unsigned_config_omits_timestamp_and_sign(self) -> None:
        capture = _Capture()
        _transport(capture).send(_envelope())
        self.assertNotIn("timestamp", capture.payload)
        self.assertNotIn("sign", capture.payload)


class OutcomeClassificationCase(unittest.TestCase):
    def test_code_zero_is_service_acceptance_not_terminal_receipt(self) -> None:
        capture = _Capture(_FakeResponse())
        result = _transport(capture, secret=_SECRET).send(_envelope())
        self.assertIs(result.outcome, DeliveryOutcome.ACCEPTED_BY_SERVICE)
        self.assertTrue(result.delivered)
        self.assertIn("SERVICE acceptance", result.detail)
        self.assertIn("out-of-band", result.detail)

    def test_nonzero_business_code_is_permanent(self) -> None:
        capture = _Capture(_FakeResponse(b'{"code":190021,"msg":"keyword not found"}'))
        result = _transport(capture).send(_envelope())
        self.assertIs(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertFalse(result.delivered)
        self.assertIn("190021", result.detail)
        self.assertIn("keyword not found", result.detail)

    def test_html_200_is_transient_unrecognized(self) -> None:
        capture = _Capture(_FakeResponse(b"<html>gateway</html>"))
        result = _transport(capture).send(_envelope())
        self.assertIs(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertFalse(result.delivered)

    def test_rate_limit_is_transient_with_retry_after(self) -> None:
        capture = _Capture(error=_http_error(429, {"Retry-After": "30"}))
        result = _transport(capture).send(_envelope())
        self.assertIs(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(result.retry_after_s, 30.0)

    def test_server_and_client_errors_split_transient_permanent(self) -> None:
        server = _transport(_Capture(error=_http_error(503))).send(_envelope())
        self.assertIs(server.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        client = _transport(_Capture(error=_http_error(404))).send(_envelope())
        self.assertIs(client.outcome, DeliveryOutcome.PERMANENT_FAILURE)

    def test_redirect_is_refused_as_permanent(self) -> None:
        capture = _Capture(error=_http_error(302))
        result = _transport(capture).send(_envelope())
        self.assertIs(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIn("redirect", result.detail)

    def test_bounded_timeout_is_its_own_outcome(self) -> None:
        capture = _Capture(error=TimeoutError("timed out"))
        result = _transport(capture).send(_envelope())
        self.assertIs(result.outcome, DeliveryOutcome.TIMEOUT)
        self.assertFalse(result.delivered)
        self.assertIn("UNKNOWN", result.detail)

    def test_connection_refused_is_transient(self) -> None:
        capture = _Capture(error=urllib.error.URLError(OSError("connection refused")))
        result = _transport(capture).send(_envelope())
        self.assertIs(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertIn("connection refused", result.detail)


class RequestShapeCase(unittest.TestCase):
    def test_request_targets_validated_url_with_json_and_timeout(self) -> None:
        capture = _Capture()
        _transport(capture).send(_envelope())
        request, timeout = capture.calls[0]
        self.assertEqual(request.full_url, _URL)
        self.assertEqual(request.headers.get("Content-type"), "application/json")
        self.assertEqual(timeout, 3.0)

    def test_body_carries_traceable_source_event_id(self) -> None:
        capture = _Capture()
        result = _transport(capture).send(_envelope())
        self.assertTrue(result.delivered)
        self.assertIn(
            "77777777-7777-4777-8777-777777777777", capture.payload["content"]["text"]
        )
        self.assertIn("温度超上限", capture.payload["content"]["text"])
        self.assertEqual(capture.payload["msg_type"], "text")


if __name__ == "__main__":
    unittest.main()
