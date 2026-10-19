"""Isolated C/B rule-selection adapter checks; no shared app or real B database."""

import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import OperationalError

from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.rule_evaluation_candidate import evaluate_one_batch_candidate
from app.modules.telemetry.storage_candidate import (
    evaluation_jobs,
    ingest_observation,
    metadata,
    observations,
)

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
BATCH = UUID("33333333-3333-4333-8333-333333333333")
SNAPSHOT = UUID("88888888-8888-4888-8888-888888888888")
NOW = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Reception:
    raw_text: str
    reception_id: UUID
    authenticated_device_id: UUID = DEVICE
    warehouse_id: UUID = WAREHOUSE
    received_at: datetime = NOW

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


class Selector:
    def __init__(self, reply: dict | Exception):
        self.reply = reply
        self.calls: list[dict] = []

    def select_rules_for_observation(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _rule(**changes: object) -> dict:
    entry = {
        "batch_id": str(BATCH),
        "warehouse_id": str(WAREHOUSE),
        "rule_version": "temp-v1",
        "snapshot_id": str(SNAPSHOT),
        "metric_code": "temperature",
        "unit": "°C",
        "kind": "temperature_high",
        "severity": "warning",
        "comparison": ">",
        "threshold": 8.0,
        "recovery": {"consecutive_samples": 3},
        "effective_start_at": "2026-10-14T00:00:00Z",
        "effective_end_at": None,
        "source": "warehouse-manager-ui",
    }
    entry.update(changes)
    return {"status": "configured", "rules": [entry], "reason_code": None}


class RuleEvaluationCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "candidate.db"
        self.engine = create_engine(f"sqlite:///{self.path}")
        metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def _ingest(self, temperature: float, *, humidity: bool = True) -> tuple[UUID, str]:
        metrics = [{"key": "temperature", "value": temperature, "unit": "°C"}]
        if humidity:
            metrics.append({"key": "humidity", "value": 75, "unit": "%RH"})
        raw = json.dumps(
            {
                "schema_version": "1.0",
                "device_id": str(DEVICE),
                "message_id": str(uuid4()),
                "sampled_at": "2026-10-15T00:00:00Z",
                "metrics": metrics,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result = ingest_observation(self.engine, Reception(raw, uuid4()))
        self.assertEqual(result.status, "accepted_new")
        assert result.observation_id is not None
        return result.observation_id, raw

    def _read(self, table) -> list[dict]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(table)).mappings()]

    def test_hot_missing_humidity_stages_once_without_losing_raw_or_quality(
        self,
    ) -> None:
        observation_id, raw = self._ingest(13, humidity=False)
        selector = Selector(_rule())
        result = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=selector,
            now=NOW,
        )
        self.assertEqual(result.status, "candidate_staged")
        self.assertEqual(selector.calls[0]["batch_ids"], (BATCH,))
        self.assertEqual(selector.calls[0]["warehouse_id"], WAREHOUSE)
        self.assertEqual(selector.calls[0]["sampled_at"], NOW)
        row = self._read(observations)[0]
        self.assertEqual(row["raw_text"], raw)
        self.assertEqual(row["quality"], ["missing_metric:humidity"])
        self.assertEqual(row["risk"]["state"], "candidate")
        sent = self._read(outbox)[0]
        self.assertEqual(sent["source_event_id"], result.source_event_id)
        self.assertEqual(sent["body"]["evidence"]["quality"], row["quality"])
        self.assertEqual(sent["body"]["evidence"]["raw_value"], 13)
        self.assertEqual(sent["body"]["rule_snapshot_id"], str(SNAPSHOT))
        self.assertEqual(self._read(evaluation_jobs)[0]["state"], "candidate_staged")
        again = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=selector,
            now=NOW,
        )
        self.assertEqual(again.status, "not_due")
        self.assertEqual(len(self._read(outbox)), 1)

    def test_no_rule_keeps_unknown_and_normal_needs_complete_quality(self) -> None:
        no_rule_id, _ = self._ingest(13)
        no_rule = Selector({"status": "no_rule", "rules": [], "reason_code": None})
        result = evaluate_one_batch_candidate(
            self.engine,
            observation_id=no_rule_id,
            batch_id=BATCH,
            selector=no_rule,
            now=NOW,
        )
        self.assertEqual(result.status, "no_rule")
        self.assertEqual(self._read(observations)[0]["risk"]["state"], "unknown")

        normal_id, _ = self._ingest(7)
        self.assertEqual(
            evaluate_one_batch_candidate(
                self.engine,
                observation_id=normal_id,
                batch_id=BATCH,
                selector=Selector(_rule()),
                now=NOW,
            ).status,
            "within_rule",
        )
        incomplete_id, _ = self._ingest(7, humidity=False)
        held = evaluate_one_batch_candidate(
            self.engine,
            observation_id=incomplete_id,
            batch_id=BATCH,
            selector=Selector(_rule()),
            now=NOW,
        )
        self.assertEqual(held.status, "held_unknown")
        rows = {row["id"]: row for row in self._read(observations)}
        self.assertEqual(rows[normal_id]["risk"]["state"], "normal")
        self.assertEqual(rows[incomplete_id]["risk"]["state"], "unknown")
        self.assertEqual(self._read(outbox), [])

    def test_transient_selection_failure_retries_and_same_event_survives_job_retry(
        self,
    ) -> None:
        observation_id, _ = self._ingest(13)
        failure = Selector(
            OperationalError("SELECT rules", {}, RuntimeError("synthetic"))
        )
        first = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=failure,
            now=NOW,
        )
        self.assertEqual(first.status, "retryable_failure")
        self.assertEqual(self._read(evaluation_jobs)[0]["state"], "pending")
        self.assertEqual(self._read(outbox), [])
        selector = Selector(_rule())
        staged = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=selector,
            now=NOW,
        )
        self.assertEqual(staged.status, "candidate_staged")
        body = self._read(outbox)[0]["body"]
        # Simulate a crash after durable staging but before job completion.
        with self.engine.begin() as connection:
            connection.execute(
                update(evaluation_jobs)
                .where(evaluation_jobs.c.observation_id == observation_id)
                .values(state="pending")
            )
        selector.reply = _rule(
            snapshot_id=str(uuid4()), rule_version="retroactive-v2", threshold=9.0
        )
        retried = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=selector,
            now=NOW,
        )
        self.assertEqual(retried.source_event_id, staged.source_event_id)
        self.assertEqual(len(selector.calls), 1)
        self.assertEqual(self._read(outbox)[0]["body"], body)
        self.assertEqual(len(self._read(outbox)), 1)

    def test_invalid_or_mismatched_rule_cannot_mark_high_as_normal(self) -> None:
        observation_id, raw = self._ingest(13)
        for selection in (
            {"status": "invalid", "rules": [], "reason_code": "unknown_batch"},
            _rule(warehouse_id=str(uuid4())),
            _rule(unit="°F"),
            _rule(severity="urgent"),
            _rule(effective_end_at="2026-10-15T00:00:00Z"),
        ):
            result = evaluate_one_batch_candidate(
                self.engine,
                observation_id=observation_id,
                batch_id=BATCH,
                selector=Selector(selection),
                now=NOW,
            )
            self.assertEqual(result.status, "held_unknown", selection)
        row = self._read(observations)[0]
        self.assertEqual(row["raw_text"], raw)
        self.assertEqual(row["risk"]["state"], "unknown")
        self.assertEqual(self._read(outbox), [])

    def test_invalid_time_or_temperature_unit_never_reaches_rule_selector(self) -> None:
        selector = Selector(_rule())
        for bad_field in (
            {"sampled_at": "not-a-time"},
            {"metrics": [{"key": "temperature", "value": 13, "unit": "°F"}]},
        ):
            raw = json.dumps(
                {
                    "schema_version": "1.0",
                    "device_id": str(DEVICE),
                    "message_id": str(uuid4()),
                    "sampled_at": "2026-10-15T00:00:00Z",
                    "metrics": [{"key": "temperature", "value": 13, "unit": "°C"}],
                    **bad_field,
                },
                ensure_ascii=False,
            )
            result = ingest_observation(self.engine, Reception(raw, uuid4()))
            self.assertEqual(result.status, "accepted_new")
            assert result.observation_id is not None
            checked = evaluate_one_batch_candidate(
                self.engine,
                observation_id=result.observation_id,
                batch_id=BATCH,
                selector=selector,
                now=NOW,
            )
            self.assertEqual(checked.status, "not_due")
        self.assertEqual(selector.calls, [])
        self.assertEqual(self._read(outbox), [])
        for row in self._read(observations):
            self.assertEqual(row["risk"]["state"], "unknown")
