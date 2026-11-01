"""No DB/runtime imports; privacy and bounded-output regressions."""

import importlib.util
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "control_diagnostics", Path(__file__).with_name("control_proof_diagnostics.py")
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DiagnosticsTests(unittest.TestCase):
    def test_sensitive_raw_fields_and_error_text_never_enter_report(self):
        now = datetime.now(UTC)
        row = {
            "id": "11111111-1111-4111-8111-111111111111",
            "device_id": "22222222-2222-4222-8222-222222222222",
            "normalized": {
                "sampled_at": now.isoformat(),
                "metrics": {"temperature": 9.5},
                "secret": "SYNTHETIC_PRIVATE",
            },
            "first_received_at_utc": now,
            "quality": ["ok"],
            "raw_text": "SYNTHETIC_PRIVATE",
            "password": "SYNTHETIC_PRIVATE",
        }
        report = module.checkpoint_view(
            stage="stored_cooling",
            error=RuntimeError("SYNTHETIC_PRIVATE"),
            started=now,
            ack_seen=now,
            rule_start=now,
            baseline=row,
            observations=[row] * 100,
            counts={"observations": 100, "secret": "SYNTHETIC_PRIVATE"},
            plant={
                "temperature_c": 9.2,
                "execution_count": 1,
                "secret": "SYNTHETIC_PRIVATE",
            },
        )
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(report))
        self.assertEqual(len(report["last_observations"]), 64)
        self.assertEqual(report["observation_total"], 100)
        self.assertEqual(report["baseline"]["temperature_c"], 9.5)
        self.assertEqual(report["error_type"], "RuntimeError")

    def test_invalid_identifier_time_and_nonfinite_value_are_not_echoed(self):
        view = module.observation_view(
            {
                "id": "SYNTHETIC_PRIVATE",
                "device_id": "SYNTHETIC_PRIVATE",
                "normalized": {
                    "sampled_at": "SYNTHETIC_PRIVATE",
                    "metrics": {"temperature": float("nan")},
                },
                "first_received_at_utc": "SYNTHETIC_PRIVATE",
                "quality": ["bad"],
            }
        )
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(view))
        self.assertIsNone(view["temperature_c"])
        self.assertFalse(view["quality_ok"])


if __name__ == "__main__":
    unittest.main()
