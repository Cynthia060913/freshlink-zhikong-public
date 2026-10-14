"""Pure A1 boundary tests; no broker, database writes or notification sends."""

import hashlib
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID

from app.modules.iot.ingress import (
    MAX_JSON_DEPTH,
    MAX_PAYLOAD_BYTES,
    DeviceContext,
    IngressRejected,
    receive_telemetry,
)

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 10, 12, tzinfo=UTC)
TOPIC = f"freshlink/v1/devices/{DEVICE}/telemetry"
CONTEXT = DeviceContext(DEVICE, WAREHOUSE)


def observation() -> dict:
    return {
        "schema_version": "1.0",
        "device_id": str(DEVICE),
        "warehouse_id": str(WAREHOUSE),
        "message_id": "33333333-3333-4333-8333-333333333333",
        "sampled_at": "2026-10-12T00:00:00Z",
        "metrics": [{"key": "temperature", "value": 5.0, "unit": "°C"}],
    }


def encoded(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class TestIngress(unittest.TestCase):
    def receive(self, payload: bytes, **kwargs):
        args = {
            "topic": TOPIC,
            "payload": payload,
            "context": CONTEXT,
            "received_at": NOW,
        }
        args.update(kwargs)
        return receive_telemetry(**args)

    def reject(self, payload: bytes, reason: str, **kwargs) -> None:
        with self.assertRaises(IngressRejected) as caught:
            self.receive(payload, **kwargs)
        error = caught.exception
        self.assertEqual(str(error), reason)
        self.assertEqual(error.reason_code, reason)
        self.assertEqual(error.payload_bytes, len(payload))
        self.assertEqual(error.payload_sha256, hashlib.sha256(payload).hexdigest())
        self.assertFalse(hasattr(error, "raw_text"))

    def test_preserves_exact_text_and_independent_snapshot(self):
        payload = b" \n" + encoded(observation()) + b"\t"
        envelope = self.receive(payload)
        self.assertEqual(envelope.raw_text.encode(), payload)
        self.assertEqual(envelope.raw_payload, observation())
        snapshot = envelope.raw_payload
        snapshot["metrics"][0]["value"] = 99
        self.assertEqual(envelope.raw_payload["metrics"][0]["value"], 5)
        with self.assertRaises(FrozenInstanceError):
            envelope.received_at = NOW

    def test_server_identity_and_clock(self):
        raw = observation()
        raw["received_at"] = "1900-01-01T00:00:00Z"
        envelope = self.receive(encoded(raw))
        self.assertEqual(envelope.authenticated_device_id, DEVICE)
        self.assertEqual(envelope.warehouse_id, WAREHOUSE)
        self.assertEqual(envelope.received_at, NOW)
        self.assertEqual(envelope.raw_payload["received_at"], raw["received_at"])

    def test_missing_warehouse_does_not_rewrite_raw(self):
        raw = observation()
        del raw["warehouse_id"]
        envelope = self.receive(encoded(raw))
        self.assertEqual(envelope.warehouse_id, WAREHOUSE)
        self.assertNotIn("warehouse_id", envelope.raw_payload)

    def test_server_clock_normalized_and_naive_rejected(self):
        local = NOW.astimezone(timezone(timedelta(hours=8)))
        self.assertEqual(
            self.receive(encoded(observation()), received_at=local).received_at, NOW
        )
        with self.assertRaisesRegex(ValueError, "server-generated"):
            self.receive(encoded(observation()), received_at=NOW.replace(tzinfo=None))

    def test_network_reception_ids_are_unique_not_message_ids(self):
        payload = encoded(observation())
        first, second = self.receive(payload), self.receive(payload)
        self.assertNotEqual(first.reception_id, second.reception_id)
        self.assertEqual(
            first.raw_payload["message_id"], second.raw_payload["message_id"]
        )

    def test_untrusted_device_states(self):
        for changes, reason in [
            ({"registered": False}, "device_unknown"),
            ({"enabled": False}, "device_disabled"),
            ({"warehouse_id": None}, "device_unbound"),
        ]:
            with self.subTest(reason=reason):
                self.reject(
                    encoded(observation()), reason, context=replace(CONTEXT, **changes)
                )

    def test_topic_boundaries(self):
        for topic in [
            TOPIC + "/",
            TOPIC + "extra",
            TOPIC.replace(str(DEVICE), str(WAREHOUSE)),
            TOPIC.replace("telemetry", "heartbeat"),
            "#",
            "",
        ]:
            with self.subTest(topic=topic):
                self.reject(encoded(observation()), "topic_mismatch", topic=topic)

    def test_identity_claims(self):
        for field in ["device_id", "warehouse_id"]:
            for value in [
                None,
                1,
                {},
                [],
                "",
                "bad-id",
                "44444444-4444-4444-8444-444444444444",
            ]:
                with self.subTest(field=field, value=value):
                    raw = observation()
                    raw[field] = value
                    self.reject(encoded(raw), "identity_mismatch")
        raw = observation()
        del raw["device_id"]
        self.reject(encoded(raw), "identity_mismatch")

    def test_version_is_exact(self):
        for value in [None, 1, 1.0, "draft-1", "1.1", {}, []]:
            with self.subTest(value=value):
                raw = observation()
                raw["schema_version"] = value
                self.reject(encoded(raw), "invalid_schema")

    def test_encoding_and_json_errors(self):
        self.reject(b"\xff", "invalid_encoding")
        for payload in [
            b"",
            b"{",
            b"{secret}",
            b'{"x": NaN}',
            b'{"x": Infinity}',
            b'{"x": -Infinity}',
            b'{"x":1,"x":2}',
            b'{"nested":{"x":1,"x":2}}',
        ]:
            with self.subTest(payload=payload):
                self.reject(payload, "invalid_json")

    def test_top_level_must_be_object(self):
        for payload in [b"null", b"[]", b"true", b"12", b'"text"']:
            self.reject(payload, "invalid_payload")

    def test_payload_bytes_limit_includes_utf8(self):
        payload = encoded(observation())
        exact = payload + b" " * (MAX_PAYLOAD_BYTES - len(payload))
        self.assertEqual(len(self.receive(exact).raw_text.encode()), MAX_PAYLOAD_BYTES)
        self.reject(exact + b" ", "payload_too_large")
        self.reject("温".encode() * (MAX_PAYLOAD_BYTES // 3 + 1), "payload_too_large")

    def test_depth_and_parser_recursion_are_bounded(self):
        raw = observation()
        raw["extra"] = []
        payload = encoded(raw).replace(
            b'"extra": []',
            b'"extra": ' + b"[" * MAX_JSON_DEPTH + b"0" + b"]" * MAX_JSON_DEPTH,
        )
        self.reject(payload, "invalid_payload")
        # Parser recursion thresholds vary across Python versions. Either path
        # must reject rather than leak RecursionError or accept the structure.
        with self.assertRaises(IngressRejected) as caught:
            self.receive(b"[" * 2000 + b"0" + b"]" * 2000)
        self.assertIn(caught.exception.reason_code, {"invalid_json", "invalid_payload"})
        with patch("app.modules.iot.ingress.json.loads", side_effect=RecursionError):
            self.reject(encoded(observation()), "invalid_json")

    def test_bad_quality_is_not_silently_sanitized(self):
        for value in [None, "not-number", -9999, 10000, {"unexpected": "value"}]:
            raw = observation()
            raw["sampled_at"] = "invalid-time"
            raw["metrics"][0].update(value=value, unit="unknown")
            self.assertEqual(self.receive(encoded(raw)).raw_payload, raw)


if __name__ == "__main__":
    unittest.main()
