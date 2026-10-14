"""C-only F03 proposal vector; no B receiver, outbox, or delivery is exercised."""

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from app.modules.telemetry.quality import process_batch

_EXAMPLES = Path(__file__).resolve().parents[3] / "docs" / "modules" / "C" / "examples"


class CandidateContractProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proposal = json.loads(
            (_EXAMPLES / "candidate-f03-rc1-proposal.json").read_text(encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
