"""Owned PG: real B rule selection and C staging, not external delivery."""

import os
import unittest
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.modules.alerts.models import (
    Batch,
    BatchCreate,
    BatchRuleSnapshot,
    BatchRuleSnapshotCreate,
)
from app.modules.alerts.services.batches import create_batch
from app.modules.alerts.services.rules import create_snapshot
from app.modules.iot.high_evaluation_worker import GatedHighWorker
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.storage_candidate import evaluation_jobs, observations
from tests.api.routes import test_stream_frontier as checks
from tests.modules import test_gated_telemetry_storage as bridge_checks


@unittest.skipUnless(
    os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") == "1", "strict owned PG only"
)
class GatedHighBusinessTests(unittest.TestCase):
    def setUp(self):
        bridge_checks.GatedTelemetryStorageTests.setUp(self)
        Batch.__table__.create(self.store.engine)
        self.addCleanup(Batch.__table__.drop, self.store.engine)
        BatchRuleSnapshot.__table__.create(self.store.engine)
        self.addCleanup(BatchRuleSnapshot.__table__.drop, self.store.engine)
        self.worker = GatedHighWorker(
            self.store.engine, frontier_epochs={self.store.device_id: self.store.epoch}
        )

    def saved(self):
        frame = bridge_checks.GatedTelemetryStorageTests.frame(self)
        result = self.sink(frame)
        self.assertEqual(result.status, "accepted_new")
        return result.observation_id

    def configure(self, *, rule=True, threshold=8, kind="temperature_high"):
        with Session(self.store.engine) as session:
            batch = create_batch(
                session,
                self.store.warehouse_id,
                BatchCreate(
                    code="batch-" + uuid4().hex[:8],
                    product="apple",
                    started_at=checks.T0 - timedelta(days=1),
                ),
            )
            batch_id = batch.id
            if rule:
                create_snapshot(
                    session,
                    batch_id,
                    BatchRuleSnapshotCreate(
                        version_id=uuid4(),
                        rule_version="actual-b-v1",
                        effective_start_at=checks.T0 - timedelta(days=1),
                        rules=[
                            {
                                "metric_code": "temperature",
                                "candidate_kind": kind,
                                "severity": "warning",
                                "threshold_high": threshold,
                                "unit": "°C",
                            }
                        ],
                    ),
                )
        return batch_id

    def rows(self):
        with self.store.engine.connect() as conn:
            return list(conn.execute(select(outbox)).mappings())

    def pending(self, observation_id):
        with self.store.engine.connect() as conn:
            return conn.scalar(
                select(evaluation_jobs.c.state).where(
                    evaluation_jobs.c.observation_id == observation_id
                )
            )

    def test_actual_b_rule_stages_c_under_gate_and_replay_does_not_write(self):
        batch = self.configure()
        observation = self.saved()
        before = checks.state(self.store)["evidence_revision"]
        result = self.worker.evaluate(observation, now=checks.T0)
        self.assertEqual(result.status, "candidate_staged")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["body"]["batch_id"], str(batch))
        self.assertEqual(rows[0]["body"]["rule_version"], "actual-b-v1")
        self.assertEqual(rows[0]["body"]["evidence"]["threshold"], 8)
        self.assertEqual(checks.state(self.store)["evidence_revision"], before + 1)
        self.assertEqual(
            self.worker.evaluate(observation, now=checks.T0).status, "not_due"
        )
        self.assertEqual(checks.state(self.store)["evidence_revision"], before + 1)

    def test_zero_and_multiple_batches_remain_pending_without_silent_first_batch(self):
        observation = self.saved()
        for count in (0, 2):
            if count:
                self.configure()
                self.configure()
            result = self.worker.evaluate(observation, now=checks.T0)
            self.assertEqual(result.reason, "one_active_batch_required")
            self.assertEqual(self.pending(observation), "pending")
            self.assertEqual(self.rows(), [])

    def test_no_rule_remains_pending_then_valid_non_high_finishes_under_gate(self):
        batch = self.configure(rule=False)
        observation = self.saved()
        self.assertEqual(
            self.worker.evaluate(observation, now=checks.T0).reason, "no_rule"
        )
        with Session(self.store.engine) as session:
            create_snapshot(
                session,
                batch,
                BatchRuleSnapshotCreate(
                    version_id=uuid4(),
                    rule_version="not-high",
                    effective_start_at=checks.T0 - timedelta(days=1),
                    rules=[
                        {
                            "metric_code": "temperature",
                            "candidate_kind": "temperature_high",
                            "severity": "warning",
                            "threshold_high": 20,
                            "unit": "°C",
                        }
                    ],
                ),
            )
        before = checks.state(self.store)["evidence_revision"]
        self.assertEqual(
            self.worker.evaluate(observation, now=checks.T0).status, "within_rule"
        )
        self.assertEqual(self.pending(observation), "within_rule")
        self.assertEqual(checks.state(self.store)["evidence_revision"], before + 1)
        self.assertEqual(
            self.worker.evaluate(observation, now=checks.T0).status, "not_due"
        )
        self.assertEqual(checks.state(self.store)["evidence_revision"], before + 1)
        self.assertEqual(self.rows(), [])

    def test_canonical_f02_kind_stages_high_instead_of_silently_holding(self):
        self.configure(kind="threshold_high")
        observation = self.saved()
        self.assertEqual(
            self.worker.evaluate(observation, now=checks.T0).status, "candidate_staged"
        )
        self.assertEqual(self.rows()[0]["body"]["kind"], "temperature_high")

    def test_normal_path_failure_rolls_back_job_and_evidence_revision(self):
        from app.modules.telemetry import rule_evaluation_candidate as c

        self.configure(threshold=20)
        observation = self.saved()
        before = checks.state(self.store)["evidence_revision"]
        with patch.object(
            c,
            "mark_evaluation_on_connection_candidate",
            side_effect=OperationalError("synthetic", {}, RuntimeError("private")),
        ):
            self.assertEqual(
                self.worker.evaluate(observation, now=checks.T0).status,
                "retryable_failure",
            )
        self.assertEqual(self.pending(observation), "pending")
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)
        self.assertEqual(self.rows(), [])

    def test_rule_read_failure_preserves_pending_and_revision(self):
        self.configure()
        observation = self.saved()
        before = checks.state(self.store)["evidence_revision"]
        with patch(
            "app.modules.iot.high_evaluation_worker.select_rules_for_observation",
            side_effect=OperationalError("synthetic", {}, RuntimeError("private")),
        ):
            result = self.worker.evaluate(observation, now=checks.T0)
        self.assertEqual(result.status, "retryable_failure")
        self.assertNotIn("private", result.reason)
        self.assertEqual(self.pending(observation), "pending")
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)
        self.assertEqual(self.rows(), [])

    def test_staging_failure_rolls_back_c_and_a_revision(self):
        from app.modules.telemetry import rule_evaluation_candidate as c

        self.configure()
        observation = self.saved()
        before = checks.state(self.store)["evidence_revision"]
        original = c.stage_evaluated_high_on_connection_candidate

        def fail(connection, **kwargs):
            original(connection, **kwargs)
            raise OperationalError("synthetic", {}, RuntimeError("private"))

        with patch.object(
            c, "stage_evaluated_high_on_connection_candidate", side_effect=fail
        ):
            result = self.worker.evaluate(observation, now=checks.T0)
        self.assertEqual(result.status, "retryable_failure")
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.pending(observation), "pending")
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)

    def test_wrong_epoch_does_not_stage(self):
        self.configure()
        observation = self.saved()
        wrong = GatedHighWorker(
            self.store.engine, frontier_epochs={self.store.device_id: uuid4()}
        )
        self.assertEqual(
            wrong.evaluate(observation, now=checks.T0).status, "retryable_failure"
        )
        self.assertEqual(self.rows(), [])

    def test_changed_raw_quality_is_held_without_outbox_write(self):
        self.configure()
        observation = self.saved()
        with self.store.engine.begin() as conn:
            conn.execute(
                update(observations)
                .where(observations.c.id == observation)
                .values(normalized={})
            )
        self.assertEqual(
            self.worker.evaluate(observation, now=checks.T0).status, "held_unknown"
        )
        self.assertEqual(self.rows(), [])

    def test_pending_scan_is_explicit_and_bounded(self):
        self.configure()
        observation = self.saved()
        results = self.worker.run_once(now=checks.T0, limit=1)
        self.assertEqual([row.observation_id for row in results], [observation])
        self.assertEqual(results[0].status, "candidate_staged")
        for limit in (0, 101, True):
            with self.assertRaises(ValueError):
                self.worker.run_once(now=checks.T0, limit=limit)
