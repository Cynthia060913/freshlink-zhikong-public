"""Predeclared synthetic stress audit; no fitting, tuning, DB or notifications.

Uses the production as-of guard and the already published C model unchanged.
Run --write-plan first, then --plan FILE --output DIR. Existing v1 results and
the old warning no-go decision are never overwritten or reversed by this audit.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from statistics import mean, median
from uuid import NAMESPACE_URL, UUID, uuid5

from app.modules.iot.forecast_window import build_forecast_window
from app.modules.telemetry.forecast_candidate import (
    RidgeParameters,
    last_value_baseline,
    linear_trend_baseline,
    predict_temperature,
)

SCENARIOS = ("stable_far", "stable_near", "gradual_warming", "step_warming", "cooling")
SEED_BASE = 2026110200
RUNS_PER_SCENARIO = 10
DECISIONS = tuple(range(130, 541, 10))
BASE = datetime(2026, 11, 2, tzinfo=UTC)
PARAMETER_SHA256 = "631c342435be05626f4ddafc648984091d9a317a803ffe2ee652d324ab5a7ec8"
METHODS = ("last_value", "linear_trend", "ridge", "ridge_margin_0.1")


def payload(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    )


def plan() -> dict[str, object]:
    return {
        "version": "a-frozen-stress-v1",
        "scope": "synthetic stress audit, not a field experiment or parameter search",
        "parameter_sha256": PARAMETER_SHA256,
        "seed_base": SEED_BASE,
        "seed_formula": "base + scenario_index * 100 + run_index",
        "scenarios": list(SCENARIOS),
        "runs_per_scenario": RUNS_PER_SCENARIO,
        "duration_seconds": 600,
        "sample_interval_seconds": 5,
        "decisions_seconds": list(DECISIONS),
        "history_seconds": 120,
        "target_seconds": 60,
        "noise_sd_c": 0.09,
        "missing_probability": 0.07,
        "bad_quality_probability": 0.04,
        "late_probability": 0.12,
        "late_seconds": [20, 80],
        "threshold_c": 8.0,
        "event_definition": "new upward truth crossing from below 8C, remaining at/above for 10 seconds; initial above-threshold is not a new event",
        "false_episode_definition": "false decisions in a run separated by >10 seconds start a new episode",
        "methods": list(METHODS),
        "warning_margin_c": 0.1,
        "adoption_rule": "diagnostic only: numeric MAE <= linear, detected events >= linear, false episodes <= linear; cannot override prior no-go or imply field benefit",
        "trajectory_ranges": {
            "stable_far": "start U[6.7,7.1], slope U[-0.0003,0.0003] C/s",
            "stable_near": "start U[7.65,7.92], slope U[-0.00015,0.00015], 0.03*sin(2*pi*t/180)",
            "gradual_warming": "start U[6.6,6.9], slope U[0.003,0.005] after 30s",
            "step_warming": "start U[6.9,7.3], slope U[-0.0002,0.0002], rise U[0.7,1.3] over 15s starting U[240,360]",
            "cooling": "start U[8.1,8.6], slope -U[0.003,0.005] after 20s",
        },
    }


def make_run(
    scenario: str, index: int
) -> tuple[dict[str, object], list[float], list[dict[str, object]]]:
    if scenario not in SCENARIOS or not 0 <= index < RUNS_PER_SCENARIO:
        raise ValueError("invalid_audit_run")
    seed = SEED_BASE + SCENARIOS.index(scenario) * 100 + index
    rng = random.Random(seed)
    run_id = f"a-stress-v1-{scenario}-{index:02}"
    device, room = uuid5(NAMESPACE_URL, run_id), uuid5(NAMESPACE_URL, f"{run_id}-room")
    if scenario == "stable_far":
        start, rate = rng.uniform(6.7, 7.1), rng.uniform(-0.0003, 0.0003)
    elif scenario == "stable_near":
        start, rate = rng.uniform(7.65, 7.92), rng.uniform(-0.00015, 0.00015)
    elif scenario == "gradual_warming":
        start, rate = rng.uniform(6.6, 6.9), rng.uniform(0.003, 0.005)
    elif scenario == "step_warming":
        start, rate = rng.uniform(6.9, 7.3), rng.uniform(-0.0002, 0.0002)
    else:
        start, rate = rng.uniform(8.1, 8.6), -rng.uniform(0.003, 0.005)
    step_at, step_size = rng.randrange(240, 361), rng.uniform(0.7, 1.3)
    truth = []
    for second in range(601):
        elapsed = (
            max(0, second - 30)
            if scenario == "gradual_warming"
            else max(0, second - 20)
            if scenario == "cooling"
            else second
        )
        value = start + rate * elapsed
        if scenario == "stable_near":
            value += 0.03 * math.sin(2 * math.pi * second / 180)
        if scenario == "step_warming":
            value += step_size * min(1, max(0, (second - step_at) / 15))
        truth.append(value)
    rows: list[dict[str, object]] = []
    for second in range(0, 601, 5):
        if rng.random() < 0.07:
            continue
        quality = "sensor_error" if rng.random() < 0.04 else "ok"
        value = truth[second] + rng.gauss(0, 0.09) if quality == "ok" else None
        delay = rng.randrange(20, 81) if rng.random() < 0.12 else rng.randrange(0, 3)
        rows.append(
            {
                "observation_id": str(uuid5(NAMESPACE_URL, f"{run_id}-{second}")),
                "device_id": str(device),
                "warehouse_id": str(room),
                "sampled_at": (BASE + timedelta(seconds=second)).isoformat(),
                "received_at": (BASE + timedelta(seconds=second + delay)).isoformat(),
                "temperature_c": value,
                "quality": [quality],
            }
        )
    meta: dict[str, object] = {
        "run_id": run_id,
        "scenario": scenario,
        "seed": seed,
        "device_id": str(device),
        "warehouse_id": str(room),
        "observations": len(rows),
    }
    return meta, truth, rows


def crossings(truth: list[float]) -> list[int]:
    return [
        second
        for second in range(1, len(truth) - 9)
        if truth[second - 1] < 8 <= truth[second]
        and all(value >= 8 for value in truth[second : second + 10])
    ]


def episodes(decisions: list[int]) -> int:
    return (
        0
        if not decisions
        else 1
        + sum(
            right - left > 10
            for left, right in zip(decisions, decisions[1:], strict=False)
        )
    )


def evaluate(
    scenario: str, index: int, parameters: RidgeParameters
) -> tuple[dict[str, object], list[dict[str, object]], list[float]]:
    meta, truth, rows = make_run(scenario, index)
    events = crossings(truth)
    errors: dict[str, list[float]] = {method: [] for method in METHODS}
    detected: dict[str, dict[int, int]] = {method: {} for method in METHODS}
    false: dict[str, list[int]] = {method: [] for method in METHODS}
    statuses: Counter[str] = Counter()
    decisions_below = 0
    for decision in DECISIONS:
        window = build_forecast_window(
            rows,
            device_id=UUID(str(meta["device_id"])),
            warehouse_id=UUID(str(meta["warehouse_id"])),
            as_of=BASE + timedelta(seconds=decision),
        )
        statuses[window.status] += 1
        if window.status != "ready":
            continue
        assert all(
            point.received_at <= window.as_of and point.sampled_at <= window.as_of
            for point in window.points
        )
        prediction = predict_temperature(window, parameters).predicted_temperature_c
        if prediction is None:
            raise AssertionError("ready_without_prediction")
        estimates = {
            "last_value": last_value_baseline(window.points),
            "linear_trend": linear_trend_baseline(window.points, window.target_at),
            "ridge": prediction,
            "ridge_margin_0.1": prediction,
        }
        if window.points[-1].temperature_c < 8:
            decisions_below += 1
        for method, estimate in estimates.items():
            errors[method].append(abs(estimate - truth[decision + 60]))
            margin = 0.1 if method == "ridge_margin_0.1" else 0
            if window.points[-1].temperature_c >= 8 or estimate < 8 + margin:
                continue
            upcoming = [event for event in events if decision < event <= decision + 60]
            if not upcoming:
                false[method].append(decision)
            for event in upcoming:
                detected[method][event] = max(
                    detected[method].get(event, 0), event - decision
                )
    if sum(statuses.values()) != len(DECISIONS):
        raise AssertionError("incomplete_decision_denominator")
    return (
        {
            **meta,
            "new_exceedance_events": events,
            "statuses": dict(sorted(statuses.items())),
            "ready_below_threshold": decisions_below,
            "methods": {
                method: {
                    "absolute_errors_c": errors[method],
                    "detected_leads_seconds": list(detected[method].values()),
                    "detected_events": len(detected[method]),
                    "false_decisions": len(false[method]),
                    "false_episodes": episodes(false[method]),
                }
                for method in METHODS
            },
        },
        rows,
        truth,
    )


def aggregate(runs: list[dict[str, object]], method: str) -> dict[str, object]:
    methods = [run["methods"][method] for run in runs]  # type: ignore[index]
    errors = [error for item in methods for error in item["absolute_errors_c"]]
    leads = [lead for item in methods for lead in item["detected_leads_seconds"]]
    return {
        "mae_c": mean(errors) if errors else None,
        "rmse_c": math.sqrt(mean(value * value for value in errors))
        if errors
        else None,
        "error_denominator": len(errors),
        "per_run_mae_c": [
            mean(item["absolute_errors_c"]) if item["absolute_errors_c"] else None
            for item in methods
        ],
        "detected_events": sum(item["detected_events"] for item in methods),
        "positive_events": sum(len(run["new_exceedance_events"]) for run in runs),  # type: ignore[arg-type]
        "false_decisions": sum(item["false_decisions"] for item in methods),
        "false_episodes": sum(item["false_episodes"] for item in methods),
        "false_decision_denominator": sum(run["ready_below_threshold"] for run in runs),
        "lead_seconds_median": median(leads) if leads else None,
        "lead_denominator": len(leads),
    }


def audit(plan_file: Path, output: Path) -> None:
    declared = json.loads(plan_file.read_text(encoding="utf-8"))
    if declared != plan():
        raise ValueError("plan_mismatch_no_silent_changes")
    # A Windows checkout may use CRLF. Hash the declared JSON in canonical
    # UTF-8/LF form so the same plan has the same identity across platforms.
    canonical_plan = payload(declared).encode("utf-8")
    parameters_file = Path(__file__).parent / "artifacts/v1/parameters.json"
    parameters = RidgeParameters.from_json(
        json.loads(parameters_file.read_text(encoding="utf-8"))
    )
    if parameters.sha256() != PARAMETER_SHA256:
        raise ValueError("frozen_parameter_mismatch")
    output.mkdir(parents=True, exist_ok=False)
    runs, samples = [], []
    raw_path, truth_path = output / "observations.jsonl", output / "ground_truth.jsonl"
    with (
        raw_path.open("w", encoding="utf-8", newline="\n") as raw,
        truth_path.open("w", encoding="utf-8", newline="\n") as truth_file,
    ):
        for scenario in SCENARIOS:
            for index in range(RUNS_PER_SCENARIO):
                result, rows, truth = evaluate(scenario, index, parameters)
                runs.append(result)
                samples.extend({"run_id": result["run_id"], **row} for row in rows[:2])
                for row in rows:
                    raw.write(
                        json.dumps(
                            {"run_id": result["run_id"], **row},
                            sort_keys=True,
                            allow_nan=False,
                        )
                        + "\n"
                    )
                truth_file.write(
                    json.dumps(
                        {"run_id": result["run_id"], "temperature_c_per_second": truth},
                        sort_keys=True,
                    )
                    + "\n"
                )
    statuses = sum((Counter(run["statuses"]) for run in runs), Counter())
    overall = {method: aggregate(runs, method) for method in METHODS}
    linear, selected = overall["linear_trend"], overall["ridge_margin_0.1"]
    diagnostic_gate = (
        selected["mae_c"] <= linear["mae_c"]
        and selected["detected_events"] >= linear["detected_events"]
        and selected["false_episodes"] <= linear["false_episodes"]
    )
    report = {
        "plan_sha256": sha256(canonical_plan).hexdigest(),
        "model_parameter_sha256": parameters.sha256(),
        "scope": "fixed-model synthetic stress audit; no fitting, no field benefits, no runtime alert policy changes",
        "runs": len(runs),
        "decisions": len(runs) * len(DECISIONS),
        "statuses": dict(sorted(statuses.items())),
        "ready_fraction": statuses["ready"] / (len(runs) * len(DECISIONS)),
        "overall": overall,
        "by_scenario": {
            scenario: {
                method: aggregate(
                    [run for run in runs if run["scenario"] == scenario], method
                )
                for method in METHODS
            }
            for scenario in SCENARIOS
        },
        "additional_synthetic_gate_passed": diagnostic_gate,
        "business_warning_accepted": False,
        "prior_confirmation_no_go_unchanged": True,
        "files": {
            name: {
                "bytes": (output / name).stat().st_size,
                "sha256": sha256((output / name).read_bytes()).hexdigest(),
            }
            for name in (raw_path.name, truth_path.name)
        },
        "run_results": runs,
    }
    (output / "evaluation.json").write_text(
        payload(report), encoding="utf-8", newline="\n"
    )
    (output / "manifest.json").write_text(
        payload(
            {
                "plan": declared,
                "plan_sha256": report["plan_sha256"],
                "parameter_sha256": parameters.sha256(),
                "run_ids": [run["run_id"] for run in runs],
                "seeds": [run["seed"] for run in runs],
                "files": report["files"],
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    (output / "sample_observations.json").write_text(
        payload(samples), encoding="utf-8", newline="\n"
    )
    sys.stdout.write(
        payload(
            {
                key: report[key]
                for key in (
                    "runs",
                    "decisions",
                    "statuses",
                    "overall",
                    "additional_synthetic_gate_passed",
                    "business_warning_accepted",
                )
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-plan", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.write_plan and not args.plan and not args.output:
        args.write_plan.parent.mkdir(parents=True, exist_ok=True)
        with args.write_plan.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload(plan()))
    elif args.plan and args.output and not args.write_plan:
        audit(args.plan, args.output)
    else:
        parser.error("use --write-plan FILE or --plan FILE --output NEW_DIRECTORY")


if __name__ == "__main__":
    main()
