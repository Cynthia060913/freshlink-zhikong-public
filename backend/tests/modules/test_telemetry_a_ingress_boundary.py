"""A ingress to C projection boundary; no broker, database, or queue is started."""

import json
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.modules.iot.ingress import (
    DeviceContext,
    IngressRejected,
    ReceptionEnvelope,
    receive_telemetry,
)
from app.modules.telemetry.envelope_preview import prepare_receptions

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
TOPIC = f"freshlink/v1/devices/{DEVICE}/telemetry"
SERVER_TIME = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def _raw(*, temperature: object = 12, unit: str = "°C") -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "device_id": str(DEVICE),
        "message_id": str(uuid4()),
        "sampled_at": "2026-10-13T11:59:00Z",
        "metrics": [
            {"key": "temperature", "value": temperature, "unit": unit},
            {"key": "humidity", "value": 80, "unit": "%RH"},
        ],
    }


def _receive(
    raw: dict[str, Any], received_at: datetime = SERVER_TIME
) -> ReceptionEnvelope:
    return receive_telemetry(
        topic=TOPIC,
        payload=json.dumps(raw, ensure_ascii=False).encode("utf-8"),
        context=DeviceContext(device_id=DEVICE, warehouse_id=WAREHOUSE),
        received_at=received_at,
    )


class ActualCodeBoundaryTests(unittest.TestCase):
    def test_valid_heat_keeps_exact_raw_and_trusted_binding(self) -> None:
        raw = _raw()
        envelope = _receive(raw)
        projected = prepare_receptions([envelope], threshold_c=8)[0]
        self.assertEqual(projected["raw_text"], envelope.raw_text)
        self.assertEqual(projected["raw_payload"], raw)
        self.assertNotIn("warehouse_id", projected["raw_payload"])
        self.assertEqual(projected["normalized"]["warehouse_id"], str(WAREHOUSE))
        self.assertEqual(projected["normalized"]["received_at"], "2026-10-13T12:00:00Z")
        self.assertEqual(projected["normalized"]["metrics"]["temperature"], 12.0)
        self.assertEqual(projected["risk"]["state"], "candidate")
        self.assertEqual(projected["quality"], ["ok"])

    def test_network_redelivery_and_changed_content(self) -> None:
        raw = _raw()
        repeated = deepcopy(raw)
        conflict = deepcopy(raw)
        conflict["metrics"][0]["value"] = 14
        envelopes = [
            _receive(raw),
            _receive(repeated, SERVER_TIME + timedelta(seconds=1)),
            _receive(conflict, SERVER_TIME + timedelta(seconds=2)),
        ]
        projected = prepare_receptions(envelopes, threshold_c=8)
        self.assertEqual(
            [row["batch_duplicate"] for row in projected],
            ["new", "duplicate", "conflict"],
        )
        self.assertEqual(
            [row["risk"]["state"] for row in projected],
            ["candidate", "unknown", "unknown"],
        )
        self.assertEqual(projected[2]["raw_payload"]["metrics"][0]["value"], 14)
        self.assertEqual(len({row["trusted"]["reception_id"] for row in projected}), 3)

    def test_missing_bad_unit_and_bad_sample_time_remain_unknown(self) -> None:
        missing = _raw(temperature=None)
        wrong_unit = _raw(unit="°F")
        bad_time = _raw()
        bad_time["sampled_at"] = "2026-10-13 11:59:00"
        projected = prepare_receptions(
            [_receive(item) for item in (missing, wrong_unit, bad_time)],
            threshold_c=8,
        )
        self.assertEqual([row["risk"]["state"] for row in projected], ["unknown"] * 3)
        self.assertIn("missing_value:temperature", projected[0]["quality"])
        self.assertIn("invalid_unit:temperature", projected[1]["quality"])
        self.assertIn("invalid_time:sampled_at", projected[2]["quality"])
        self.assertEqual(projected[1]["raw_payload"]["metrics"][0]["unit"], "°F")

    def test_device_reported_receipt_does_not_override_server_clock(self) -> None:
        raw = _raw()
        raw["received_at"] = "2020-01-01T00:00:00Z"
        projected = prepare_receptions([_receive(raw)], threshold_c=8)[0]
        self.assertEqual(projected["raw_payload"]["received_at"], raw["received_at"])
        self.assertEqual(
            projected["processing_view"]["received_at"], "2026-10-13T12:00:00Z"
        )
        self.assertEqual(projected["normalized"]["received_at"], "2026-10-13T12:00:00Z")

    def test_a_rejects_wrong_identity_before_c(self) -> None:
        raw = _raw()
        raw["warehouse_id"] = str(uuid4())
        with self.assertRaises(IngressRejected) as context:
            _receive(raw)
        self.assertEqual(context.exception.reason_code, "identity_mismatch")


if __name__ == "__main__":
    unittest.main()
