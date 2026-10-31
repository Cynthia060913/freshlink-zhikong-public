"""Synthetic experiment reproducibility and historical availability boundaries."""

import unittest
from dataclasses import replace
from uuid import UUID

from experiments.c_forecast.simulate_evaluate import (
    DECISIONS,
    RUNS_PER_SCENARIO,
    SCENARIOS,
    Observation,
    fit_ridge,
    make_run,
    select_window,
)


class SyntheticForecastExperimentTests(unittest.TestCase):
    def test_generator_repeats_same_run_and_uses_distinct_seeds(self) -> None:
        first = make_run("warming", 15)
        self.assertEqual(first, make_run("warming", 15))
        self.assertNotEqual(first.seed, make_run("warming", 14).seed)
        self.assertNotEqual(first.device_id, make_run("stable", 15).device_id)

    def test_complete_run_split_is_disjoint_and_scenario_balanced(self) -> None:
        runs = [
            make_run(scenario, index)
            for scenario in SCENARIOS
            for index in range(RUNS_PER_SCENARIO)
        ]
        self.assertEqual(len(runs), 60)
        self.assertEqual(len({run.seed for run in runs}), 60)
        self.assertEqual(len({run.run_id for run in runs}), 60)
        for scenario in SCENARIOS:
            group = [run for run in runs if run.scenario == scenario]
            self.assertEqual(
                [
                    sum(run.split == split for run in group)
                    for split in ("train", "validation", "test")
                ],
                [12, 3, 5],
            )

    def test_fresh_confirmation_runs_never_reuse_initial_seeds(self) -> None:
        initial = {
            make_run(scenario, index).seed
            for scenario in SCENARIOS
            for index in range(RUNS_PER_SCENARIO)
        }
        confirmation = [
            make_run(scenario, index)
            for scenario in SCENARIOS
            for index in range(RUNS_PER_SCENARIO, RUNS_PER_SCENARIO + 5)
        ]
        self.assertEqual(len(confirmation), 15)
        self.assertTrue(all(run.split == "confirmation" for run in confirmation))
        self.assertFalse(initial & {run.seed for run in confirmation})

    def test_late_observation_cannot_change_prior_decision(self) -> None:
        run = make_run("stable", 0)
        decision = DECISIONS[5]
        before = select_window(run, decision)
        late = Observation(UUID(int=999), decision - 5, decision + 50, 99.0, "ok")
        after = select_window(
            replace(run, observations=run.observations + (late,)), decision
        )
        self.assertEqual(before, after)

    def test_ridge_parameter_fitting_rejects_empty_training_set(self) -> None:
        with self.assertRaises(ValueError):
            fit_ridge([])


if __name__ == "__main__":
    unittest.main()
