"""Historical B rule binding for C recovery; synthetic DTOs, no B service."""

from __future__ import annotations

import unittest
from copy import deepcopy

from app.modules.telemetry.recovery_rule_candidate import (
    bind_original_recovery_rule,
)


def _original() -> dict:
    return {
        "kind": "temperature_high",
        "warehouse_id": "22222222-2222-4222-8222-222222222222",
        "batch_id": "33333333-3333-4333-8333-333333333333",
        "metric_code": "temperature",
        "rule_snapshot_id": "88888888-8888-4888-8888-888888888888",
        "rule_version": "temp-v1",
        "severity": "warning",
        "unit": "°C",
        "sampled_at": "2026-10-19T00:01:00Z",
        "evidence": {"comparison": ">", "threshold": 8.0},
    }


def _selection() -> dict:
    return {
        "kind": "threshold_high",
        "warehouse_id": "22222222-2222-4222-8222-222222222222",
        "batch_id": "33333333-3333-4333-8333-333333333333",
        "metric_code": "temperature",
        "snapshot_id": "88888888-8888-4888-8888-888888888888",
        "rule_version": "temp-v1",
        "severity": "warning",
        "unit": "°C",
        "comparison": ">",
        "threshold": 8.0,
        "effective_start_at": "2026-10-19T00:00:00Z",
        "effective_end_at": "2026-10-19T00:02:00Z",
        "recovery": {"comparison": "<=", "threshold": 7.0, "min_samples": 1},
    }


class HistoricalRecoveryRuleCase(unittest.TestCase):
    def test_b_one_sample_rule_is_strengthened_to_s0_minimum(self) -> None:
        rule = bind_original_recovery_rule(_original(), _selection())
        self.assertIsNotNone(rule)
        assert rule is not None
        self.assertEqual(
            (rule.comparison, rule.threshold_c, rule.min_samples, rule.max_gap_seconds),
            ("<=", 7.0, 3, 90),
        )

    def test_stricter_original_rule_is_preserved(self) -> None:
        selected = _selection()
        selected["recovery"] = {
            "comparison": "<",
            "threshold": 7.0,
            "min_samples": 5,
            "max_gap_seconds": 45,
        }
        selected["effective_start_at"] = "2026-10-19T08:00:00+08:00"
        rule = bind_original_recovery_rule(_original(), selected)
        self.assertIsNotNone(rule)
        assert rule is not None
        self.assertEqual(
            (rule.comparison, rule.min_samples, rule.max_gap_seconds), ("<", 5, 45)
        )

    def test_no_rule_or_missing_explicit_recovery_never_proves_recovery(self) -> None:
        self.assertIsNone(bind_original_recovery_rule(_original(), None))
        selected = _selection()
        selected["recovery"] = None
        self.assertIsNone(bind_original_recovery_rule(_original(), selected))

    def test_changed_snapshot_rule_or_threshold_is_rejected(self) -> None:
        for field, changed in (
            ("snapshot_id", "99999999-9999-4999-8999-999999999999"),
            ("rule_version", "temp-v2"),
            ("severity", "critical"),
            ("warehouse_id", "99999999-9999-4999-8999-999999999999"),
            ("threshold", 9.0),
            ("kind", "temperature_low"),
        ):
            with self.subTest(field=field):
                selected = _selection()
                selected[field] = changed
                with self.assertRaises(ValueError):
                    bind_original_recovery_rule(_original(), selected)

    def test_out_of_window_rule_and_invalid_time_are_rejected(self) -> None:
        for field, changed in (
            ("effective_start_at", "2026-10-19T00:01:01Z"),
            ("effective_end_at", "2026-10-19T00:01:00Z"),
            ("effective_start_at", "2026-10-19T00:00:00"),
        ):
            with self.subTest(field=field):
                selected = _selection()
                selected[field] = changed
                with self.assertRaises(ValueError):
                    bind_original_recovery_rule(_original(), selected)

    def test_invalid_recovery_cannot_be_coerced_to_s0_policy(self) -> None:
        for recovery in (
            {},
            {"comparison": ">", "threshold": 7.0, "min_samples": 3},
            {"comparison": "<=", "threshold": float("nan"), "min_samples": 3},
            {"comparison": "<=", "threshold": 7.0, "min_samples": True},
            {"comparison": "<=", "threshold": 7.0, "min_samples": 0},
            {
                "comparison": "<=",
                "threshold": 7.0,
                "min_samples": 3,
                "max_gap_seconds": 91,
            },
        ):
            with self.subTest(recovery=recovery):
                selected = _selection()
                selected["recovery"] = recovery
                with self.assertRaises(ValueError):
                    bind_original_recovery_rule(_original(), selected)

    def test_original_evidence_or_anomaly_kind_cannot_be_substituted(self) -> None:
        original = deepcopy(_original())
        original["kind"] = "data_quality"
        with self.assertRaises(ValueError):
            bind_original_recovery_rule(original, _selection())
        original = deepcopy(_original())
        original["evidence"]["threshold"] = 9.0
        with self.assertRaises(ValueError):
            bind_original_recovery_rule(original, _selection())


if __name__ == "__main__":
    unittest.main()
