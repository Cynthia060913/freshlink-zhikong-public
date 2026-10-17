"""Fixed-vector CSV bytes for C1 preview only."""

import csv
import json
import unittest
from copy import deepcopy
from io import StringIO
from pathlib import Path
from typing import Any

from app.modules.telemetry.csv_preview import (
    MAX_PREVIEW_EXPORT_ROWS,
    PREVIEW_COLUMNS,
    render_preview_csv,
)
from app.modules.telemetry.quality import process_batch
from app.modules.telemetry.query_preview import (
    page_preview_by_sampled_at,
    select_preview_observations,
)

_EXAMPLES = Path(__file__).resolve().parents[3] / "docs" / "modules" / "C" / "examples"


def _fixture() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    proposal = json.loads(
        (_EXAMPLES / "telemetry-contract-proposal.json").read_text(encoding="utf-8")
    )
    acceptance = json.loads(
        (_EXAMPLES / "query-csv-acceptance.json").read_text(encoding="utf-8")
    )
    results = process_batch(
        proposal["a_to_c"]["observations"], proposal["fixture_threshold_c"]
    )
    for result, row in zip(results, acceptance["assigned_rows"], strict=True):
        result["observation_id"] = row["observation_id"]
    return results, acceptance


def _parse(data: bytes) -> list[dict[str, str]]:
    if not data.startswith(b"\xef\xbb\xbf"):
        raise AssertionError("preview CSV must have a UTF-8 BOM")
    reader = csv.DictReader(StringIO(data.decode("utf-8-sig"), newline=""))
    if reader.fieldnames != list(PREVIEW_COLUMNS):
        raise AssertionError("preview CSV columns changed")
    return list(reader)


class TelemetryCsvPreviewTests(unittest.TestCase):
    def test_same_filters_as_pages_export_every_selected_id_once(self) -> None:
        results, acceptance = _fixture()
        scenario = acceptance["scenarios"]["same_filter_page_and_csv"]
        filters = scenario["filters"]
        rows = _parse(render_preview_csv(results, filters))
        self.assertEqual(
            [row["observation_id"] for row in rows],
            scenario["csv"]["expected_observation_ids"],
        )
        self.assertEqual(len(rows), scenario["csv"]["expected_record_count"])
        selected = select_preview_observations(results, filters)
        page_ids: list[str] = []
        after = None
        while True:
            page, after = page_preview_by_sampled_at(
                selected, limit=1, after_observation_id=after
            )
            page_ids.extend(row["observation_id"] for row in page)
            if after is None:
                break
        self.assertEqual(page_ids, [row["observation_id"] for row in rows])
        self.assertEqual(rows[0]["risk_state"], "normal")
        self.assertEqual(rows[1]["risk_state"], "candidate")
        self.assertEqual(rows[1]["raw_temperature_value"], "11")
        self.assertEqual(rows[1]["temperature_c"], "11.0")
        self.assertEqual(rows[1]["rule_version"], "")
        self.assertEqual(results[3]["raw"]["metrics"][0]["value"], 11)

    def test_invalid_sample_and_missing_value_remain_unknown(self) -> None:
        results, acceptance = _fixture()
        filters = acceptance["scenarios"]["invalid_sample_time_quality_only"]["filters"]
        invalid = _parse(render_preview_csv(results, filters))
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]["sampled_at"], "")
        self.assertEqual(invalid[0]["risk_state"], "unknown")
        self.assertEqual(results[5]["raw"]["sampled_at"], "2026-10-10 00:03:00")
        missing = _parse(
            render_preview_csv(results, {"quality_in": ["missing_value:temperature"]})
        )
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["raw_temperature_value"], "")
        self.assertEqual(missing[0]["temperature_c"], "")
        self.assertEqual(missing[0]["risk_state"], "unknown")

    def test_empty_result_writes_header_only(self) -> None:
        results, _ = _fixture()
        data = render_preview_csv(results, {"sampled_from": "2026-10-11T00:00:00Z"})
        self.assertEqual(_parse(data), [])
        self.assertEqual(data.decode("utf-8-sig").count("\r\n"), 1)

    def test_export_rejects_limit_instead_of_silent_truncation(self) -> None:
        results, _ = _fixture()
        oversized = [results[0]] * (MAX_PREVIEW_EXPORT_ROWS + 1)
        with self.assertRaisesRegex(ValueError, "row limit exceeded"):
            render_preview_csv(oversized, {})

    def test_formula_and_multiline_raw_text_are_quoted_without_mutation(self) -> None:
        results, _ = _fixture()
        raw = deepcopy(results[0]["raw"])
        raw["metrics"][0]["value"] = '=HYPERLINK("https://example.invalid","x")\n次行'
        case = process_batch([raw], 8)[0]
        case["observation_id"] = results[0]["observation_id"]
        self.assertEqual(case["risk"]["state"], "unknown")
        rows = _parse(render_preview_csv([case], {}))
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["raw_temperature_value"],
            "'" + raw["metrics"][0]["value"],
        )
        self.assertEqual(rows[0]["temperature_c"], "")
        self.assertEqual(case["raw"], raw)

    def test_fullwidth_formula_prefixes_remain_text_and_raw_is_unchanged(self) -> None:
        results, _ = _fixture()
        for expression in (
            "＝1+1",
            "　＋1",
            "－5",
            "＠SUM(1)",
            "\x00＝1",
            "\ufeff=1+1",
            "\u200b=1+1",
            "\u202e=1+1",
            " \u200b=1+1",
            "\t\ufeff\u200b＝1+1",
            " \u200e\t＠SUM(1)",
        ):
            with self.subTest(expression=expression):
                raw = deepcopy(results[0]["raw"])
                raw["metrics"][0]["value"] = expression
                case = process_batch([raw], 8)[0]
                case["observation_id"] = results[0]["observation_id"]
                row = _parse(render_preview_csv([case], {}))[0]
                self.assertEqual(row["raw_temperature_value"], "'" + expression)
                self.assertEqual(row["temperature_c"], "")
                self.assertEqual(case["raw"], raw)

    def test_escaped_lone_surrogate_remains_exportable_and_raw_is_unchanged(
        self,
    ) -> None:
        results, _ = _fixture()
        raw = deepcopy(results[0]["raw"])
        raw["metrics"][0]["value"] = "\ud800"
        case = process_batch([raw], 8)[0]
        case["observation_id"] = results[0]["observation_id"]
        row = _parse(render_preview_csv([case], {}))[0]
        self.assertEqual(row["raw_temperature_value"], "\\ud800")
        self.assertEqual(row["temperature_c"], "")
        self.assertEqual(row["risk_state"], "unknown")
        self.assertEqual(case["raw"], raw)

    def test_negative_temperature_stays_numeric_while_text_is_neutralized(self) -> None:
        results, _ = _fixture()
        raw = deepcopy(results[0]["raw"])
        raw["metrics"][0]["value"] = -5
        raw["metrics"][0]["unit"] = " \t=bad_unit()"
        case = process_batch([raw], 8)[0]
        case["observation_id"] = results[0]["observation_id"]
        rows = _parse(render_preview_csv([case], {}))
        self.assertEqual(rows[0]["raw_temperature_value"], "-5")
        self.assertEqual(rows[0]["raw_temperature_unit"], "' \t=bad_unit()")
        self.assertEqual(rows[0]["temperature_c"], "")
        self.assertEqual(rows[0]["risk_state"], "unknown")
        valid = deepcopy(results[0]["raw"])
        valid["metrics"][0]["value"] = -5
        accepted = process_batch([valid], 8)[0]
        accepted["observation_id"] = results[0]["observation_id"]
        numeric = _parse(render_preview_csv([accepted], {}))[0]
        self.assertEqual(numeric["temperature_c"], "-5.0")
        self.assertEqual(numeric["raw_temperature_value"], "-5")

    def test_repeated_metric_key_is_not_arbitrarily_exported(self) -> None:
        results, _ = _fixture()
        raw = deepcopy(results[0]["raw"])
        raw["metrics"].append(deepcopy(raw["metrics"][0]))
        case = process_batch([raw], 8)[0]
        case["observation_id"] = results[0]["observation_id"]
        row = _parse(render_preview_csv([case], {}))[0]
        self.assertEqual(row["raw_temperature_value"], "")
        self.assertEqual(row["temperature_c"], "")
        self.assertIn("duplicate_metric:temperature", row["quality"])
        self.assertEqual(row["risk_state"], "unknown")

    def test_snapshot_contract_example_matches_fixed_pure_rows(self) -> None:
        proposal = json.loads(
            (_EXAMPLES / "query-snapshot-rc1-proposal.json").read_text(encoding="utf-8")
        )
        results, _ = _fixture()
        selected = select_preview_observations(results, proposal["request"]["filters"])
        body = proposal["response"]["body"]
        self.assertEqual(body["total"], 2)
        self.assertEqual(len(body["items"]), len(selected))
        self.assertEqual(
            [item["observation_id"] for item in body["items"]],
            [result["observation_id"] for result in selected],
        )
        for item, result in zip(body["items"], selected, strict=True):
            normalized = result["normalized"]
            metrics = normalized["metrics"]
            self.assertEqual(item["sampled_at"], normalized.get("sampled_at"))
            self.assertEqual(item["received_at"], normalized.get("received_at"))
            self.assertEqual(item["temperature_c"], metrics.get("temperature"))
            self.assertEqual(item["humidity_percent"], metrics.get("humidity"))
            self.assertEqual(item["quality"], result["quality"])
            self.assertEqual(item["risk_state"], result["risk"]["state"])
            self.assertNotIn("raw", item)
        export = proposal["export_request"]
        self.assertEqual(export["filters"], proposal["request"]["filters"])
        self.assertEqual(export["snapshot_id"], body["snapshot_id"])
        self.assertEqual(export["received_watermark"], body["received_watermark"])
        self.assertEqual(
            export["expected_observation_ids"],
            [item["observation_id"] for item in body["items"]],
        )


if __name__ == "__main__":
    unittest.main()
