"""Owned PG: real B history/high acceptance, A gate and saved C window.

Private per-test schemas, not migration evidence or recovery HTTP/real receipts.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.modules.alerts.models import BatchRuleSnapshotCreate
from app.modules.alerts.models.event import (
    AlertEvent,
    AlertStatusTransition,
    CandidateCreate,
)
from app.modules.alerts.services.events import (
    EventNotFound,
    get_alert_status,
    get_event_by_source,
    receive_candidate,
)
from app.modules.alerts.services.rules import create_snapshot
from app.modules.iot import recovery_observation_service as service
from app.modules.iot import recovery_observation_worker as caller
from app.modules.iot.models import Device
from app.modules.iot.telemetry_inbox import claim_next, deliver_claim, enqueue
from app.modules.telemetry.delivery_candidate import (
    RemoteCandidateReceipt,
    deliver_once_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.recovery_progress_candidate import (
    metadata as progress_metadata,
)
from app.modules.telemetry.recovery_progress_candidate import recovery_progress
from tests.api.routes import test_stream_frontier as checks
from tests.modules import test_gated_high_business as high_checks
from tests.modules.test_telemetry_service import settings_for


class LocalBPort:
    """Actual B services on the private PG schema; not HTTP or a mock receipt."""

    def __init__(self, engine):
        self.engine = engine

    def lookup(self, source):
        with Session(self.engine) as session:
            try:
                row = get_event_by_source(session, str(source))
            except EventNotFound:
                return None
            return RemoteCandidateReceipt(
                source,
                "accepted_duplicate",
                row.content_digest.removeprefix("sha256:"),
                get_alert_status(session, row.id).value,
            )

    def send(self, body):
        with Session(self.engine) as session:
            receipt = receive_candidate(session, CandidateCreate.model_validate(body))
            return RemoteCandidateReceipt(
                UUID(receipt.source_event_id),
                receipt.status.value,
                (receipt.content_digest or "").removeprefix("sha256:"),
            )


@unittest.skipUnless(os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") == "1", "owned PG only")
class GatedRecoveryObservationTests(unittest.TestCase):
    def setUp(self):
        high_checks.GatedHighBusinessTests.setUp(self)
        self.high = self.worker
        self.batch = high_checks.GatedHighBusinessTests.configure(self, rule=False)
        with Session(self.store.engine) as session:
            create_snapshot(
                session,
                self.batch,
                BatchRuleSnapshotCreate(
                    version_id=uuid4(),
                    rule_version="recovery-original-v1",
                    effective_start_at=checks.T0 - timedelta(days=1),
                    rules=[
                        {
                            "metric_code": "temperature",
                            "candidate_kind": "temperature_high",
                            "severity": "warning",
                            "threshold_high": 8,
                            "unit": "°C",
                            "recovery": {
                                "comparison": "<=",
                                "threshold": 7,
                                "min_samples": 3,
                            },
                        }
                    ],
                ),
            )
        progress_metadata.create_all(self.store.engine)
        self.addCleanup(progress_metadata.drop_all, self.store.engine)
        AlertEvent.__table__.create(self.store.engine)
        self.addCleanup(AlertEvent.__table__.drop, self.store.engine)
        AlertStatusTransition.__table__.create(self.store.engine)
        self.addCleanup(AlertStatusTransition.__table__.drop, self.store.engine)
        original = self.save(11, 0)
        self.assertEqual(
            self.high.evaluate(original, now=checks.T0).status, "candidate_staged"
        )
        with self.store.engine.connect() as connection:
            self.original = connection.scalar(select(outbox.c.source_event_id))
        self.assertEqual(
            deliver_once_candidate(
                self.store.engine,
                source_event_id=self.original,
                now=checks.T0,
                port=LocalBPort(self.store.engine),
            ),
            "delivered",
        )
        self.worker = caller.GatedRecoveryObservationWorker(
            self.store.engine, frontier_epochs={self.store.device_id: self.store.epoch}
        )

    def save(self, temperature, seconds):
        moment = checks.T0 + timedelta(seconds=seconds)
        frame = checks.inbox_checks.frame(
            self.store,
            extra={
                "sampled_at": moment.isoformat(),
                "metrics": [
                    {"key": "temperature", "value": temperature, "unit": "°C"},
                    {"key": "humidity", "value": 70, "unit": "%RH"},
                ],
            },
        )
        frame = replace(frame, received_at=moment)
        self.assertEqual(
            enqueue(
                self.store.engine, frame, now=moment, frontier_epoch=self.store.epoch
            ).status,
            "queued",
        )
        claimed = claim_next(self.store.engine, now=moment)
        self.assertIsNotNone(claimed)
        saved = deliver_claim(
            self.store.engine,
            claimed,
            self.sink,
            now=moment,
            frontier_epoch=self.store.epoch,
        )
        self.assertEqual(saved.status, "delivered")
        return saved.observation_id

    def progress(self):
        with self.store.engine.connect() as connection:
            return (
                connection.execute(select(recovery_progress)).mappings().one_or_none()
            )

    def rows(self):
        with self.store.engine.connect() as connection:
            return list(connection.execute(select(outbox)).mappings())

    def advance(self, observation):
        return self.worker.advance(
            self.original, observation, now=checks.T0 + timedelta(seconds=60)
        )

    def test_real_b_history_saved_c_window_and_frozen_frontier_commit_together(self):
        samples = [self.save(6, second) for second in (10, 20, 30)]
        for sample in samples[:2]:
            self.assertEqual(self.advance(sample).status, "progress")
        result = self.advance(samples[-1])
        self.assertEqual(result.status, "staged")
        self.assertEqual(
            self.progress()["observation_ids"], [str(value) for value in samples]
        )
        child = [
            row
            for row in self.rows()
            if row["related_source_event_id"] == self.original
        ]
        self.assertEqual(len(child), 1)
        self.assertEqual(child[0]["state"], "pending")
        self.assertEqual(child[0]["frozen_stream_epoch"], self.store.epoch)
        self.assertEqual(
            child[0]["frozen_evidence_revision"],
            checks.state(self.store)["evidence_revision"],
        )
        self.assertEqual(child[0]["body"]["rule_version"], "recovery-original-v1")
        before = checks.state(self.store)["evidence_revision"]
        self.assertEqual(self.advance(samples[-1]).status, "duplicate")
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)

    def test_sql_failure_rolls_back_actual_c_progress_and_a_revision(self):
        sample = self.save(6, 10)
        before = checks.state(self.store)["evidence_revision"]
        real = caller.advance_saved_recovery_observation_on_connection_candidate

        def fail(connection, **kwargs):
            real(connection, **kwargs)
            self.assertIsNone(self.progress())
            raise OperationalError("synthetic", {}, RuntimeError("private value"))

        with patch.object(
            caller,
            "advance_saved_recovery_observation_on_connection_candidate",
            side_effect=fail,
        ):
            result = self.advance(sample)
        self.assertEqual(result.status, "retryable_failure")
        self.assertNotIn("private", result.reason)
        self.assertIsNone(self.progress())
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)

    def test_commit_failure_does_not_return_provisional_progress(self):
        sample = self.save(6, 10)
        before = checks.state(self.store)["evidence_revision"]

        def fail(_connection):
            raise OperationalError("commit", {}, RuntimeError("private"))

        event.listen(self.store.engine, "commit", fail)
        try:
            result = self.advance(sample)
        finally:
            event.remove(self.store.engine, "commit", fail)
        self.assertEqual(result.status, "retryable_failure")
        self.assertIsNone(self.progress())
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)
        # The next real unit must reuse the pool successfully after the failed
        # driver commit, not merely report a conservative failure once.
        self.assertEqual(self.advance(sample).status, "progress")

    def test_wrong_epoch_and_changed_device_kind_do_not_write_window(self):
        sample = self.save(6, 10)
        before = checks.state(self.store)["evidence_revision"]
        wrong = caller.GatedRecoveryObservationWorker(
            self.store.engine, frontier_epochs={self.store.device_id: uuid4()}
        )
        self.assertEqual(
            wrong.advance(self.original, sample, now=checks.T0).status,
            "retryable_failure",
        )
        with self.store.engine.begin() as connection:
            connection.execute(update(Device).values(kind="cooling_actuator"))
        self.assertEqual(self.advance(sample).status, "retryable_failure")
        self.assertIsNone(self.progress())
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)

    def test_incomplete_frontier_rolls_back_third_sample_and_outbox_then_retries(self):
        samples = [self.save(6, second) for second in (10, 20, 30)]
        for sample in samples[:2]:
            self.assertEqual(self.advance(sample).status, "progress")
        pending = checks.inbox_checks.frame(self.store)
        self.assertEqual(checks.admit(self.store, pending).status, "queued")
        before = checks.state(self.store)["evidence_revision"]
        result = self.advance(samples[-1])
        self.assertEqual(
            (result.status, result.reason), ("held", "frontier_incomplete")
        )
        self.assertEqual(len(self.progress()["observation_ids"]), 2)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)
        # Explicit fixture-only terminal receipt, not a claim of C persistence.
        checks.terminal(self.store, pending)
        self.assertEqual(self.advance(samples[-1]).status, "staged")

    def test_limit_one_pages_use_durable_watermark_and_exclude_original(self):
        samples = [self.save(6, second) for second in (10, 20, 30)]
        for expected, status in zip(
            samples, ("progress", "progress", "staged"), strict=True
        ):
            page = self.worker.advance_page(
                self.original, now=checks.T0 + timedelta(seconds=60), limit=1
            )
            self.assertEqual(len(page), 1)
            self.assertEqual(
                (page[0].observation_id, page[0].status), (expected, status)
            )
        self.assertEqual(
            self.worker.advance_page(
                self.original, now=checks.T0 + timedelta(seconds=60), limit=1
            ),
            (),
        )

    def test_explicit_cli_resumes_saved_pg_window_without_delivery(self):
        for second in (10, 20, 30):
            self.save(6, second)
        settings = settings_for(self.store.device_id, self.store.epoch)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "scope.json"
            config.write_text(settings.model_dump_json(), encoding="utf-8")
            args = [
                "recovery-prepare",
                "--config",
                str(config),
                "--original-source-event-id",
                str(self.original),
                "--limit",
                "1",
            ]
            for expected in ("progress", "progress", "staged"):
                captured = io.StringIO()
                with (
                    patch("sys.argv", args),
                    patch.object(
                        service, "create_engine", return_value=self.store.engine
                    ),
                    redirect_stdout(captured),
                ):
                    service.main()
                report = json.loads(captured.getvalue())
                self.assertEqual(report["results"], {expected: 1})
                self.assertIn("not_B_delivery", report["scope"])
        child = [
            row
            for row in self.rows()
            if row["related_source_event_id"] == self.original
        ]
        self.assertEqual(len(child), 1)
        self.assertEqual((child[0]["state"], child[0]["attempt_count"]), ("pending", 0))

    def test_explicit_prepare_rejects_duplicate_originals_before_mutation(self):
        settings = settings_for(self.store.device_id, self.store.epoch)
        before = checks.state(self.store)["evidence_revision"]
        with self.assertRaises(ValueError):
            service.prepare_once(
                self.store.engine,
                settings,
                (self.original, self.original),
                now=checks.T0,
            )
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)
        self.assertIsNone(self.progress())
