"""Standalone fixed-example tests for C telemetry quality processing."""

import json
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

from app.modules.telemetry.quality import process_batch

_EXAMPLES = Path(__file__).resolve().parents[3] / "docs" / "modules" / "C" / "examples"


def _example() -> dict[str, Any]:
    return json.loads((_EXAMPLES / "telemetry-input.json").read_text(encoding="utf-8"))


def _base() -> dict[str, Any]:
    return deepcopy(_example()["observations"][0])


class TelemetryQualityTests(unittest.TestCase):
    def test_fixed_json_input_output(self) -> None:
        example = _example()
        expected = json.loads(
            (_EXAMPLES / "telemetry-output.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            process_batch(example["observations"], example["threshold_c"]), expected
        )

    def test_normal_preserves_raw(self) -> None:
        original = _base()
        result = process_batch([original], 8)[0]
        self.assertEqual(result["raw"], original)
        self.assertEqual(result["quality"], ["ok"])
        self.assertEqual(result["risk"]["state"], "normal")
        self.assertEqual(result["normalized"]["metrics"]["temperature"], 4.0)

    def test_real_heat_is_kept_and_flagged(self) -> None:
        original = _base()
        original["metrics"][0]["value"] = 12
        result = process_batch([original], 8)[0]
        self.assertEqual(result["raw"]["metrics"][0]["value"], 12)
        self.assertEqual(result["normalized"]["metrics"]["temperature"], 12.0)
        self.assertEqual(result["risk"]["state"], "candidate")

    def test_late_older_heat_remains_a_candidate(self) -> None:
        recent = _base()
        recent["schema_version"] = "1.0"
        recent["sampled_at"] = "2026-10-10T00:10:00Z"
        recent["received_at"] = "2026-10-10T00:10:01Z"
        older_heat = deepcopy(recent)
        older_heat["message_id"] = "44444444-4444-4444-8444-444444444444"
        older_heat["sampled_at"] = "2026-10-10T00:05:00Z"
        older_heat["received_at"] = "2026-10-10T00:11:00Z"
        older_heat["metrics"][0]["value"] = 12

        results = process_batch([recent, older_heat], 8)

        self.assertEqual([row["duplicate"] for row in results], ["new", "new"])
        self.assertEqual(
            [row["risk"]["state"] for row in results], ["normal", "candidate"]
        )
        self.assertEqual(results[1]["raw"], older_heat)
        self.assertEqual(results[1]["quality"], ["ok"])
        self.assertEqual(results[1]["normalized"]["sampled_at"], "2026-10-10T00:05:00Z")
        self.assertEqual(
            results[1]["normalized"]["received_at"], "2026-10-10T00:11:00Z"
        )

    def test_missing_field_and_metric_are_unknown(self) -> None:
        original = _base()
        del original["received_at"]
        original["metrics"] = [original["metrics"][0]]
        result = process_batch([original], 8)[0]
        self.assertIn("missing_field:received_at", result["quality"])
        self.assertIn("missing_metric:humidity", result["quality"])
        self.assertEqual(result["risk"]["state"], "unknown")

    def test_missing_temperature_never_becomes_normal(self) -> None:
        original = _base()
        original["metrics"][0]["value"] = None
        result = process_batch([original], 8)[0]
        self.assertIn("missing_value:temperature", result["quality"])
        self.assertIsNone(result["risk"]["temperature_c"])
        self.assertEqual(result["risk"]["state"], "unknown")

    def test_invalid_time_is_unknown(self) -> None:
        original = _base()
        original["sampled_at"] = "2026-10-10 00:00:00"
        result = process_batch([original], 8)[0]
        self.assertIn("invalid_time:sampled_at", result["quality"])
        self.assertEqual(result["risk"]["reason"], "untraceable_observation")

    def test_submicrosecond_time_is_not_silently_truncated_into_replay(self) -> None:
        first = _base()
        first["sampled_at"] = "2026-10-10T00:00:00.1234561Z"
        changed = deepcopy(first)
        changed["sampled_at"] = "2026-10-10T00:00:00.1234569Z"
        results = process_batch([first, changed], 8)
        self.assertEqual([row["duplicate"] for row in results], ["new", "conflict"])
        for row, raw in zip(results, (first, changed), strict=True):
            self.assertEqual(row["raw"], raw)
            self.assertIn("invalid_time:sampled_at", row["quality"])
            self.assertNotIn("sampled_at", row["normalized"])
            self.assertEqual(row["risk"]["state"], "unknown")

        valid = deepcopy(first)
        valid["sampled_at"] = "2026-10-10T00:00:00.123456Z"
        accepted = process_batch([valid], 8)[0]
        self.assertEqual(accepted["normalized"]["sampled_at"], valid["sampled_at"])

    def test_invalid_unit_does_not_convert_temperature(self) -> None:
        original = _base()
        original["metrics"][0]["unit"] = "°F"
        original["metrics"][0]["value"] = 104
        result = process_batch([original], 8)[0]
        self.assertIn("invalid_unit:temperature", result["quality"])
        self.assertNotIn("temperature", result["normalized"]["metrics"])
        self.assertEqual(result["risk"]["state"], "unknown")
        self.assertEqual(result["raw"]["metrics"][0]["value"], 104)

    def test_duplicate_ignores_new_receipt_time_but_preserves_it(self) -> None:
        first = _base()
        second = deepcopy(first)
        second["received_at"] = "2026-10-10T00:00:03Z"
        results = process_batch([first, second], 8)
        self.assertEqual(results[1]["duplicate"], "duplicate")
        self.assertIn("duplicate_message", results[1]["quality"])
        self.assertEqual(results[1]["risk"]["state"], "unknown")
        self.assertEqual(results[1]["raw"]["received_at"], second["received_at"])

    def test_same_message_id_with_changed_measurement_is_conflict(self) -> None:
        first = _base()
        second = deepcopy(first)
        second["metrics"][0]["value"] = 12
        results = process_batch([first, second], 8)
        self.assertEqual(results[1]["duplicate"], "conflict")
        self.assertIn("conflicting_message", results[1]["quality"])
        self.assertEqual(results[1]["normalized"]["metrics"]["temperature"], 12.0)
        self.assertEqual(results[1]["raw"], second)
        self.assertEqual(results[1]["risk"]["state"], "unknown")

    def test_high_temperature_with_missing_humidity_still_candidate(self) -> None:
        original = _base()
        original["metrics"] = [{"key": "temperature", "value": 12, "unit": "°C"}]
        result = process_batch([original], 8)[0]
        self.assertIn("missing_metric:humidity", result["quality"])
        self.assertEqual(result["risk"]["state"], "candidate")

    def test_blank_or_padded_schema_version_is_not_normal(self) -> None:
        for version in (" ", " 1.0", "1.0 ", "2.0", "0.9"):
            with self.subTest(version=version):
                original = _base()
                original["schema_version"] = version
                result = process_batch([original], 8)[0]
                self.assertEqual(result["raw"], original)
                self.assertIn("invalid_schema_version", result["quality"])
                self.assertNotIn("schema_version", result["normalized"])
                self.assertEqual(result["risk"]["state"], "unknown")

    def test_unknown_schema_cannot_create_high_temperature_candidate(self) -> None:
        original = _base()
        original["schema_version"] = "2.0"
        original["metrics"][0]["value"] = 12
        result = process_batch([original], 8)[0]
        self.assertEqual(result["raw"], original)
        self.assertEqual(result["risk"]["temperature_c"], 12.0)
        self.assertEqual(result["risk"]["state"], "unknown")
        self.assertEqual(result["risk"]["reason"], "untraceable_observation")

    def test_a_ingress_schema_is_accepted_by_pure_quality(self) -> None:
        original = _base()
        original["schema_version"] = "1.0"
        result = process_batch([original], 8)[0]
        self.assertEqual(result["quality"], ["ok"])
        self.assertEqual(result["normalized"]["schema_version"], "1.0")
        self.assertEqual(result["risk"]["state"], "normal")

    def test_batch_must_be_a_json_array(self) -> None:
        invalid_batches: tuple[Any, ...] = (
            {"observations": [_base()]},
            "not-an-array",
        )
        for invalid in invalid_batches:
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(ValueError, "observations must be a list"),
            ):
                process_batch(invalid, 8)

    def test_no_rule_is_unknown(self) -> None:
        result = process_batch([_base()])[0]
        self.assertEqual(result["risk"]["reason"], "no_threshold_rule")
        self.assertEqual(result["risk"]["state"], "unknown")

    def test_invalid_ids_and_values_are_marked(self) -> None:
        original = _base()
        original["device_id"] = "not-a-uuid"
        original["metrics"][0]["value"] = "hot"
        original["metrics"][1]["value"] = 101
        result = process_batch([original], 8)[0]
        self.assertIn("invalid_id:device_id", result["quality"])
        self.assertIn("invalid_value:temperature", result["quality"])
        self.assertIn("invalid_range:humidity", result["quality"])
        self.assertEqual(result["risk"]["state"], "unknown")

    def test_metric_count_limit_marks_quality_without_dropping_heat(self) -> None:
        original = _base()
        original["metrics"][0]["value"] = 12
        original["metrics"].extend(
            {"key": f"aux_{index}", "value": index, "unit": "u"} for index in range(15)
        )
        result = process_batch([original], 8)[0]
        self.assertEqual(len(result["raw"]["metrics"]), 17)
        self.assertIn("too_many_metrics", result["quality"])
        self.assertEqual(result["normalized"]["metrics"]["temperature"], 12.0)
        self.assertEqual(result["risk"]["state"], "unknown")
        self.assertEqual(result["risk"]["reason"], "invalid_metric_shape")

    def test_metric_key_and_unit_lengths_are_marked(self) -> None:
        original = _base()
        original["metrics"].extend(
            [
                {"key": "k" * 65, "value": 1, "unit": "u"},
                {"key": "voltage", "value": 1, "unit": "u" * 65},
                {"key": "signal", "value": 1, "unit": None},
            ]
        )
        result = process_batch([original], 8)[0]
        self.assertEqual(result["raw"], original)
        self.assertIn("invalid_metric_key_length", result["quality"])
        self.assertIn("invalid_metric_unit_length:voltage", result["quality"])
        self.assertIn("invalid_metric_unit:signal", result["quality"])
        self.assertEqual(result["risk"]["state"], "unknown")
        self.assertEqual(result["risk"]["reason"], "invalid_metric_shape")

        at_limit = _base()
        at_limit["metrics"].append({"key": "k" * 64, "value": 1, "unit": "u" * 64})
        self.assertEqual(process_batch([at_limit], 8)[0]["quality"], ["ok"])

        repeated = _base()
        repeated["metrics"].append({"key": "temperature", "value": 99, "unit": None})
        ambiguous = process_batch([repeated], 8)[0]
        self.assertIn("duplicate_metric:temperature", ambiguous["quality"])
        self.assertNotIn("temperature", ambiguous["normalized"]["metrics"])
        self.assertEqual(ambiguous["risk"]["state"], "unknown")

    def test_duplicate_metric_and_malformed_entry_are_marked(self) -> None:
        original = _base()
        original["metrics"].extend(
            [
                {"key": "temperature", "value": 5, "unit": "°C"},
                {"key": "power", "value": 10, "unit": "W"},
                "broken",
            ]
        )
        result = process_batch([original], 8)[0]
        self.assertIn("duplicate_metric:temperature", result["quality"])
        self.assertIn("invalid_metric_entry", result["quality"])
        self.assertEqual(result["risk"]["state"], "unknown")

    def test_raw_snapshot_is_independent_of_later_mutations(self) -> None:
        original = _base()
        snapshot = deepcopy(original)
        result = process_batch([original], 8)[0]
        original["metrics"][0]["value"] = 50
        self.assertEqual(result["raw"], snapshot)
        self.assertEqual(result["normalized"]["metrics"]["temperature"], 4.0)
        result["raw"]["metrics"][0]["value"] = 60
        self.assertEqual(original["metrics"][0]["value"], 50)
        self.assertEqual(result["normalized"]["metrics"]["temperature"], 4.0)

    def test_non_utc_time_and_malformed_metrics_keep_raw_unknown(self) -> None:
        for sampled_at in (
            "2026-10-10T08:00:00+08:00",
            "2026-10-10T00:00:00",
        ):
            with self.subTest(sampled_at=sampled_at):
                original = _base()
                original["sampled_at"] = sampled_at
                result = process_batch([original], 8)[0]
                self.assertEqual(result["raw"], original)
                self.assertIn("invalid_time:sampled_at", result["quality"])
                self.assertEqual(result["risk"]["state"], "unknown")

        original = _base()
        original["metrics"] = {"temperature": 12}
        result = process_batch([original], 8)[0]
        self.assertEqual(result["raw"], original)
        self.assertIn("invalid_metrics", result["quality"])
        self.assertIn("missing_metric:temperature", result["quality"])
        self.assertEqual(result["risk"]["state"], "unknown")

    def test_non_object_and_invalid_threshold_fail_clearly(self) -> None:
        result = process_batch(["bad"], 8)[0]
        self.assertEqual(result["raw"], "bad")
        self.assertEqual(result["quality"], ["invalid_observation"])
        self.assertEqual(result["risk"]["state"], "unknown")
        with self.assertRaisesRegex(ValueError, "finite number"):
            process_batch([_base()], float("nan"))
        with self.assertRaisesRegex(ValueError, "finite number"):
            process_batch([_base()], True)

    def test_threshold_equal_is_normal_and_above_is_candidate(self) -> None:
        equal = _base()
        equal["metrics"][0]["value"] = 8
        above = _base()
        above["message_id"] = "44444444-4444-4444-8444-444444444444"
        above["metrics"][0]["value"] = 8.01
        results = process_batch([equal, above], 8)
        self.assertEqual(results[0]["risk"]["state"], "normal")
        self.assertEqual(results[1]["risk"]["state"], "candidate")
        self.assertEqual(results[1]["raw"]["metrics"][0]["value"], 8.01)

    def test_same_message_with_changed_context_conflicts(self) -> None:
        for field, changed in (
            ("warehouse_id", "55555555-5555-4555-8555-555555555555"),
            ("sampled_at", "2026-10-10T00:01:00Z"),
            ("schema_version", "1.0"),
        ):
            with self.subTest(field=field):
                first = _base()
                second = deepcopy(first)
                second[field] = changed
                results = process_batch([first, second], 8)
                self.assertEqual(results[1]["duplicate"], "conflict")
                self.assertEqual(results[1]["raw"], second)
                self.assertEqual(results[1]["risk"]["state"], "unknown")

    def test_changed_unknown_content_is_conflict(self) -> None:
        first = _base()
        first["firmware"] = {"revision": "r1", "flags": ["test"]}
        reordered = dict(reversed(list(first.items())))
        changed = deepcopy(first)
        changed["firmware"]["revision"] = "r2"
        results = process_batch([first, reordered, changed], 8)
        self.assertEqual(
            [item["duplicate"] for item in results],
            ["new", "duplicate", "conflict"],
        )
        self.assertEqual(results[2]["raw"]["firmware"]["revision"], "r2")
        self.assertEqual(results[2]["risk"]["state"], "unknown")

    def test_message_identity_is_scoped_to_device(self) -> None:
        first = _base()
        second = deepcopy(first)
        second["device_id"] = "99999999-9999-4999-8999-999999999999"
        results = process_batch([first, second], 8)
        self.assertEqual([item["duplicate"] for item in results], ["new", "new"])

    def test_proposal_observation_sequence_and_filter_example(self) -> None:
        proposal = json.loads(
            (_EXAMPLES / "telemetry-contract-proposal.json").read_text(encoding="utf-8")
        )
        results = process_batch(
            proposal["a_to_c"]["observations"], proposal["fixture_threshold_c"]
        )
        self.assertEqual(
            [item["duplicate"] for item in results],
            ["new", "duplicate", "conflict", "new", "new", "new", "new"],
        )
        self.assertEqual(
            [item["risk"]["state"] for item in results],
            [
                "normal",
                "unknown",
                "unknown",
                "candidate",
                "unknown",
                "unknown",
                "unknown",
            ],
        )
        self.assertEqual(
            results[1]["normalized"]["sampled_at"],
            results[0]["normalized"]["sampled_at"],
        )
        self.assertEqual(results[2]["raw"], proposal["a_to_c"]["observations"][2])
        self.assertIn("missing_value:temperature", results[4]["quality"])
        self.assertIn("invalid_time:sampled_at", results[5]["quality"])
        self.assertIn("invalid_unit:temperature", results[6]["quality"])
        self.assertEqual(results[6]["raw"]["metrics"][0]["value"], 104)
        self.assertEqual(
            proposal["query"]["page"]["filters"],
            proposal["query"]["csv"]["filters"],
        )

    def test_rc1_unordered_metrics_and_duplicate_key_proposal(self) -> None:
        fixture = json.loads(
            (_EXAMPLES / "telemetry-rc1-proposal.json").read_text(encoding="utf-8")
        )
        results = process_batch(fixture["observations"], fixture["fixture_threshold_c"])
        self.assertEqual(
            [result["duplicate"] for result in results],
            fixture["expected_duplicate"],
        )
        self.assertEqual(
            [result["quality"] for result in results],
            fixture["expected_quality"],
        )
        self.assertEqual(
            [result["risk"]["state"] for result in results],
            fixture["expected_risk_state"],
        )
        self.assertEqual(
            results[0]["normalized"]["sampled_at"],
            results[1]["normalized"]["sampled_at"],
        )
        self.assertNotEqual(results[0]["raw"]["metrics"], results[1]["raw"]["metrics"])
        self.assertEqual(results[1]["raw"], fixture["observations"][1])
        self.assertEqual(results[3]["raw"], fixture["observations"][3])
        self.assertNotIn("temperature", results[3]["normalized"]["metrics"])
        self.assertIsNone(results[3]["risk"]["temperature_c"])

    def test_duplicate_unknown_metric_and_empty_key_are_invalid(self) -> None:
        for additions, expected_tag in (
            (
                [
                    {"key": "power", "value": 10, "unit": "W"},
                    {"key": "power", "value": 11, "unit": "W"},
                ],
                "duplicate_metric:power",
            ),
            ([{"key": "", "value": 1, "unit": "W"}], "invalid_metric_key"),
        ):
            with self.subTest(expected_tag=expected_tag):
                original = _base()
                original["metrics"].extend(additions)
                result = process_batch([original], 8)[0]
                self.assertEqual(result["raw"], original)
                self.assertIn(expected_tag, result["quality"])
                self.assertEqual(result["risk"]["state"], "unknown")

    def test_high_temperature_with_repeated_unknown_metric_is_ambiguous(self) -> None:
        original = _base()
        original["metrics"][0]["value"] = 12
        original["metrics"].extend(
            [
                {"key": "power", "value": 10, "unit": "W"},
                {"key": "power", "value": 11, "unit": "W"},
            ]
        )
        result = process_batch([original], 8)[0]
        self.assertEqual(result["raw"], original)
        self.assertEqual(result["normalized"]["metrics"]["temperature"], 12.0)
        self.assertIn("duplicate_metric:power", result["quality"])
        self.assertEqual(result["risk"]["state"], "unknown")
        self.assertEqual(result["risk"]["reason"], "ambiguous_metrics")

    def test_query_csv_acceptance_vector_matches_quality_fixture(self) -> None:
        proposal = json.loads(
            (_EXAMPLES / "telemetry-contract-proposal.json").read_text(encoding="utf-8")
        )
        acceptance = json.loads(
            (_EXAMPLES / "query-csv-acceptance.json").read_text(encoding="utf-8")
        )
        observations = proposal["a_to_c"]["observations"]
        results = process_batch(observations, proposal["fixture_threshold_c"])
        rows = acceptance["assigned_rows"]
        self.assertEqual(len(rows), len(results))
        ids = [row["observation_id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        for index, (row, result) in enumerate(zip(rows, results, strict=True)):
            with self.subTest(index=index):
                self.assertEqual(row["source_index"], index)
                self.assertEqual(
                    str(UUID(row["observation_id"])), row["observation_id"]
                )
                self.assertEqual(result["raw"], observations[index])
                self.assertEqual(row["expected_quality"], result["quality"])
                self.assertEqual(row["expected_duplicate"], result["duplicate"])
                self.assertEqual(row["expected_risk_state"], result["risk"]["state"])

        scenario = acceptance["scenarios"]["same_filter_page_and_csv"]
        self.assertEqual(scenario["filters"], scenario["csv"]["filters"])
        page_ids = [
            item for page in scenario["page"]["expected_pages"] for item in page
        ]
        self.assertEqual(page_ids, scenario["csv"]["expected_observation_ids"])
        self.assertEqual(len(page_ids), scenario["csv"]["expected_record_count"])
        self.assertEqual(
            acceptance["scenarios"]["invalid_sample_time_quality_only"][
                "expected_observation_ids"
            ],
            [rows[5]["observation_id"]],
        )


if __name__ == "__main__":
    unittest.main()
