"""Deterministic synthetic forecast experiment; never uses live cold-room data.

Run from backend: python -m experiments.c_forecast.simulate_evaluate --output DIR
Only the training split fits preprocessing and ridge coefficients. Validation
and test splits contain complete, disjoint runs and seeds.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from math import isfinite
from pathlib import Path
from statistics import mean, median
from uuid import UUID, uuid5

from app.modules.telemetry.forecast_candidate import (
    FEATURE_NAMES,
    RidgeParameters,
    features,
    last_value_baseline,
    linear_trend_baseline,
    predict_temperature,
)

BASE = datetime(2026, 10, 31, tzinfo=UTC)
HISTORY = 120
HORIZON = 60
THRESHOLD_C = 8.0
RUNS_PER_SCENARIO = 20
CONFIRMATION_RUNS_PER_SCENARIO = 5
SCENARIOS = ("stable", "warming", "cooling")
DECISIONS = tuple(range(130, 541, 10))
SEED_BASE = 2026103100


@dataclass(frozen=True)
class Observation:
    observation_id: UUID
    sampled_second: int
    received_second: int
    temperature_c: float | None
    quality: str


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


@dataclass(frozen=True)
class Run:
    run_id: str
    scenario: str
    seed: int
    split: str
    device_id: UUID
    warehouse_id: UUID
    true_c: tuple[float, ...]
    observations: tuple[Observation, ...]


def _time(second: int) -> datetime:
    return BASE + timedelta(seconds=second)


def make_run(scenario: str, index: int) -> Run:
    if (
        scenario not in SCENARIOS
        or not 0 <= index < RUNS_PER_SCENARIO + CONFIRMATION_RUNS_PER_SCENARIO
    ):
        raise ValueError("invalid_run")
    scenario_number = SCENARIOS.index(scenario)
    seed = SEED_BASE + scenario_number * 1000 + index
    rng = random.Random(seed)
    run_id = f"{scenario}-{index:02d}"
    split = (
        "train"
        if index < 12
        else "validation"
        if index < 15
        else "test"
        if index < RUNS_PER_SCENARIO
        else "confirmation"
    )
    device = uuid5(UUID(int=0), f"synthetic-device-{run_id}")
    room = uuid5(UUID(int=0), f"synthetic-room-{scenario}-{index % 2}")
    start = (
        rng.uniform(6.35, 6.8)
        if scenario == "warming"
        else (
            rng.uniform(7.0, 7.45) if scenario == "stable" else rng.uniform(8.25, 8.6)
        )
    )
    rate = (
        rng.uniform(0.0045, 0.0065)
        if scenario == "warming"
        else rng.uniform(-0.00045, 0.00045)
        if scenario == "stable"
        else rng.uniform(-0.0058, -0.0038)
    )
    change_time = rng.randrange(250, 380, 5)
    impulse = rng.uniform(-0.25, 0.55)  # Unpredictable at an earlier decision.
    trajectory = []
    for second in range(0, 601):
        warming_elapsed = max(0, second - 45) if scenario == "warming" else second
        value = start + rate * warming_elapsed
        if second >= change_time:
            value += impulse
        trajectory.append(value)
    rows = []
    for second in range(0, 601, 5):
        if rng.random() < 0.07:  # Missing sample; do not impute it.
            continue
        quality = "sensor_error" if rng.random() < 0.04 else "ok"
        temperature = (
            trajectory[second] + rng.gauss(0, 0.09) if quality == "ok" else None
        )
        delay = rng.randrange(20, 81) if rng.random() < 0.12 else rng.randrange(0, 3)
        rows.append(
            Observation(
                uuid5(UUID(int=0), f"synthetic-observation-{run_id}-{second}"),
                second,
                second + delay,
                temperature,
                quality,
            )
        )
    return Run(
        run_id, scenario, seed, split, device, room, tuple(trajectory), tuple(rows)
    )


def select_window(run: Run, decision: int) -> Window:
    """Experiment-only mirror of PR103 gates; final integration uses A's guard."""
    available = [
        row
        for row in run.observations
        if row.received_second <= decision
        and decision - HISTORY <= row.sampled_second <= decision
    ]
    clean = [
        row
        for row in available
        if row.quality == "ok" and row.temperature_c is not None
    ]
    clean.sort(
        key=lambda row: (
            row.sampled_second,
            row.received_second,
            row.observation_id.int,
        )
    )
    last_bad = max(
        (row.sampled_second for row in available if row.quality != "ok"), default=-1
    )
    last_good = clean[-1].sampled_second if clean else -1
    if last_bad >= last_good:
        status = "quality_blocked"
    elif len(clean) < 8 or clean[-1].sampled_second - clean[0].sampled_second < 10:
        status = "insufficient_data"
    elif decision - clean[-1].sampled_second > 15:
        status = "stale_data"
    elif any(
        b.sampled_second - a.sampled_second > 10
        for a, b in zip(clean, clean[1:], strict=False)
    ):
        status = "gapped_data"
    else:
        status = "ready"
    points = (
        tuple(
            Point(
                row.observation_id,
                _time(row.sampled_second),
                _time(row.received_second),
                float(row.temperature_c),
            )
            for row in clean
        )
        if status == "ready"
        else ()
    )
    return Window(
        status,
        run.device_id,
        run.warehouse_id,
        _time(decision),
        _time(decision + HORIZON),
        points,
    )


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [matrix[index][:] + [vector[index]] for index in range(size)]
    for column in range(size):
        pivot = max(
            range(column, size), key=lambda index: abs(augmented[index][column])
        )
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("singular_training_matrix")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                a - factor * b
                for a, b in zip(augmented[row], augmented[column], strict=True)
            ]
    return [row[-1] for row in augmented]


def fit_ridge(
    examples: list[tuple[tuple[float, ...], float]], *, penalty: float = 1.0
) -> RidgeParameters:
    if not examples or penalty <= 0:
        raise ValueError("invalid_training_set")
    count = len(FEATURE_NAMES)
    means = tuple(mean(vector[j] for vector, _ in examples) for j in range(count))
    scales = tuple(
        max((mean((vector[j] - means[j]) ** 2 for vector, _ in examples)) ** 0.5, 1e-9)
        for j in range(count)
    )
    target_mean = mean(target for _, target in examples)
    matrix = [[0.0] * count for _ in range(count)]
    rhs = [0.0] * count
    for vector, target in examples:
        scaled = [(vector[j] - means[j]) / scales[j] for j in range(count)]
        for j in range(count):
            rhs[j] += scaled[j] * (target - target_mean)
            for k in range(count):
                matrix[j][k] += scaled[j] * scaled[k]
    for j in range(count):
        matrix[j][j] += penalty
    coefficients = tuple(_solve(matrix, rhs))
    return RidgeParameters(
        "c-ridge-synthetic-v1",
        HISTORY,
        HORIZON,
        means,
        scales,
        target_mean,
        coefficients,
    )


def _first_crossing(run: Run) -> int | None:
    return next(
        (
            second
            for second in range(1, len(run.true_c))
            if run.true_c[second - 1] < THRESHOLD_C <= run.true_c[second]
        ),
        None,
    )


def evaluate(runs: list[Run], parameters: RidgeParameters) -> dict[str, object]:
    test_runs = [run for run in runs if run.split == "test"]
    methods = ("last_value", "linear_trend", "ridge")
    errors: dict[str, list[float]] = {method: [] for method in methods}
    scenario_errors: dict[str, dict[str, list[float]]] = {
        scenario: {method: [] for method in methods} for scenario in SCENARIOS
    }
    refusals: Counter[str] = Counter()
    alerts: dict[str, dict[str, list[int]]] = {
        method: defaultdict(list) for method in methods
    }
    false_alarms: Counter[str] = Counter()
    scenario_false_alarms: dict[str, Counter[str]] = {
        scenario: Counter() for scenario in SCENARIOS
    }
    at_risk_decisions = 0
    scenario_at_risk: Counter[str] = Counter()
    ready = 0
    for run in test_runs:
        for decision in DECISIONS:
            window = select_window(run, decision)
            if window.status != "ready":
                refusals[window.status] += 1
                continue
            ready += 1
            target = run.true_c[decision + HORIZON]
            predictions = {
                "last_value": last_value_baseline(window.points),
                "linear_trend": linear_trend_baseline(window.points, window.target_at),
                "ridge": predict_temperature(
                    window, parameters
                ).predicted_temperature_c,
            }
            if predictions["ridge"] is None:
                raise AssertionError("ready_window_was_not_predicted")
            latest = window.points[-1].temperature_c
            crossing = _first_crossing(run)
            if latest < THRESHOLD_C:
                at_risk_decisions += 1
                scenario_at_risk[run.scenario] += 1
            for method, prediction in predictions.items():
                if prediction is None or not isfinite(prediction):
                    raise AssertionError("nonfinite_prediction")
                error = abs(prediction - target)
                errors[method].append(error)
                scenario_errors[run.scenario][method].append(error)
                if latest < THRESHOLD_C and prediction >= THRESHOLD_C:
                    if (
                        crossing is not None
                        and decision < crossing <= decision + HORIZON
                    ):
                        alerts[method][run.run_id].append(crossing - decision)
                    else:
                        false_alarms[method] += 1
                        scenario_false_alarms[run.scenario][method] += 1
    total_decisions = len(test_runs) * len(DECISIONS)
    events = {run.run_id: _first_crossing(run) for run in test_runs}
    positive_runs = {key for key, crossing in events.items() if crossing is not None}

    def metrics(method: str) -> dict[str, object]:
        detected = len(positive_runs & alerts[method].keys())
        leads = [
            max(alerts[method][run_id])
            for run_id in positive_runs
            if alerts[method].get(run_id)
        ]
        return {
            "mae_c": mean(errors[method]) if errors[method] else None,
            "mae_denominator_ready_windows": len(errors[method]),
            "event_recall": detected / len(positive_runs) if positive_runs else None,
            "detected_events": detected,
            "positive_events": len(positive_runs),
            "missed_events": len(positive_runs) - detected,
            "lead_seconds_correct_events_median": median(leads) if leads else None,
            "lead_seconds_correct_events_count": len(leads),
            "false_alarm_decisions": false_alarms[method],
            "false_alarm_denominator_ready_below_threshold": at_risk_decisions,
            "false_alarm_rate": false_alarms[method] / at_risk_decisions
            if at_risk_decisions
            else None,
            "by_scenario": {
                scenario: {
                    "mae_c": (
                        mean(scenario_errors[scenario][method])
                        if scenario_errors[scenario][method]
                        else None
                    ),
                    "ready_windows": len(scenario_errors[scenario][method]),
                    "positive_events": sum(
                        run.run_id in positive_runs
                        for run in test_runs
                        if run.scenario == scenario
                    ),
                    "false_alarm_decisions": scenario_false_alarms[scenario][method],
                    "false_alarm_denominator": scenario_at_risk[scenario],
                }
                for scenario in SCENARIOS
            },
        }

    return {
        "split": "test_only",
        "test_run_count": len(test_runs),
        "decision_count": total_decisions,
        "ready_count": ready,
        "coverage": ready / total_decisions,
        "refused_count": total_decisions - ready,
        "refusal_reasons": dict(sorted(refusals.items())),
        "threshold_c": THRESHOLD_C,
        "methods": {method: metrics(method) for method in methods},
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output: Path = args.output
    output.mkdir(parents=True, exist_ok=True)
    runs = [
        make_run(scenario, index)
        for scenario in SCENARIOS
        for index in range(RUNS_PER_SCENARIO)
    ]
    examples = [
        (features(window.points, window.target_at), run.true_c[decision + HORIZON])
        for run in runs
        if run.split == "train"
        for decision in DECISIONS
        if (window := select_window(run, decision)).status == "ready"
    ]
    parameters = fit_ridge(examples)
    # A single fixed initial ridge penalty. Validation is reported, not used to
    # retune; the held-out test is evaluated once after parameters are frozen.
    validation = [
        abs(
            predict_temperature(window, parameters).predicted_temperature_c
            - run.true_c[decision + HORIZON]
        )
        for run in runs
        if run.split == "validation"
        for decision in DECISIONS
        if (window := select_window(run, decision)).status == "ready"
    ]
    report = evaluate(runs, parameters)
    report["validation_ridge_mae_c"] = mean(validation) if validation else None
    report["validation_ready_windows"] = len(validation)
    report["train_ready_windows"] = len(examples)
    report["ridge_penalty"] = 1.0
    report["parameter_sha256"] = parameters.sha256()
    report["input_source"] = (
        "deterministic synthetic runs, not real cold-room measurements"
    )
    report["split_rule"] = (
        "per scenario: indices 00-11 train, 12-14 validation, 15-19 test"
    )
    _write_json(output / "parameters.json", parameters.to_json())
    _write_json(output / "evaluation.json", report)
    manifest = {
        "seed_base": SEED_BASE,
        "scenarios": list(SCENARIOS),
        "runs_per_scenario": RUNS_PER_SCENARIO,
        "total_runs": len(runs),
        "sample_step_seconds": 5,
        "run_seconds": 600,
        "decision_seconds": list(DECISIONS),
        "history_seconds": HISTORY,
        "horizon_seconds": HORIZON,
        "threshold_c": THRESHOLD_C,
        "observations_total": sum(len(run.observations) for run in runs),
        "run_ids": {
            split: [run.run_id for run in runs if run.split == split]
            for split in ("train", "validation", "test")
        },
        "generation": "5 s virtual sampling; scenario-specific drift; 0.09 C Gaussian sensor noise; 7% missing; 4% sensor_error; 12% 20-80 s arrival delay; one hidden -0.25..+0.55 C impulse per run",
    }
    _write_json(output / "manifest.json", manifest)
    sample_rows = []
    for run in runs:
        for row in run.observations:
            sample_rows.append(
                {
                    "run_id": run.run_id,
                    "split": run.split,
                    "seed": run.seed,
                    "scenario": run.scenario,
                    "device_id": str(run.device_id),
                    "warehouse_id": str(run.warehouse_id),
                    "observation_id": str(row.observation_id),
                    "sampled_at": _time(row.sampled_second).isoformat(),
                    "received_at": _time(row.received_second).isoformat(),
                    "temperature_c": row.temperature_c,
                    "quality": [row.quality],
                }
            )
    data_path = output / "synthetic_observations.jsonl"
    data_path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
            for row in sample_rows
        ),
        encoding="utf-8",
    )
    manifest["synthetic_observations_sha256"] = sha256(
        data_path.read_bytes()
    ).hexdigest()
    sample = [
        item
        for scenario in SCENARIOS
        for item in [row for row in sample_rows if row["run_id"] == f"{scenario}-15"][
            :20
        ]
    ]
    sample_path = output / "sample_observations.jsonl"
    sample_path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in sample
        ),
        encoding="utf-8",
    )
    manifest["committed_sample_rows"] = len(sample)
    manifest["sample_observations_sha256"] = sha256(
        sample_path.read_bytes()
    ).hexdigest()
    example_run = next(run for run in runs if run.run_id == "stable-15")
    example_decision = next(
        decision
        for decision in DECISIONS
        if select_window(example_run, decision).status == "ready"
    )
    example_window = select_window(example_run, example_decision)
    example_result = predict_temperature(example_window, parameters)
    example = {
        "input": {
            "status": example_window.status,
            "device_id": str(example_window.device_id),
            "warehouse_id": str(example_window.warehouse_id),
            "as_of": example_window.as_of.isoformat(),
            "target_at": example_window.target_at.isoformat(),
            "points": [
                {
                    "observation_id": str(point.observation_id),
                    "sampled_at": point.sampled_at.isoformat(),
                    "received_at": point.received_at.isoformat(),
                    "temperature_c": point.temperature_c,
                }
                for point in example_window.points
            ],
        },
        "parameter_file": "parameters.json",
        "output": example_result.to_json(),
        "held_out_actual_target_c": example_run.true_c[example_decision + HORIZON],
        "note": "The held-out label is for evaluation only, never a model input.",
    }
    example_path = output / "example_prediction.json"
    _write_json(example_path, example)
    manifest["example_prediction_sha256"] = sha256(
        example_path.read_bytes()
    ).hexdigest()
    _write_json(output / "manifest.json", manifest)
    sys.stdout.write(
        json.dumps(
            {
                "runs": len(runs),
                "train_ready": len(examples),
                "validation_ready": len(validation),
                "test_ready": report["ready_count"],
                "test_coverage": report["coverage"],
                "ridge_mae_c": report["methods"]["ridge"]["mae_c"],
            },
            sort_keys=True,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
