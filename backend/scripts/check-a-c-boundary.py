"""Read-only cross-branch A/C pure-boundary diagnostic; not a DB/E1 test."""

import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import UUID

import app.modules

# Separate read-only C mount, without altering either branch or writing an
# artificial production integration into the repository.
from app.modules.iot.ingress import DeviceContext, IngressRejected, receive_telemetry

review_root = os.environ.get("FRESHLINK_C_MODULE_ROOT")
if review_root:
    app.modules.__path__.append(review_root)
prepare_receptions = import_module(
    "app.modules.telemetry.envelope_preview"
).prepare_receptions

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 10, 12, tzinfo=UTC)
TOPIC = f"freshlink/v1/devices/{DEVICE}/telemetry"


def packet():
    return {
        "schema_version": "1.0",
        "device_id": str(DEVICE),
        "message_id": "33333333-3333-4333-8333-333333333333",
        "sampled_at": "2026-10-12T00:00:00Z",
        "metrics": [
            {"key": "temperature", "value": 10.0, "unit": "°C"},
            {"key": "humidity", "value": 80, "unit": "%RH"},
        ],
    }


def envelope(raw, now=NOW):
    return receive_telemetry(
        topic=TOPIC,
        payload=(" \n" + json.dumps(raw, ensure_ascii=False) + "\t").encode(),
        context=DeviceContext(DEVICE, WAREHOUSE),
        received_at=now,
    )


class CrossBoundary(unittest.TestCase):
    def test_high_temperature_and_raw_preservation(self):
        first = envelope(packet())
        result = prepare_receptions([first], threshold_c=5)[0]
        self.assertEqual(result["raw_text"], first.raw_text)
        self.assertNotIn("warehouse_id", result["raw_payload"])
        self.assertEqual(result["normalized"]["warehouse_id"], str(WAREHOUSE))
        self.assertEqual(result["risk"]["state"], "candidate")
        self.assertEqual(result["risk"]["temperature_c"], 10)

    def test_self_claimed_receipt_is_only_raw_evidence(self):
        first = envelope({**packet(), "received_at": "1900-01-01T00:00:00Z"})
        result = prepare_receptions([first], threshold_c=5)[0]
        self.assertEqual(result["raw_payload"]["received_at"], "1900-01-01T00:00:00Z")
        self.assertEqual(result["normalized"]["received_at"], "2026-10-12T00:00:00Z")

    def test_network_redelivery_keeps_content_not_receipt(self):
        results = prepare_receptions(
            [envelope(packet()), envelope(packet(), NOW + timedelta(seconds=1))],
            threshold_c=5,
        )
        self.assertEqual([r["batch_duplicate"] for r in results], ["new", "duplicate"])
        self.assertNotEqual(
            results[0]["trusted"]["reception_id"], results[1]["trusted"]["reception_id"]
        )
        self.assertEqual(results[1]["risk"]["state"], "unknown")

    def test_extension_change_is_conflict(self):
        results = prepare_receptions(
            [
                envelope({**packet(), "extension": {"x": 1}}),
                envelope({**packet(), "extension": {"x": 2}}),
            ],
            threshold_c=5,
        )
        self.assertEqual(results[1]["batch_duplicate"], "conflict")
        self.assertEqual(results[1]["risk"]["state"], "unknown")

    def test_member_and_metric_order_is_content_equivalent(self):
        changed = dict(reversed(list(packet().items())))
        changed["metrics"] = list(reversed(changed["metrics"]))
        results = prepare_receptions([envelope(packet()), envelope(changed)])
        self.assertEqual(results[1]["batch_duplicate"], "duplicate")

    def test_metric_shape_bounds_are_unknown_not_candidate(self):
        for kind in ("count", "unit", "key"):
            raw = packet()
            if kind == "count":
                raw["metrics"] += [
                    {"key": f"extra{i}", "value": 1, "unit": "x"} for i in range(15)
                ]
            elif kind == "unit":
                raw["metrics"][1]["unit"] = "x" * 65
            else:
                raw["metrics"].append({"key": "x" * 65, "value": 1, "unit": "x"})
            result = prepare_receptions([envelope(raw)], threshold_c=5)[0]
            with self.subTest(kind=kind):
                self.assertEqual(result["risk"]["state"], "unknown")
                self.assertEqual(result["risk"]["reason"], "invalid_metric_shape")
                self.assertEqual(result["raw_payload"], raw)

    def test_no_rule_and_invalid_time_are_not_normal(self):
        self.assertEqual(
            prepare_receptions([envelope(packet())])[0]["risk"]["state"], "unknown"
        )
        result = prepare_receptions(
            [envelope({**packet(), "sampled_at": "bad"})], threshold_c=5
        )[0]
        self.assertEqual(result["risk"]["state"], "unknown")

    def test_bad_unit_preserved_not_silently_converted(self):
        raw = packet()
        raw["metrics"][0]["unit"] = "°F"
        result = prepare_receptions([envelope(raw)], threshold_c=5)[0]
        self.assertEqual(result["risk"]["state"], "unknown")
        self.assertNotIn("temperature", result["normalized"]["metrics"])
        self.assertEqual(result["raw_payload"], raw)

    def test_auth_identity_and_schema_fail_before_C(self):
        for field, value in (
            ("device_id", str(WAREHOUSE)),
            ("warehouse_id", str(DEVICE)),
            ("schema_version", "2.0"),
        ):
            with self.subTest(field=field), self.assertRaises(IngressRejected):
                envelope({**packet(), field: value})


if __name__ == "__main__":
    unittest.main(verbosity=2)
