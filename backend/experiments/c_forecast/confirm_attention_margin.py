"""One predeclared attention-margin adjustment with fresh confirmation runs.

The first 60 runs and their reported test results remain unchanged. Selection
uses only validation runs 12-14 of each scenario; confirmation indices 20-24
are generated only after selection. This is synthetic research, not a rule or
notification implementation.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, median

from app.modules.telemetry.forecast_candidate import (
    RidgeParameters,
    last_value_baseline,
    linear_trend_baseline,
    predict_temperature,
)
from experiments.c_forecast.simulate_evaluate import (
    CONFIRMATION_RUNS_PER_SCENARIO,
    DECISIONS,
    HORIZON,
    RUNS_PER_SCENARIO,
    SCENARIOS,
    THRESHOLD_C,
    Run,
    _first_crossing,
    make_run,
    select_window,
)

MARGINS_C = (0.0, 0.1, 0.2, 0.3)


def score(
    runs: list[Run], parameters: RidgeParameters, *, method: str, margin_c: float = 0.0
) -> dict[str, object]:
    if method not in ("last_value", "linear_trend", "ridge"):
        raise ValueError("unknown_forecast_method")
    alerts: dict[str, list[int]] = {}
    false_alarms = 0
    false_alarm_decisions_by_run: dict[str, list[int]] = {}
    below_threshold = 0
    ready = 0
    errors = []
    refusals: Counter[str] = Counter()
    for run in runs:
        crossing = _first_crossing(run)
        for decision in DECISIONS:
            window = select_window(run, decision)
            if window.status != "ready":
                refusals[window.status] += 1
                continue
            ready += 1
            if method == "last_value":
                prediction = last_value_baseline(window.points)
            elif method == "linear_trend":
                prediction = linear_trend_baseline(window.points, window.target_at)
            else:
                result = predict_temperature(window, parameters)
                if result.predicted_temperature_c is None:
                    raise AssertionError("ready_window_without_ridge_prediction")
                prediction = result.predicted_temperature_c
            errors.append(abs(prediction - run.true_c[decision + HORIZON]))
            if window.points[-1].temperature_c >= THRESHOLD_C:
                continue
            below_threshold += 1
            if prediction < THRESHOLD_C + margin_c:
                continue
            if crossing is not None and decision < crossing <= decision + HORIZON:
                alerts.setdefault(run.run_id, []).append(crossing - decision)
            else:
                false_alarms += 1
                false_alarm_decisions_by_run.setdefault(run.run_id, []).append(decision)
    positive = {run.run_id for run in runs if _first_crossing(run) is not None}
    leads = [max(alerts[run_id]) for run_id in positive if run_id in alerts]
    return {
        "method": method,
        "margin_c": margin_c,
        "run_count": len(runs),
        "decision_count": len(runs) * len(DECISIONS),
        "ready_count": ready,
        "refusals": dict(sorted(refusals.items())),
        "mae_c": mean(errors) if errors else None,
        "mae_denominator": len(errors),
        "positive_events": len(positive),
        "detected_events": len(positive & alerts.keys()),
        "missed_events": len(positive - alerts.keys()),
        "lead_seconds_median": median(leads) if leads else None,
        "lead_denominator": len(leads),
        "false_alarm_decisions": false_alarms,
        "false_alarm_denominator": below_threshold,
        "false_alarm_runs": len(false_alarm_decisions_by_run),
        "false_alarm_episodes_10s": sum(
            1
            + sum(
                later - earlier > 10
                for earlier, later in zip(decisions, decisions[1:], strict=False)
            )
            for decisions in false_alarm_decisions_by_run.values()
        ),
        "false_alarm_decisions_by_run": {
            run_id: len(decisions)
            for run_id, decisions in sorted(false_alarm_decisions_by_run.items())
        },
    }


def select_margin(
    validation: list[Run], parameters: RidgeParameters
) -> tuple[float | None, dict[str, object]]:
    """Maximize detected events, then minimize false alarms, then margin.

    A candidate must match/exceed the linear trend's validation detections and
    not exceed its false-alarm decisions. If none qualifies, do not promote a
    calibrated ridge alert. These rules are fixed before confirmation runs.
    """
    linear = score(validation, parameters, method="linear_trend")
    candidates = [
        score(validation, parameters, method="ridge", margin_c=margin)
        for margin in MARGINS_C
    ]
    eligible = [
        item
        for item in candidates
        if item["detected_events"] >= linear["detected_events"]
        and item["false_alarm_decisions"] <= linear["false_alarm_decisions"]
    ]
    chosen = max(
        eligible,
        key=lambda item: (
            item["detected_events"],
            -item["false_alarm_decisions"],
            -item["margin_c"],
        ),
        default=None,
    )
    return (chosen["margin_c"] if chosen else None), {
        "linear_reference": linear,
        "ridge_candidates": candidates,
        "chosen_margin_c": chosen["margin_c"] if chosen else None,
        "rule": "validation only: detected >= linear and false alarms <= linear; maximize detections, minimize false alarms, prefer smaller margin",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parameters", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    parameters = RidgeParameters.from_json(
        json.loads(args.parameters.read_text(encoding="utf-8"))
    )
    validation = [
        make_run(scenario, index) for scenario in SCENARIOS for index in range(12, 15)
    ]
    margin, selection = select_margin(validation, parameters)
    # Fresh seeds and complete runs are instantiated only after the choice.
    confirmation = [
        make_run(scenario, index)
        for scenario in SCENARIOS
        for index in range(
            RUNS_PER_SCENARIO, RUNS_PER_SCENARIO + CONFIRMATION_RUNS_PER_SCENARIO
        )
    ]
    results = {
        "source": "deterministic synthetic only",
        "initial_test_runs_used_for_selection": False,
        "model_parameter_sha256": parameters.sha256(),
        "margin_grid_c": list(MARGINS_C),
        "validation_run_ids": [run.run_id for run in validation],
        "confirmation_run_ids": [run.run_id for run in confirmation],
        "selection": selection,
        "confirmation": {
            "last_value": score(confirmation, parameters, method="last_value"),
            "linear_trend": score(confirmation, parameters, method="linear_trend"),
            "ridge_no_margin": score(confirmation, parameters, method="ridge"),
            "ridge_selected_margin": (
                score(confirmation, parameters, method="ridge", margin_c=margin)
                if margin is not None
                else None
            ),
        },
        "scope": "one calibrated synthetic confirmation; not live early-warning efficacy",
    }
    payload = (
        json.dumps(
            results, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    )
    args.output.write_text(payload, encoding="utf-8")
    sys.stdout.write(
        json.dumps(
            {
                "selected_margin_c": margin,
                "validation_linear_events": selection["linear_reference"][
                    "detected_events"
                ],
                "confirmation_linear_events": results["confirmation"]["linear_trend"][
                    "detected_events"
                ],
                "confirmation_ridge_events": results["confirmation"][
                    "ridge_selected_margin"
                ]["detected_events"]
                if margin is not None
                else None,
            },
            sort_keys=True,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
