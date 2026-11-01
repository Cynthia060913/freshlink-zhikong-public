"""Forecast availability boundaries; no database, model, or provider involved."""

import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.modules.iot.forecast_window import (
    ForecastWindow,
    ForecastWindowPolicy,
    build_forecast_window,
)

DEVICE = UUID(int=1)
ROOM = UUID(int=2)
NOW = datetime(2026, 10, 31, 0, 5, tzinfo=UTC)


def row(index: int, *, age: float | None = None) -> dict[str, object]:
    sampled = NOW - timedelta(seconds=age if age is not None else 16 - index * 2)
    return {
        "observation_id": str(UUID(int=100 + index)),
        "device_id": str(DEVICE),
        "warehouse_id": str(ROOM),
        "sampled_at": sampled.isoformat(),
        "received_at": sampled.isoformat(),
        "temperature_c": 7.0 + index / 10,
        "quality": ["ok"],
    }


def window(
    rows: list[dict[str, object]],
    *,
    policy: ForecastWindowPolicy = ForecastWindowPolicy(),
) -> ForecastWindow:
    return build_forecast_window(
        rows, device_id=DEVICE, warehouse_id=ROOM, as_of=NOW, policy=policy
    )


class ForecastWindowTests(unittest.TestCase):
    def test_ready_is_ordered_and_predicts_from_decision_time(self) -> None:
        result = window([row(i) for i in reversed(range(8))])
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.target_at, NOW + timedelta(seconds=60))
        self.assertEqual(result.latest_sample_age_seconds, 2)
        self.assertEqual(
            [p.observation_id.int for p in result.points], list(range(100, 108))
        )

    def test_future_arrival_cannot_change_points_or_quality_status(self) -> None:
        rows = [row(i) for i in range(8)]
        hidden = row(30, age=1)
        hidden.update(
            received_at=(NOW + timedelta(seconds=1)).isoformat(),
            quality=["bad"],
            temperature_c=9999,
        )
        before, after = window(rows), window(rows + [hidden])
        self.assertEqual(after.status, before.status)
        self.assertEqual(after.points, before.points)
        self.assertEqual(dict(after.excluded)["not_available_as_of"], 1)

    def test_future_sample_is_not_current_input(self) -> None:
        hidden = row(30, age=-1)
        hidden["received_at"] = NOW.isoformat()
        result = window([row(i) for i in range(8)] + [hidden])
        self.assertEqual(result.eligible_count, 8)
        self.assertEqual(result.status, "ready")

    def test_unavailable_arrival_is_filtered_before_its_sample_fields(self) -> None:
        hidden = row(30, age=1)
        hidden.update(
            received_at=(NOW + timedelta(seconds=1)).isoformat(), sampled_at="bad"
        )
        result = window([row(i) for i in range(8)] + [hidden])
        self.assertEqual(result.status, "ready")
        self.assertNotIn("invalid", dict(result.excluded))

    def test_other_device_and_room_cannot_enter_window(self) -> None:
        other_device, other_room = row(20, age=1), row(21, age=1)
        other_device["device_id"], other_room["warehouse_id"] = (
            str(UUID(int=3)),
            str(UUID(int=4)),
        )
        result = window([row(i) for i in range(8)] + [other_device, other_room])
        self.assertEqual(result.eligible_count, 8)
        self.assertEqual(dict(result.excluded)["other_scope"], 2)

    def test_late_old_sampling_is_stale_even_when_just_received(self) -> None:
        rows = [row(i, age=80 + i * 2) for i in range(8)]
        for item in rows:
            item["received_at"] = NOW.isoformat()
        result = window(rows)
        self.assertEqual(result.status, "stale_data")
        self.assertEqual(result.points, ())

    def test_old_outside_window_does_not_change_current_quality(self) -> None:
        old = row(30, age=121)
        old["quality"] = ["bad"]
        result = window([row(i) for i in range(8)] + [old])
        self.assertEqual(result.status, "ready")
        self.assertEqual(dict(result.excluded)["outside_window"], 1)

    def test_latest_bad_quality_blocks_older_clean_data(self) -> None:
        bad = row(20, age=1)
        bad.update(quality=["out_of_range"], temperature_c=None)
        result = window([row(i) for i in range(8)] + [bad])
        self.assertEqual(result.status, "quality_blocked")
        self.assertEqual(result.points, ())

    def test_nonfinite_or_invalid_timestamp_fails_closed(self) -> None:
        for replacement in [
            {"temperature_c": float("nan")},
            {"temperature_c": True},
            {"sampled_at": "bad"},
        ]:
            with self.subTest(replacement=list(replacement)):
                bad = row(20, age=1)
                bad.update(replacement)
                self.assertEqual(
                    window([row(i) for i in range(8)] + [bad]).status, "invalid_input"
                )

    def test_duplicate_is_idempotent_but_conflicting_id_blocks(self) -> None:
        rows = [row(i) for i in range(8)]
        self.assertEqual(window(rows + [dict(rows[-1])]).eligible_count, 8)
        conflict = dict(rows[-1])
        conflict["temperature_c"] = 100.0
        self.assertEqual(window(rows + [conflict]).status, "invalid_input")

    def test_count_span_and_gaps_are_separate_gates(self) -> None:
        self.assertEqual(window([row(1)]).status, "insufficient_data")
        self.assertEqual(
            window([row(i, age=i / 10) for i in range(8)]).status, "insufficient_data"
        )
        self.assertEqual(
            window([row(i, age=i * 11) for i in range(8)]).status, "gapped_data"
        )

    def test_history_is_bounded_without_using_future_rows(self) -> None:
        policy = ForecastWindowPolicy(max_points=8)
        result = window([row(i, age=30 - i * 2) for i in range(15)], policy=policy)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.eligible_count, 8)
        self.assertEqual(dict(result.excluded)["bounded_history"], 7)

    def test_naive_decision_and_invalid_policy_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_forecast_window(
                [], device_id=DEVICE, warehouse_id=ROOM, as_of=NOW.replace(tzinfo=None)
            )
        for kwargs in [{"min_points": True}, {"max_points": 7}, {"history_seconds": 2}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ForecastWindowPolicy(**kwargs)
