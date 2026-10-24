"""C atomic multi-batch unit tests; A gate is mocked, not real PostgreSQL."""

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4, uuid5

from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError

from app.modules.telemetry.multi_batch_candidate import (
    stage_gated_multi_batch_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.rule_evaluation_candidate import _SOURCE_NAMESPACE
from app.modules.telemetry.storage_candidate import (
    evaluation_jobs,
    ingest_observation,
    metadata,
    observations,
)

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
FIRST = UUID("33333333-3333-4333-8333-333333333333")
SECOND = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 10, 15, tzinfo=UTC)


class Reception:
    def __init__(self, raw: dict):
        self.raw_payload = raw
        self.raw_text = json.dumps(raw, ensure_ascii=False)
        self.reception_id = uuid4()
        self.authenticated_device_id = DEVICE
        self.warehouse_id = WAREHOUSE
        self.received_at = NOW


def rule(batch: UUID, threshold: float) -> dict:
    return {
        "batch_id": str(batch),
        "warehouse_id": str(WAREHOUSE),
        "snapshot_id": str(uuid4()),
        "rule_version": f"{batch}-v1",
        "metric_code": "temperature",
        "kind": "threshold_high",
        "comparison": ">",
        "severity": "warning",
        "threshold": threshold,
        "unit": "°C",
        "effective_start_at": "2026-10-14T00:00:00Z",
        "effective_end_at": None,
    }


class MultiBatchCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temporary.name) / 'multi.db'}"
        )
        metadata.create_all(self.engine)
        self.epoch = uuid4()

    def tearDown(self):
        self.engine.dispose()
        self.temporary.cleanup()

    def ingest(self, *, temperature=13, humidity=True):
        metrics = [{"key": "temperature", "value": temperature, "unit": "°C"}]
        if humidity:
            metrics.append({"key": "humidity", "value": 75, "unit": "%RH"})
        result = ingest_observation(
            self.engine,
            Reception(
                {
                    "schema_version": "1.0",
                    "device_id": str(DEVICE),
                    "message_id": str(uuid4()),
                    "sampled_at": "2026-10-15T00:00:00Z",
                    "metrics": metrics,
                }
            ),
        )
        self.assertEqual(result.status, "accepted_new")
        return result.observation_id

    def stage(self, observation, rules=None, batches=(FIRST, SECOND)):
        if rules is None:
            rules = [rule(FIRST, 8), rule(SECOND, 20)]

        def gate(connection, **_kwargs):
            # The real A gate updates its revision before C's savepoints.
            # SQLite needs a DML statement to begin that outer DB transaction.
            connection.exec_driver_sql(
                "UPDATE telemetry_evaluation_candidate SET state=state WHERE 0"
            )
            return 1

        with patch(
            "app.modules.telemetry.multi_batch_candidate.begin_evidence_write",
            side_effect=gate,
        ):
            return stage_gated_multi_batch_candidate(
                self.engine,
                device_id=DEVICE,
                warehouse_id=WAREHOUSE,
                stream_epoch=self.epoch,
                observation_id=observation,
                batch_ids=batches,
                selected_rules=rules,
                now=NOW,
            )

    def rows(self, table):
        with self.engine.connect() as connection:
            return list(connection.execute(select(table)).mappings())

    def test_high_and_normal_commit_together_and_replay_is_noop(self):
        observation = self.ingest()
        result = self.stage(observation)
        self.assertEqual(result.status, "candidate_staged")
        self.assertEqual(
            result.source_event_ids,
            (uuid5(_SOURCE_NAMESPACE, f"{observation}:{FIRST}:temperature_high"),),
        )
        self.assertEqual(len(self.rows(outbox)), 1)
        risk = self.rows(observations)[0]["risk"]
        self.assertEqual(risk["state"], "candidate")
        self.assertEqual(
            {item["batch_id"]: item["state"] for item in risk["batches"]},
            {str(FIRST): "candidate", str(SECOND): "normal"},
        )
        self.assertEqual(self.rows(evaluation_jobs)[0]["state"], "candidate_staged")
        self.assertEqual(self.stage(observation).status, "not_due")
        self.assertEqual(len(self.rows(outbox)), 1)

    def test_two_high_batches_stage_two_distinct_sources(self):
        observation = self.ingest()
        result = self.stage(observation, rules=[rule(FIRST, 8), rule(SECOND, 9)])
        self.assertEqual(result.status, "candidate_staged")
        self.assertEqual(len(result.source_event_ids), 2)
        self.assertEqual(len(set(result.source_event_ids)), 2)
        self.assertEqual(len(self.rows(outbox)), 2)

    def test_missing_rule_or_bad_unit_keeps_whole_job_pending(self):
        observation = self.ingest()
        for rules in (
            [rule(FIRST, 8)],
            [rule(FIRST, 8), {**rule(SECOND, 20), "unit": "°F"}],
        ):
            self.assertEqual(
                self.stage(observation, rules=rules).status, "held_unknown"
            )
            self.assertEqual(self.rows(outbox), [])
            self.assertEqual(self.rows(evaluation_jobs)[0]["state"], "pending")
            self.assertEqual(self.rows(observations)[0]["risk"]["state"], "unknown")

    def test_missing_humidity_never_marks_second_batch_normal(self):
        observation = self.ingest(humidity=False)
        result = self.stage(observation)
        self.assertEqual(result.status, "candidate_staged")
        states = {
            item["batch_id"]: item["state"]
            for item in self.rows(observations)[0]["risk"]["batches"]
        }
        self.assertEqual(states[str(FIRST)], "candidate")
        self.assertEqual(states[str(SECOND)], "unknown")
        self.assertEqual(len(self.rows(outbox)), 1)

    def test_failure_after_first_batch_rolls_back_everything_then_retries(self):
        from app.modules.telemetry import multi_batch_candidate as module

        observation = self.ingest()
        actual = module.stage_f03_on_connection_candidate
        count = 0

        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise OperationalError("synthetic", {}, RuntimeError("private"))
            return actual(*args, **kwargs)

        with patch.object(
            module, "stage_f03_on_connection_candidate", side_effect=fail_second
        ):
            with self.assertRaises(OperationalError):
                self.stage(observation, rules=[rule(FIRST, 8), rule(SECOND, 9)])
        self.assertEqual(self.rows(outbox), [])
        self.assertEqual(self.rows(evaluation_jobs)[0]["state"], "pending")
        self.assertEqual(self.rows(observations)[0]["risk"]["state"], "unknown")
        self.assertEqual(
            self.stage(observation, rules=[rule(FIRST, 8), rule(SECOND, 9)]).status,
            "candidate_staged",
        )
        self.assertEqual(len(self.rows(outbox)), 2)


if __name__ == "__main__":
    unittest.main()
