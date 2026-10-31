"""C pure forecast contract: as-of, unavailable, parameter and traceability gates."""

import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.modules.telemetry.forecast_candidate import (
    FEATURE_NAMES,
    RidgeParameters,
    predict_temperature,
)

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


@dataclass(frozen=True)
class Point:
    observation_id: UUID
    sampled_at: datetime
    received_at: datetime
    temperature_c: float


@dataclass(frozen=True)
class Window:
    status: str
    device_id: UUID
    warehouse_id: UUID
    as_of: datetime
    target_at: datetime
    points: tuple[Point, ...]


def sample() -> Window:
    return Window(
        "ready",
        UUID(int=1),
        UUID(int=2),
        NOW,
        NOW + timedelta(seconds=60),
        tuple(
            Point(
                UUID(int=100 + index),
                NOW - timedelta(seconds=35 - index * 5),
                NOW - timedelta(seconds=35 - index * 5),
                7.0 + index * 0.1,
            )
            for index in range(8)
        ),
    )


def params() -> RidgeParameters:
    return RidgeParameters.from_json(
        {
            "schema_version": "c-ridge-v1",
            "model_version": "contract-example-only",
            "history_seconds": 120,
            "horizon_seconds": 60,
            "feature_names": list(FEATURE_NAMES),
            "feature_mean": [0.0, 0.0, 0.0, 0.0],
            "feature_scale": [1.0, 1.0, 1.0, 1.0],
            "intercept_c": 0.0,
            "coefficients": [1.0, 0.0, 0.0, 0.0],
        }
    )


class PureForecastTests(unittest.TestCase):
    def test_ready_contract_and_traceability(self) -> None:
        result = predict_temperature(sample(), params())
        self.assertEqual(result.status, "predicted")
        self.assertAlmostEqual(result.predicted_temperature_c, 7.7)
        self.assertEqual(result.target_at, NOW + timedelta(seconds=60))
        self.assertEqual(len(result.input_observation_ids), 8)
        self.assertEqual(result.input_last_sampled_at, NOW)
        self.assertEqual(len(result.parameter_sha256), 64)
        self.assertEqual(result.to_json()["device_id"], str(UUID(int=1)))

    def test_unavailable_does_not_return_normal_or_temperature(self) -> None:
        for state in (
            "stale_data",
            "gapped_data",
            "quality_blocked",
            "insufficient_data",
        ):
            with self.subTest(state=state):
                original = sample()
                window = Window(
                    state,
                    original.device_id,
                    original.warehouse_id,
                    original.as_of,
                    original.target_at,
                    (),
                )
                result = predict_temperature(window, params())
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.reason, state)
                self.assertIsNone(result.predicted_temperature_c)
                self.assertEqual(result.input_observation_ids, ())

    def test_future_receipt_is_rejected_even_when_sample_is_old(self) -> None:
        original = sample()
        points = list(original.points)
        points[-1] = Point(
            points[-1].observation_id,
            points[-1].sampled_at,
            NOW + timedelta(seconds=1),
            points[-1].temperature_c,
        )
        with self.assertRaisesRegex(ValueError, "future_forecast_input"):
            predict_temperature(
                Window(
                    original.status,
                    original.device_id,
                    original.warehouse_id,
                    original.as_of,
                    original.target_at,
                    tuple(points),
                ),
                params(),
            )

    def test_target_and_parameter_schema_rejected(self) -> None:
        original = sample()
        with self.assertRaisesRegex(ValueError, "forecast_target_mismatch"):
            predict_temperature(
                Window(
                    original.status,
                    original.device_id,
                    original.warehouse_id,
                    original.as_of,
                    NOW + timedelta(seconds=30),
                    original.points,
                ),
                params(),
            )
        bad = params().to_json()
        bad["feature_names"] = ["wrong"] * len(FEATURE_NAMES)
        with self.assertRaisesRegex(ValueError, "feature_names_mismatch"):
            RidgeParameters.from_json(bad)
        bad = params().to_json()
        bad["coefficients"] = [float("nan")] * len(FEATURE_NAMES)
        with self.assertRaisesRegex(ValueError, "invalid_coefficients"):
            RidgeParameters.from_json(bad)

    def test_huge_numeric_input_is_rejected_as_invalid(self) -> None:
        huge = 10**1000
        for field, replacement, reason in (
            ("feature_mean", [huge, 0, 0, 0], "invalid_feature_mean"),
            ("feature_scale", [huge, 1, 1, 1], "invalid_feature_scale"),
            ("coefficients", [huge, 0, 0, 0], "invalid_coefficients"),
            ("intercept_c", huge, "invalid_intercept"),
        ):
            with self.subTest(field=field):
                bad = params().to_json()
                bad[field] = replacement
                with self.assertRaisesRegex(ValueError, reason):
                    RidgeParameters.from_json(bad)

        original = sample()
        bad_point = Point(
            original.points[-1].observation_id,
            original.points[-1].sampled_at,
            original.points[-1].received_at,
            huge,
        )
        with self.assertRaisesRegex(ValueError, "invalid_forecast_temperature"):
            predict_temperature(
                Window(
                    original.status,
                    original.device_id,
                    original.warehouse_id,
                    original.as_of,
                    original.target_at,
                    original.points[:-1] + (bad_point,),
                ),
                params(),
            )


if __name__ == "__main__":
    unittest.main()
