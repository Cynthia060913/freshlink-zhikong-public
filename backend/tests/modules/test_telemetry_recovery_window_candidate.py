"""S0 §3 recovery decision only; no database, B HTTP, or alert closure."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.modules.telemetry.recovery_window_candidate import (
    RecoveryRuleCandidate,
    RecoverySampleCandidate,
    RecoveryWindowCandidate,
    advance_recovery_window,
)

_BASE = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
_DEVICE = UUID("11111111-1111-4111-8111-111111111111")
_WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
_BATCH = UUID("33333333-3333-4333-8333-333333333333")


class RecoveryWindowCandidateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.window = RecoveryWindowCandidate(
            original_source_event_id=UUID("77777777-7777-4777-8777-777777777777"),
            device_id=_DEVICE,
            warehouse_id=_WAREHOUSE,
            batch_id=_BATCH,
            anomaly_sampled_at=_BASE,
        )
        self.rule = RecoveryRuleCandidate(
            comparison="<=", threshold_c=8.0, min_samples=3, max_gap_seconds=90
        )

    def sample(
        self, number: int, *, second: int, **over: object
    ) -> RecoverySampleCandidate:
        base = RecoverySampleCandidate(
            observation_id=UUID(int=number),
            device_id=_DEVICE,
            warehouse_id=_WAREHOUSE,
            batch_id=_BATCH,
            sampled_at=_BASE + timedelta(seconds=second),
            temperature_c=7.5,
            quality=("ok",),
            duplicate="new",
        )
        return replace(base, **over)

    def advance(
        self, *samples: RecoverySampleCandidate
    ) -> tuple[RecoveryWindowCandidate, str]:
        window = self.window
        decision = ""
        for sample in samples:
            window, decision = advance_recovery_window(window, sample, self.rule)
        return window, decision

    def test_three_distinct_good_samples_become_eligible_and_freeze(self) -> None:
        window, decision = self.advance(
            self.sample(1, second=10),
            self.sample(2, second=50),
            self.sample(3, second=140),
        )
        self.assertEqual(decision, "eligible")
        self.assertEqual(
            window.observation_ids, (UUID(int=1), UUID(int=2), UUID(int=3))
        )
        replay, decision = advance_recovery_window(
            window, self.sample(4, second=150), self.rule
        )
        self.assertEqual(decision, "frozen")
        self.assertEqual(replay, window)

    def test_duplicate_does_not_count_or_advance_watermark(self) -> None:
        window, _ = self.advance(self.sample(1, second=10))
        same, decision = advance_recovery_window(
            window, self.sample(1, second=30, duplicate="duplicate"), self.rule
        )
        self.assertEqual(decision, "duplicate")
        self.assertEqual(same, window)

    def test_conflict_and_missing_quality_reset_count(self) -> None:
        window, _ = self.advance(self.sample(1, second=10), self.sample(2, second=20))
        window, decision = advance_recovery_window(
            window, self.sample(2, second=25, duplicate="conflict"), self.rule
        )
        self.assertEqual((decision, window.observation_ids), ("reset", ()))
        self.assertEqual(window.last_sampled_at, _BASE + timedelta(seconds=25))
        late, decision = advance_recovery_window(
            window, self.sample(9, second=24), self.rule
        )
        self.assertEqual((late, decision), (window, "late"))
        window, _ = advance_recovery_window(
            window, self.sample(3, second=30), self.rule
        )
        window, decision = advance_recovery_window(
            window,
            self.sample(4, second=40, quality=("missing_metric:humidity",)),
            self.rule,
        )
        self.assertEqual((decision, window.observation_ids), ("reset", ()))
        self.assertFalse(window.eligible)

    def test_bad_temperature_and_new_anomaly_reset_count(self) -> None:
        window, _ = self.advance(self.sample(1, second=10))
        for index, value in enumerate((None, float("nan"), 10.0), start=2):
            window, decision = advance_recovery_window(
                window,
                self.sample(index, second=index * 10, temperature_c=value),
                self.rule,
            )
            self.assertEqual((decision, window.observation_ids), ("reset", ()))

    def test_gap_over_limit_restarts_from_current_sample(self) -> None:
        window, _ = self.advance(self.sample(1, second=10), self.sample(2, second=20))
        window, decision = advance_recovery_window(
            window, self.sample(3, second=111), self.rule
        )
        self.assertEqual(decision, "progress")
        self.assertEqual(window.observation_ids, (UUID(int=3),))

    def test_late_or_at_original_does_not_close_window(self) -> None:
        window, _ = self.advance(self.sample(1, second=10))
        for second in (0, 5, 10):
            same, decision = advance_recovery_window(
                window, self.sample(2, second=second), self.rule
            )
            self.assertEqual((same, decision), (window, "late"))

    def test_other_device_or_batch_does_not_count(self) -> None:
        for change in (
            {"device_id": UUID(int=9)},
            {"warehouse_id": UUID(int=9)},
            {"batch_id": UUID(int=9)},
        ):
            same, decision = advance_recovery_window(
                self.window, self.sample(1, second=10, **change), self.rule
            )
            self.assertEqual((same, decision), (self.window, "unrelated"))

    def test_explicit_rule_required_and_strict_comparison(self) -> None:
        for rule in (
            {"comparison": ">"},
            {"threshold_c": float("nan")},
            {"threshold_c": True},
            {"min_samples": 0},
            {"min_samples": 2},
            {"min_samples": "3"},
            {"max_gap_seconds": 0},
            {"max_gap_seconds": 91},
            {"max_gap_seconds": "90"},
        ):
            with self.assertRaises(ValueError):
                replace(self.rule, **rule)
        strict = replace(self.rule, comparison="<")
        window, decision = advance_recovery_window(
            self.window, self.sample(1, second=10, temperature_c=8.0), strict
        )
        self.assertEqual((decision, window.observation_ids), ("reset", ()))

    def test_missing_or_naive_time_resets_or_rejects(self) -> None:
        window, _ = self.advance(self.sample(1, second=10))
        reset, decision = advance_recovery_window(
            window, self.sample(2, second=20, sampled_at=None), self.rule
        )
        self.assertEqual((decision, reset.observation_ids), ("reset", ()))
        with self.assertRaises(ValueError):
            advance_recovery_window(
                window,
                self.sample(2, second=20, sampled_at=datetime(2026, 10, 19)),
                self.rule,
            )

    def test_receipt_watermark_prevents_bad_sample_replay_after_restart(self) -> None:
        first = self.sample(1, second=10, received_at=_BASE + timedelta(seconds=10))
        bad = self.sample(
            2,
            second=20,
            sampled_at=None,
            received_at=_BASE + timedelta(seconds=20),
        )
        later = self.sample(3, second=30, received_at=_BASE + timedelta(seconds=30))
        window, decision = self.advance(first, bad, later)
        self.assertEqual(
            (decision, window.observation_ids), ("progress", (UUID(int=3),))
        )
        self.assertEqual(window.last_received_observation_id, UUID(int=3))
        replay, decision = advance_recovery_window(window, bad, self.rule)
        self.assertEqual((decision, replay), ("late", window))
        same, decision = advance_recovery_window(window, later, self.rule)
        self.assertEqual((decision, same), ("duplicate", window))
        with self.assertRaisesRegex(ValueError, "received_at required"):
            advance_recovery_window(window, self.sample(4, second=40), self.rule)

    def test_reused_observation_id_does_not_count_even_if_marked_new(self) -> None:
        window, _ = self.advance(self.sample(1, second=10))
        same, decision = advance_recovery_window(
            window, self.sample(1, second=20), self.rule
        )
        self.assertEqual((same, decision), (window, "duplicate"))


if __name__ == "__main__":
    unittest.main()
