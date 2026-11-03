"""Pure audit-method checks; no database fixtures or model re-fitting."""

import unittest
from datetime import timedelta

from app.modules.iot.forecast_window import build_forecast_window
from experiments.c_forecast.audit_frozen_model import (
    BASE,
    RUNS_PER_SCENARIO,
    SCENARIOS,
    crossings,
    episodes,
    make_run,
)


class AuditMethodTests(unittest.TestCase):
    def test_run_seeds_and_samples_are_new_and_reproducible(self) -> None:
        old_seeds = {
            2026103100 + scenario * 1000 + run
            for scenario in range(3)
            for run in range(25)
        }
        generated = [
            make_run(scenario, run)
            for scenario in SCENARIOS
            for run in range(RUNS_PER_SCENARIO)
        ]
        seeds = {result[0]["seed"] for result in generated}
        ids = {row["observation_id"] for _, _, rows in generated for row in rows}
        self.assertEqual(len(seeds), 50)
        self.assertFalse(seeds & old_seeds)
        self.assertEqual(len(ids), sum(len(rows) for _, _, rows in generated))
        self.assertEqual(make_run("step_warming", 3), make_run("step_warming", 3))

    def test_future_arrival_cannot_change_a_production_input_window(self) -> None:
        from uuid import UUID

        meta, _, rows = make_run("gradual_warming", 0)
        decision = BASE + timedelta(seconds=300)
        options = {
            "device_id": UUID(str(meta["device_id"])),
            "warehouse_id": UUID(str(meta["warehouse_id"])),
            "as_of": decision,
        }
        original = build_forecast_window(rows, **options)
        changed = [dict(row) for row in rows]
        for row in changed:
            if row["received_at"] > decision.isoformat():
                row["temperature_c"] = 999.0
                row["quality"] = ["sensor_error"]
        actual = build_forecast_window(changed, **options)
        self.assertEqual(original.status, actual.status)
        self.assertEqual(original.points, actual.points)

    def test_metric_denominators_do_not_turn_initial_heat_or_brief_spikes_into_new_events(
        self,
    ) -> None:
        self.assertEqual(crossings([8.2] * 20 + [7.0] * 20), [])
        self.assertEqual(crossings([7.0] * 20 + [8.2] * 5 + [7.0] * 20), [])
        self.assertEqual(crossings([7.0] * 20 + [8.2] * 20), [20])
        self.assertEqual(episodes([130, 140, 150, 180]), 2)
        self.assertEqual(episodes([]), 0)


if __name__ == "__main__":
    unittest.main()
