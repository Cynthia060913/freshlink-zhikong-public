"""Compare C synthetic-window selection with an explicitly supplied A guard.

This is a development-only, pure computation check. It neither imports an
unmerged A branch at application startup nor accesses a database or network.
Run only with reviewed, trusted source passed as --guard-file.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path

from app.modules.telemetry.forecast_candidate import (
    RidgeParameters,
    predict_temperature,
)
from experiments.c_forecast.simulate_evaluate import (
    DECISIONS,
    RUNS_PER_SCENARIO,
    SCENARIOS,
    _time,
    make_run,
    select_window,
)


def verify(guard_file: Path, parameter_file: Path) -> dict[str, object]:
    """Check all 60 synthetic runs against exactly one reviewed guard file."""
    source = guard_file.read_bytes()
    spec = importlib.util.spec_from_file_location(
        "reviewed_forecast_window", guard_file
    )
    if spec is None or spec.loader is None:
        raise ValueError("guard_source_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    parameters = RidgeParameters.from_json(
        json.loads(parameter_file.read_text(encoding="utf-8"))
    )
    counts: Counter[str] = Counter()
    for scenario in SCENARIOS:
        for index in range(RUNS_PER_SCENARIO):
            run = make_run(scenario, index)
            rows = [
                {
                    "observation_id": str(row.observation_id),
                    "device_id": str(run.device_id),
                    "warehouse_id": str(run.warehouse_id),
                    "sampled_at": _time(row.sampled_second),
                    "received_at": _time(row.received_second),
                    "temperature_c": row.temperature_c,
                    "quality": [row.quality],
                }
                for row in run.observations
            ]
            for decision in DECISIONS:
                expected = select_window(run, decision)
                actual = module.build_forecast_window(
                    rows,
                    device_id=run.device_id,
                    warehouse_id=run.warehouse_id,
                    as_of=_time(decision),
                )
                if actual.status != expected.status:
                    raise AssertionError((run.run_id, decision, "status_mismatch"))
                if tuple(p.observation_id for p in actual.points) != tuple(
                    p.observation_id for p in expected.points
                ):
                    raise AssertionError((run.run_id, decision, "point_ID_mismatch"))
                result = predict_temperature(actual, parameters)
                if actual.status == "ready":
                    if (
                        result.status != "predicted"
                        or result.predicted_temperature_c is None
                    ):
                        raise AssertionError(
                            (run.run_id, decision, "ready_not_predicted")
                        )
                elif (
                    result.status != "unavailable"
                    or result.predicted_temperature_c is not None
                ):
                    raise AssertionError((run.run_id, decision, "refusal_mismatch"))
                counts[actual.status] += 1
    return {
        "guard_source_sha256": sha256(source).hexdigest(),
        "parameter_sha256": parameters.sha256(),
        "runs": len(SCENARIOS) * RUNS_PER_SCENARIO,
        "windows": sum(counts.values()),
        "status_counts": dict(sorted(counts.items())),
        "scope": "pure synthetic window parity; no PG/HTTP/page/model effect claim",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--guard-file", required=True, type=Path)
    parser.add_argument(
        "--parameters",
        type=Path,
        default=Path(__file__).resolve().parent
        / "artifacts"
        / "v1"
        / "parameters.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.guard_file, args.parameters)
    result = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(result, encoding="utf-8")
    sys.stdout.write(result)


if __name__ == "__main__":
    main()
