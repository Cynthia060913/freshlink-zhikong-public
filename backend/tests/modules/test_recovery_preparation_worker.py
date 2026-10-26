"""Owned PG preparation scheduler: no network transport or notification port."""

import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import insert, update
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.modules.alerts.models import BatchRuleSnapshotCreate
from app.modules.alerts.services.rules import create_snapshot
from app.modules.iot import recovery_observation_service as service
from app.modules.iot.recovery_preparation_worker import RecoveryPreparationWorker
from app.modules.iot.stream_frontier import (
    FrontierUnavailable,
    begin_evidence_write,
    mark_uncertain,
)
from app.modules.telemetry.delivery_candidate import deliver_once_candidate
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.recovery_freshness_candidate import (
    check_recovery_freshness_candidate,
)
from app.modules.telemetry.storage_candidate import observations
from tests.api.routes import test_stream_frontier as checks
from tests.modules import test_gated_high_business as high_checks
from tests.modules import test_gated_recovery_observation as recovery_checks
from tests.modules.test_telemetry_service import settings_for


@unittest.skipUnless(os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") == "1", "owned PG only")
class RecoveryPreparationTests(unittest.TestCase):
    save = recovery_checks.GatedRecoveryObservationTests.save
    rows = recovery_checks.GatedRecoveryObservationTests.rows
    progress = recovery_checks.GatedRecoveryObservationTests.progress
    advance = recovery_checks.GatedRecoveryObservationTests.advance

    def setUp(self):
        recovery_checks.GatedRecoveryObservationTests.setUp(self)
        self.settings = settings_for(self.store.device_id, self.store.epoch)
        self.watch = RecoveryPreparationWorker(self.store.engine, self.settings)
        self.now = checks.T0 + timedelta(minutes=1)

    def child(self):
        return next(
            r for r in self.rows() if r["related_source_event_id"] == self.original
        )

    def test_saved_samples_background_prepare_then_restart_renews_not_rewrites(self):
        for second in (10, 20, 30):
            self.save(6, second)
        result = self.watch.run_once(now=self.now)
        self.assertEqual(result["observation_staged"], 1)
        before = self.child()
        self.assertEqual((before["state"], before["attempt_count"]), ("pending", 0))
        self.save(6, 40)
        restarted = RecoveryPreparationWorker(self.store.engine, self.settings)
        result = restarted.run_once(now=self.now)
        self.assertEqual(result["frontier_refrozen"], 1)
        after = self.child()
        for field in (
            "source_event_id",
            "body",
            "body_sha256",
            "state",
            "attempt_count",
        ):
            self.assertEqual(before[field], after[field])
        self.assertGreater(after["frozen_admitted_seq"], before["frozen_admitted_seq"])
        self.assertEqual(
            service.check_ready_once(
                self.store.engine, self.settings, (after["source_event_id"],)
            ),
            {"ready_at_check": 1},
        )

    def test_new_high_stays_held_and_never_mutates_stable_body(self):
        for second in (10, 20, 30):
            self.save(6, second)
        self.watch.run_once(now=self.now)
        before = self.child()
        self.save(11, 40)
        result = self.watch.run_once(now=self.now)
        self.assertEqual(result["frontier_held"], 1)
        after = self.child()
        for field in (
            "body",
            "body_sha256",
            "state",
            "attempt_count",
            "frozen_admitted_seq",
        ):
            self.assertEqual(before[field], after[field])

    def test_saved_bad_raw_is_not_promoted_to_ready_by_background_watch(self):
        for second in (10, 20, 30):
            self.save(6, second)
        self.watch.run_once(now=self.now)
        before = self.child()
        newest = self.save(6, 40)
        with self.store.engine.begin() as connection:
            connection.execute(
                update(observations)
                .where(observations.c.id == newest)
                .values(raw_text="invalid synthetic JSON")
            )
        result = self.watch.run_once(now=self.now)
        self.assertEqual(result["frontier_held"], 1)
        after = self.child()
        self.assertEqual(after["frozen_admitted_seq"], before["frozen_admitted_seq"])
        self.assertEqual((after["state"], after["attempt_count"]), ("pending", 0))

    def test_revision_only_does_not_hide_bad_saved_raw_without_new_admission(self):
        for second in (10, 20, 30):
            self.save(6, second)
        self.watch.run_once(now=self.now)
        before = self.child()
        with self.store.engine.begin() as connection:
            begin_evidence_write(
                connection,
                device_id=self.store.device_id,
                warehouse_id=self.store.warehouse_id,
                epoch=self.store.epoch,
            )
            connection.execute(
                update(observations)
                .where(observations.c.id == before["observation_id"])
                .values(raw_text="invalid synthetic JSON")
            )
        result = self.watch.run_once(now=self.now)
        self.assertEqual(result["frontier_held"], 1)
        self.assertEqual(
            self.child()["frozen_evidence_revision"], before["frozen_evidence_revision"]
        )
        self.assertEqual(self.child()["attempt_count"], 0)

    def test_revision_only_does_not_clear_uncertainty_without_new_admission(self):
        for second in (10, 20, 30):
            self.save(6, second)
        self.watch.run_once(now=self.now)
        before = self.child()
        with self.store.engine.begin() as connection:
            begin_evidence_write(
                connection,
                device_id=self.store.device_id,
                warehouse_id=self.store.warehouse_id,
                epoch=self.store.epoch,
            )
            mark_uncertain(
                connection, device_id=self.store.device_id, epoch=self.store.epoch
            )
        result = self.watch.run_once(now=self.now)
        self.assertEqual(result["frontier_held"], 1)
        self.assertTrue(checks.state(self.store)["gap_reason"])
        self.assertEqual(
            self.child()["frozen_evidence_revision"], before["frozen_evidence_revision"]
        )

    def test_two_real_pg_preparers_keep_one_stable_window_without_uncertainty(self):
        for second in (10, 20, 30):
            self.save(6, second)
        barrier = Barrier(2)

        def prepare():
            candidate = RecoveryPreparationWorker(self.store.engine, self.settings)
            barrier.wait(timeout=10)
            return candidate.run_once(now=self.now)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(prepare) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)
        children = [
            r for r in self.rows() if r["related_source_event_id"] == self.original
        ]
        self.assertEqual(len(children), 1)
        self.assertEqual(
            (children[0]["state"], children[0]["attempt_count"]), ("pending", 0)
        )
        self.assertEqual(
            service.check_ready_once(
                self.store.engine, self.settings, (children[0]["source_event_id"],)
            ),
            {"ready_at_check": 1},
        )

    def test_leased_or_retried_work_is_not_refrozen_or_reclaimed(self):
        for second in (10, 20, 30):
            self.save(6, second)
        self.watch.run_once(now=self.now)
        before = self.child()
        self.save(6, 40)
        for state, attempt, token in (("leased", 1, uuid4()), ("pending", 2, None)):
            with self.store.engine.begin() as connection:
                connection.execute(
                    update(outbox)
                    .where(outbox.c.source_event_id == before["source_event_id"])
                    .values(state=state, attempt_count=attempt, lease_token=token)
                )
            result = self.watch.run_once(now=self.now)
            self.assertFalse(any(k.startswith("frontier_") for k in result))
            after = self.child()
            self.assertEqual(
                after["frozen_admitted_seq"], before["frozen_admitted_seq"]
            )
            self.assertEqual(
                (after["state"], after["attempt_count"], after["lease_token"]),
                (state, attempt, token),
            )

    def test_noop_tick_does_not_change_revision_and_delivered_child_is_excluded(self):
        for second in (10, 20, 30):
            self.save(6, second)
        self.watch.run_once(now=self.now)
        before = checks.state(self.store)["evidence_revision"]
        self.watch.run_once(now=self.now)
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)
        with self.store.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == self.child()["source_event_id"])
                .values(state="delivered")
            )
        self.assertEqual(self.watch.run_once(now=self.now), {})

    def test_round_robin_bounded_discovery_does_not_starve_held_originals(self):
        original = self.rows()[0]
        ids = [self.original]
        # Synthetic extra originals test discovery fairness, not business validity.
        with self.store.engine.begin() as connection:
            for _ in range(30):
                identity = uuid4()
                ids.append(identity)
                clone = dict(original)
                clone.update(
                    source_event_id=identity,
                    body={**original["body"], "source_event_id": str(identity)},
                )
                connection.execute(insert(outbox).values(**clone))
        seen = set()
        for _ in range(3):
            page = self.watch._originals()
            self.assertLessEqual(len(page), 14)
            self.assertEqual(len(page), len(set(page)))
            seen.update(page)
            self.watch._cursor = page[-1]
        self.assertEqual(seen, set(ids))

    def test_actual_two_batch_originals_both_gain_valid_windows_without_first_batch_choice(
        self,
    ):
        batch = high_checks.GatedHighBusinessTests.configure(self, rule=False)
        with Session(self.store.engine) as session:
            create_snapshot(
                session,
                batch,
                BatchRuleSnapshotCreate(
                    version_id=uuid4(),
                    rule_version="second-batch-recovery-v1",
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
        observation = self.save(12, 5)
        result = self.high.evaluate(observation, now=self.now)
        self.assertEqual(len(result.source_event_ids), 2)
        for source in result.source_event_ids:
            self.assertEqual(
                deliver_once_candidate(
                    self.store.engine,
                    source_event_id=source,
                    now=self.now,
                    port=recovery_checks.LocalBPort(self.store.engine),
                ),
                "delivered",
            )
        for second in (10, 20, 30):
            self.save(6, second)
        # Preparing another original legitimately advances the common stream
        # revision. A later bounded tick must renew every earlier window too.
        first_tick = self.watch.run_once(now=self.now)
        second_tick = self.watch.run_once(now=self.now)
        children = [
            r
            for r in self.rows()
            if r["related_source_event_id"] in result.source_event_ids
        ]
        self.assertEqual(len(children), 2)
        self.assertEqual(
            {r["body"]["batch_id"] for r in children}, {str(self.batch), str(batch)}
        )
        for child in children:
            self.assertEqual((child["state"], child["attempt_count"]), ("pending", 0))
            detail = check_recovery_freshness_candidate(
                self.store.engine,
                recovery_source_event_id=child["source_event_id"],
                device_id=self.store.device_id,
                warehouse_id=self.store.warehouse_id,
                stream_epoch=self.store.epoch,
            )
            self.assertEqual(
                service.check_ready_once(
                    self.store.engine, self.settings, (child["source_event_id"],)
                ),
                {"ready_at_check": 1},
                (detail, first_tick, second_tick, checks.state(self.store), child),
            )

    def test_foreign_scope_rejected_before_any_window_write(self):
        self.watch.settings = settings_for(self.store.device_id, uuid4())
        before = checks.state(self.store)["evidence_revision"]
        with self.assertRaises(FrontierUnavailable):
            self.watch.run_once(now=self.now)
        self.assertIsNone(self.progress())
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)

    def test_loop_retries_storage_failure_then_honors_stop(self):
        calls = []

        def tick():
            calls.append(1)
            if len(calls) == 1:
                raise OperationalError("synthetic", {}, RuntimeError("private"))
            self.watch.stop_requested.set()
            return {"synthetic_tick": 1}

        with patch.object(self.watch, "run_once", side_effect=tick):
            result = self.watch.run(max_seconds=1, poll_seconds=0.1)
        self.assertEqual(result, {"retryable_cycle": 1, "synthetic_tick": 1})
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.watch.run(max_seconds=1), {})

    def test_invalid_loop_duration_does_not_start(self):
        for duration in (True, 0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                self.watch.run(max_seconds=duration)
        for poll in (True, 0, 11, float("nan")):
            with self.assertRaises(ValueError):
                self.watch.run(max_seconds=1, poll_seconds=poll)

    def test_explicit_watch_cli_prepares_pending_attempt_zero(self):
        for second in (10, 20, 30):
            self.save(6, second)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "scope.json"
            config.write_text(self.settings.model_dump_json(), encoding="utf-8")
            captured = io.StringIO()
            with (
                patch(
                    "sys.argv",
                    [
                        "prepare",
                        "--config",
                        str(config),
                        "--watch-preparation",
                        "--max-seconds",
                        "0.05",
                    ],
                ),
                patch.object(service, "create_engine", return_value=self.store.engine),
                redirect_stdout(captured),
            ):
                service.main()
            report = json.loads(captured.getvalue())
            self.assertIn("not_B_delivery", report["scope"])
            self.assertEqual(report["devices_scoped"], 1)
        self.assertEqual(
            (self.child()["state"], self.child()["attempt_count"]), ("pending", 0)
        )
