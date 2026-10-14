"""Fixed A-envelope projection checks; no MQTT, database, or B service."""

import json
import unittest
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from app.modules.telemetry.envelope_preview import prepare_receptions

_EXAMPLES = Path(__file__).resolve().parents[3] / "docs" / "modules" / "C" / "examples"
NOW = datetime(2026, 10, 10, 0, 0, 1, tzinfo=UTC)
DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")


@dataclass(frozen=True)
class ExampleReception:
    raw_text: str
    authenticated_device_id: UUID = DEVICE
    warehouse_id: UUID = WAREHOUSE
    received_at: datetime = NOW
    reception_id: UUID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


def _raw() -> dict:
    fixture = json.loads(
        (_EXAMPLES / "telemetry-envelope-rc1-proposal.json").read_text(encoding="utf-8")
    )
    return deepcopy(fixture["envelope"]["raw_payload"])


def _reception(raw: dict, **changes) -> ExampleReception:
    return ExampleReception(
        raw_text=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
        **changes,
    )


class EnvelopePreviewTests(unittest.TestCase):
    def test_trusted_fields_fill_view_without_rewriting_raw(self) -> None:
        raw = _raw()
        del raw["warehouse_id"]
        raw["received_at"] = "1900-01-01T00:00:00Z"
        reception = _reception(raw)
        result = prepare_receptions([reception], 8)[0]
        self.assertEqual(result["raw_text"], reception.raw_text)
        self.assertEqual(result["raw_payload"], raw)
        self.assertNotIn("warehouse_id", result["raw_payload"])
        self.assertEqual(result["raw_payload"]["received_at"], raw["received_at"])
        self.assertEqual(result["processing_view"]["warehouse_id"], str(WAREHOUSE))
        self.assertEqual(result["normalized"]["received_at"], "2026-10-10T00:00:01Z")
        self.assertEqual(result["quality"], ["ok"])
        self.assertEqual(result["risk"]["state"], "normal")

    def test_heat_and_missing_humidity_are_preserved(self) -> None:
        raw = _raw()
        raw["metrics"][0]["value"] = 12
        raw["metrics"] = raw["metrics"][:1]
        result = prepare_receptions([_reception(raw)], 8)[0]
        self.assertEqual(result["raw_payload"], raw)
        self.assertEqual(result["normalized"]["metrics"]["temperature"], 12.0)
        self.assertIn("missing_metric:humidity", result["quality"])
        self.assertEqual(result["risk"]["state"], "candidate")

    def test_bad_time_and_unit_cannot_become_normal(self) -> None:
        raw = _raw()
        raw["sampled_at"] = "invalid-time"
        raw["metrics"][0]["unit"] = "°F"
        result = prepare_receptions([_reception(raw)], 8)[0]
        self.assertIn("invalid_time:sampled_at", result["quality"])
        self.assertIn("invalid_unit:temperature", result["quality"])
        self.assertEqual(result["risk"]["state"], "unknown")
        self.assertEqual(result["raw_payload"], raw)

    def test_repeat_and_conflict_remain_distinct(self) -> None:
        raw = _raw()
        first = _reception(raw)
        same = deepcopy(raw)
        del same["warehouse_id"]
        same["received_at"] = "1900-01-01T00:00:00Z"
        repeated = _reception(same, reception_id=uuid4())
        changed = deepcopy(raw)
        changed["metrics"][0]["value"] = 12
        conflict = _reception(changed, reception_id=uuid4())
        results = prepare_receptions([first, repeated, conflict], 8)
        self.assertEqual(
            [item["batch_duplicate"] for item in results],
            ["new", "duplicate", "conflict"],
        )
        self.assertEqual(results[1]["raw_payload"], same)
        self.assertEqual(results[2]["raw_payload"], changed)
        self.assertEqual(results[1]["risk"]["state"], "unknown")
        self.assertEqual(results[2]["risk"]["state"], "unknown")

    def test_no_rule_is_unknown(self) -> None:
        result = prepare_receptions([_reception(_raw())])[0]
        self.assertEqual(result["risk"]["reason"], "no_threshold_rule")
        self.assertEqual(result["risk"]["state"], "unknown")

    def test_mismatched_identity_and_untrusted_time_fail(self) -> None:
        raw = _raw()
        with self.assertRaisesRegex(ValueError, "trusted device"):
            prepare_receptions([_reception(raw, authenticated_device_id=uuid4())], 8)
        with self.assertRaisesRegex(ValueError, "trusted warehouse"):
            prepare_receptions([_reception(raw, warehouse_id=uuid4())], 8)
        with self.assertRaisesRegex(ValueError, "aware timestamp"):
            prepare_receptions(
                [_reception(raw, received_at=NOW.replace(tzinfo=None))], 8
            )


if __name__ == "__main__":
    unittest.main()
