"""C-only F03 proposal vector; no B receiver, outbox, or delivery is exercised."""

import json
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from app.modules.telemetry.candidate_preview import build_candidate_preview
from app.modules.telemetry.quality import process_batch

_EXAMPLES = Path(__file__).resolve().parents[3] / "docs" / "modules" / "C" / "examples"


class CandidateContractProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proposal = json.loads(
            (_EXAMPLES / "candidate-f03-rc1-proposal.json").read_text(encoding="utf-8")
        )
        self.rc2 = json.loads(
            (_EXAMPLES / "candidate-f03-rc2-c-proposal.json").read_text(
                encoding="utf-8"
            )
        )
        observations = json.loads(
            (_EXAMPLES / "telemetry-contract-proposal.json").read_text(encoding="utf-8")
        )
        acceptance = json.loads(
            (_EXAMPLES / "query-csv-acceptance.json").read_text(encoding="utf-8")
        )
        self.assignments = acceptance["assigned_rows"]
        self.threshold = observations["fixture_threshold_c"]
        self.rows = process_batch(
            observations["a_to_c"]["observations"], self.threshold
        )

    def test_high_temperature_vector_matches_pure_processing(self) -> None:
        self.assertEqual(self.proposal["status"], "contract_target_not_implemented")
        candidate = self.proposal["candidate"]
        evidence = candidate["evidence"]
        hot = self.rows[3]
        self.assertEqual(hot["duplicate"], "new")
        self.assertEqual(hot["risk"]["state"], "candidate")
        self.assertEqual(hot["risk"]["reason"], "threshold_exceeded")
        self.assertEqual(evidence["message_id"], hot["normalized"]["message_id"])
        self.assertEqual(evidence["device_id"], hot["normalized"]["device_id"])
        self.assertEqual(evidence["sampled_at"], hot["normalized"]["sampled_at"])
        self.assertEqual(
            evidence["normalized_value"], hot["normalized"]["metrics"]["temperature"]
        )
        raw_temperature = next(
            metric
            for metric in hot["raw"]["metrics"]
            if isinstance(metric, dict) and metric.get("key") == "temperature"
        )
        self.assertEqual(evidence["raw_value"], raw_temperature["value"])
        self.assertEqual(evidence["quality"], hot["quality"])
        self.assertEqual(evidence["threshold"], self.threshold)
        self.assertEqual(evidence["comparison"], ">")
        self.assertGreater(evidence["normalized_value"], evidence["threshold"])
        self.assertEqual(evidence["normalized_unit"], "°C")
        self.assertEqual(candidate["occurred_at"], evidence["sampled_at"])
        for field in ("source_event_id", "rule_snapshot_id", "warehouse_id"):
            UUID(candidate[field])
        for value in candidate["observation_ids"] + candidate["batch_ids"]:
            UUID(value)
        self.assertEqual(
            candidate["observation_ids"],
            [self.assignments[3]["observation_id"]],
        )
        occurred = datetime.fromisoformat(
            candidate["occurred_at"].replace("Z", "+00:00")
        )
        self.assertEqual(occurred.utcoffset(), timedelta(0))

    def test_retry_vector_keeps_one_source_event_id(self) -> None:
        candidate_id = self.proposal["candidate"]["source_event_id"]
        steps = self.proposal["transport_sequence"]
        self.assertEqual(
            [step["step"] for step in steps],
            ["submit", "lookup_by_source", "retry_same_candidate"],
        )
        self.assertEqual(steps[0]["outcome"], "timeout")
        self.assertEqual(steps[1]["http_status"], 404)
        self.assertEqual(steps[1]["reason_code"], "not_found")
        self.assertEqual(steps[1]["outbox_state"], "retryable_unknown")
        self.assertEqual(
            steps[2]["expected_status"], "accepted_new_or_accepted_duplicate"
        )
        self.assertEqual(steps[1]["source_event_id"], candidate_id)
        self.assertEqual(steps[2]["source_event_id"], candidate_id)
        statuses = {result["status"] for result in self.proposal["other_result_shapes"]}
        self.assertEqual(
            statuses,
            {"accepted_duplicate", "conflict", "invalid", "retryable_failure"},
        )
        invalid = next(
            result
            for result in self.proposal["other_result_shapes"]
            if result["status"] == "invalid"
        )
        self.assertEqual(invalid["reason_code"], "unknown_batch")
        self.assertIs(invalid["retryable"], False)
        retryable = next(
            result
            for result in self.proposal["other_result_shapes"]
            if result["status"] == "retryable_failure"
        )
        self.assertEqual(retryable["reason_code"], "storage_unavailable")
        self.assertIs(retryable["retryable"], True)
        self.assertEqual(retryable["retry_after_s"], 30)

    def test_missing_and_replayed_rows_do_not_claim_candidate(self) -> None:
        replay, conflict, missing = self.rows[1], self.rows[2], self.rows[4]
        self.assertEqual(replay["duplicate"], "duplicate")
        self.assertEqual(conflict["duplicate"], "conflict")
        self.assertEqual(replay["risk"]["state"], "unknown")
        self.assertEqual(conflict["risk"]["state"], "unknown")
        example = self.proposal["not_emitted_example"]
        self.assertEqual(
            example["observation_id"], self.assignments[4]["observation_id"]
        )
        self.assertIsNone(missing["normalized"]["metrics"].get("temperature"))
        self.assertEqual(missing["risk"]["state"], example["risk_state"])
        self.assertEqual(missing["quality"], example["quality"])
        self.assertIsNone(example["temperature_c"])
        self.assertIs(example["candidate_sent"], False)

    def test_rc2_single_batch_vector_matches_quality_and_rule_fixture(self) -> None:
        rc2 = self.rc2
        self.assertEqual(rc2["status"], "C_F03_RC2_proposal_not_implemented")
        candidate = rc2["candidate"]
        source = rc2["source_observation"]
        rule = rc2["rule_fixture"]
        result = process_batch([source], rule["threshold"])[0]
        self.assertEqual(result["duplicate"], "new")
        self.assertEqual(result["risk"]["state"], "candidate")
        self.assertEqual(result["risk"]["reason"], "threshold_exceeded")
        self.assertEqual(
            set(candidate),
            {
                "schema_version",
                "source_event_id",
                "warehouse_id",
                "batch_id",
                "observation_ids",
                "metric_code",
                "sampled_at",
                "value",
                "unit",
                "kind",
                "severity",
                "rule_snapshot_id",
                "rule_version",
                "algorithm_version",
                "evidence",
            },
        )
        self.assertEqual(candidate["schema_version"], "1.0")
        self.assertEqual(
            candidate["warehouse_id"], result["normalized"]["warehouse_id"]
        )
        self.assertEqual(candidate["batch_id"], rule["batch_id"])
        self.assertEqual(rule["warehouse_id"], candidate["warehouse_id"])
        self.assertEqual(candidate["metric_code"], rule["metric_code"])
        self.assertEqual(candidate["sampled_at"], result["normalized"]["sampled_at"])
        self.assertEqual(
            candidate["value"], result["normalized"]["metrics"]["temperature"]
        )
        self.assertEqual(candidate["unit"], rule["unit"])
        self.assertEqual(candidate["kind"], "temperature_high")
        self.assertEqual(candidate["severity"], rule["severity"])
        self.assertEqual(candidate["rule_snapshot_id"], rule["rule_snapshot_id"])
        self.assertEqual(candidate["rule_version"], rule["rule_version"])
        self.assertEqual(
            candidate["observation_ids"], [self.assignments[3]["observation_id"]]
        )
        for field in (
            "source_event_id",
            "warehouse_id",
            "batch_id",
            "rule_snapshot_id",
        ):
            UUID(candidate[field])
        for value in candidate["observation_ids"]:
            UUID(value)
        sampled = datetime.fromisoformat(candidate["sampled_at"].replace("Z", "+00:00"))
        starts = datetime.fromisoformat(rule["effective_from"].replace("Z", "+00:00"))
        self.assertEqual(sampled.utcoffset(), timedelta(0))
        self.assertLessEqual(starts, sampled)
        self.assertIsNone(rule["effective_to"])
        evidence = candidate["evidence"]
        self.assertEqual(evidence["device_id"], result["normalized"]["device_id"])
        self.assertEqual(evidence["message_id"], result["normalized"]["message_id"])
        self.assertEqual(evidence["raw_value"], source["metrics"][0]["value"])
        self.assertEqual(evidence["raw_unit"], source["metrics"][0]["unit"])
        self.assertEqual(evidence["normalized_value"], candidate["value"])
        self.assertEqual(evidence["normalized_unit"], candidate["unit"])
        self.assertEqual(evidence["comparison"], rule["comparison"])
        self.assertEqual(evidence["threshold"], rule["threshold"])
        self.assertEqual(evidence["quality"], result["quality"])

    def test_rc2_candidate_is_built_from_processed_row_and_explicit_ids(self) -> None:
        source = deepcopy(self.rc2["source_observation"])
        rule = deepcopy(self.rc2["rule_fixture"])
        processed = process_batch([source], rule["threshold"])[0]
        expected = self.rc2["candidate"]
        built = build_candidate_preview(
            processed,
            observation_id=expected["observation_ids"][0],
            source_event_id=expected["source_event_id"],
            rule=rule,
        )
        self.assertEqual(built, expected)
        self.assertEqual(processed["raw"], source)
        self.assertEqual(rule, self.rc2["rule_fixture"])

    def test_rc2_builder_refuses_unknown_replay_and_conflict(self) -> None:
        source = deepcopy(self.rc2["source_observation"])
        rule = self.rc2["rule_fixture"]
        missing = deepcopy(source)
        missing["metrics"][0]["value"] = None
        wrong_unit = deepcopy(source)
        wrong_unit["metrics"][0]["unit"] = "°F"
        changed = deepcopy(source)
        changed["metrics"][0]["value"] = 12
        replay = process_batch([source, deepcopy(source)], 8)[1]
        conflict = process_batch([source, changed], 8)[1]
        rows = [
            process_batch([missing], 8)[0],
            process_batch([wrong_unit], 8)[0],
            process_batch([source])[0],
            replay,
            conflict,
        ]
        for row in rows:
            with self.subTest(reason=row["risk"]["reason"]):
                self.assertIsNone(
                    build_candidate_preview(
                        row,
                        observation_id=self.rc2["candidate"]["observation_ids"][0],
                        source_event_id=self.rc2["candidate"]["source_event_id"],
                        rule=rule,
                    )
                )
        high = process_batch([source], 8)[0]
        self.assertIsNone(
            build_candidate_preview(
                high,
                observation_id=self.rc2["candidate"]["observation_ids"][0],
                source_event_id=self.rc2["candidate"]["source_event_id"],
                rule=None,
            )
        )

    def test_rc2_builder_rejects_mismatched_rule_and_bad_source_id(self) -> None:
        source = deepcopy(self.rc2["source_observation"])
        rule = self.rc2["rule_fixture"]
        row = process_batch([source], rule["threshold"])[0]
        candidate = self.rc2["candidate"]
        changes = (
            {"warehouse_id": "99999999-9999-4999-8999-999999999999"},
            {"effective_to": source["sampled_at"]},
            {"unit": "%RH"},
            {"threshold": 10},
        )
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    build_candidate_preview(
                        row,
                        observation_id=candidate["observation_ids"][0],
                        source_event_id=candidate["source_event_id"],
                        rule={**rule, **change},
                    )
        with self.assertRaises(ValueError):
            build_candidate_preview(
                row,
                observation_id=candidate["observation_ids"][0],
                source_event_id="not-a-uuid",
                rule=rule,
            )

    def test_rc2_builder_only_copies_agreed_rule_severity(self) -> None:
        rule = self.rc2["rule_fixture"]
        row = process_batch([deepcopy(self.rc2["source_observation"])], 8)[0]
        candidate = self.rc2["candidate"]
        for severity in ("info", "warning", "critical"):
            with self.subTest(severity=severity):
                built = build_candidate_preview(
                    row,
                    observation_id=candidate["observation_ids"][0],
                    source_event_id=candidate["source_event_id"],
                    rule={**rule, "severity": severity},
                )
                self.assertIsNotNone(built)
                self.assertEqual(built["severity"], severity)
        for severity in ("emergency", "normal", "", None, ["warning"]):
            with self.subTest(severity=severity):
                with self.assertRaisesRegex(ValueError, "rule severity"):
                    build_candidate_preview(
                        row,
                        observation_id=candidate["observation_ids"][0],
                        source_event_id=candidate["source_event_id"],
                        rule={**rule, "severity": severity},
                    )

    def test_rc2_builder_refuses_changed_raw_or_risk_evidence(self) -> None:
        source = deepcopy(self.rc2["source_observation"])
        rule = self.rc2["rule_fixture"]
        valid = process_batch([source], rule["threshold"])[0]
        candidate = self.rc2["candidate"]
        changed_raw = deepcopy(valid)
        changed_raw["raw"]["metrics"][0]["value"] = 3
        changed_risk_value = deepcopy(valid)
        changed_risk_value["risk"]["temperature_c"] = 3
        changed_risk_reason = deepcopy(valid)
        changed_risk_reason["risk"]["reason"] = "within_threshold"
        for row in (changed_raw, changed_risk_value, changed_risk_reason):
            with self.subTest(row=row["risk"]):
                with self.assertRaises(ValueError):
                    build_candidate_preview(
                        row,
                        observation_id=candidate["observation_ids"][0],
                        source_event_id=candidate["source_event_id"],
                        rule=rule,
                    )
        self.assertEqual(valid["raw"], source)

    def test_rc2_builder_refuses_changed_raw_identity_or_sample_time(self) -> None:
        source = deepcopy(self.rc2["source_observation"])
        rule = self.rc2["rule_fixture"]
        valid = process_batch([source], rule["threshold"])[0]
        candidate = self.rc2["candidate"]
        changes = (
            ("schema_version", "draft-1"),
            ("device_id", "99999999-9999-4999-8999-999999999999"),
            ("warehouse_id", "99999999-9999-4999-8999-999999999999"),
            ("message_id", "99999999-9999-4999-8999-999999999999"),
            ("sampled_at", "2026-10-14T00:02:00Z"),
        )
        for field, changed in changes:
            with self.subTest(field=field):
                row = deepcopy(valid)
                row["raw"][field] = changed
                with self.assertRaisesRegex(ValueError, "raw .* differs"):
                    build_candidate_preview(
                        row,
                        observation_id=candidate["observation_ids"][0],
                        source_event_id=candidate["source_event_id"],
                        rule=rule,
                    )
        self.assertEqual(valid["raw"], source)

    def test_rc2_builder_keeps_missing_humidity_quality_in_evidence(self) -> None:
        source = deepcopy(self.rc2["source_observation"])
        source["metrics"] = [
            metric for metric in source["metrics"] if metric["key"] == "temperature"
        ]
        rule = self.rc2["rule_fixture"]
        valid = process_batch([source], rule["threshold"])[0]
        self.assertEqual(valid["risk"]["state"], "candidate")
        self.assertIn("missing_metric:humidity", valid["quality"])
        candidate = self.rc2["candidate"]
        built = build_candidate_preview(
            valid,
            observation_id=candidate["observation_ids"][0],
            source_event_id=candidate["source_event_id"],
            rule=rule,
        )
        self.assertIsNotNone(built)
        self.assertEqual(built["evidence"]["quality"], valid["quality"])
        self.assertEqual(built["evidence"]["raw_value"], source["metrics"][0]["value"])

        changed_quality = deepcopy(valid)
        changed_quality["quality"] = ["ok"]
        with self.assertRaisesRegex(ValueError, "processed evidence differs"):
            build_candidate_preview(
                changed_quality,
                observation_id=candidate["observation_ids"][0],
                source_event_id=candidate["source_event_id"],
                rule=rule,
            )

        changed_raw = deepcopy(valid)
        changed_raw["raw"]["metrics"].append(
            {"key": "humidity", "value": 91, "unit": "%RH"}
        )
        with self.assertRaisesRegex(ValueError, "processed evidence differs"):
            build_candidate_preview(
                changed_raw,
                observation_id=candidate["observation_ids"][0],
                source_event_id=candidate["source_event_id"],
                rule=rule,
            )

    def test_rc2_failure_vectors_never_claim_low_risk_or_candidate(self) -> None:
        rc2 = self.rc2
        source = rc2["source_observation"]
        missing = deepcopy(source)
        missing["metrics"][0]["value"] = None
        wrong_unit = deepcopy(source)
        wrong_unit["metrics"][0]["value"] = 104
        wrong_unit["metrics"][0]["unit"] = "°F"
        replay = process_batch([source, deepcopy(source)], 8)[1]
        changed = deepcopy(source)
        changed["metrics"][0]["value"] = 12
        conflict = process_batch([source, changed], 8)[1]
        actual = {
            "missing_temperature": process_batch([missing], 8)[0],
            "wrong_temperature_unit": process_batch([wrong_unit], 8)[0],
            "no_rule": process_batch([source])[0],
            "same_message_replay": replay,
            "same_message_changed_measurement": conflict,
        }
        expected_raw = {
            "missing_temperature": missing,
            "wrong_temperature_unit": wrong_unit,
            "no_rule": source,
            "same_message_replay": source,
            "same_message_changed_measurement": changed,
        }
        self.assertEqual(set(actual), {row["case"] for row in rc2["failure_examples"]})
        for example in rc2["failure_examples"]:
            with self.subTest(case=example["case"]):
                result = actual[example["case"]]
                self.assertEqual(result["risk"]["state"], example["risk_state"])
                self.assertIsNone(example["candidate"])
                if "quality" in example:
                    self.assertEqual(result["quality"], example["quality"])
                if "duplicate" in example:
                    self.assertEqual(result["duplicate"], example["duplicate"])
                if "risk_reason" in example:
                    self.assertEqual(result["risk"]["reason"], example["risk_reason"])
                self.assertEqual(result["raw"], expected_raw[example["case"]])


if __name__ == "__main__":
    unittest.main()
