"""Owned PG A scheduler -> C lease -> real B service; not terminal delivery."""

import os
import unittest
from datetime import timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy import update

from app.modules.iot.correction_delivery_worker import CorrectionDeliveryWorker
from app.modules.iot.stream_frontier import FrontierUnavailable
from app.modules.telemetry.data_quality_correction_candidate import (
    stage_late_reception_corrections_on_connection_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox
from tests.api.routes import test_stream_frontier as checks
from tests.modules import test_gated_recovery_observation as recovery_checks
from tests.modules.test_telemetry_service import settings_for


def test_unconfirmed_scheduler_does_not_access_database_or_port():
    engine, port = MagicMock(), MagicMock()
    worker = CorrectionDeliveryWorker(engine, MagicMock(), port)
    assert worker.run_once() == {}
    engine.connect.assert_not_called()
    port.lookup.assert_not_called()
    port.send.assert_not_called()


@unittest.skipUnless(os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") == "1", "owned PG only")
class CorrectionSchedulerTests(unittest.TestCase):
    save = recovery_checks.GatedRecoveryObservationTests.save

    def setUp(self):
        recovery_checks.GatedRecoveryObservationTests.setUp(self)
        self.now = checks.T0 + timedelta(minutes=1)
        self.settings = settings_for(self.store.device_id, self.store.epoch)
        self.port = recovery_checks.LocalBPort(self.store.engine)
        with self.store.engine.begin() as connection:
            staged = stage_late_reception_corrections_on_connection_candidate(
                connection,
                evidence_kind="reception",
                evidence_id=uuid4(),
                receipt_sha256="a" * 64,
                outcome="invalid",
                reason_code="invalid_json",
                received_at_utc=self.now.isoformat(),
                device_id=self.store.device_id,
                warehouse_id=self.store.warehouse_id,
            )
        self.assertEqual(len(staged), 1)
        self.source = staged[0].source_event_id
        self.sender = CorrectionDeliveryWorker(
            self.store.engine, self.settings, self.port, contract_confirmed=True
        )

    def row(self):
        return next(
            row
            for row in recovery_checks.GatedRecoveryObservationTests.rows(self)
            if row["source_event_id"] == self.source
        )

    def test_claim_deliver_and_new_worker_does_not_resend(self):
        before = self.row()
        self.assertEqual(self.sender.run_once(now=self.now), {"delivered": 1})
        after = self.row()
        self.assertEqual((after["state"], after["attempt_count"]), ("delivered", 1))
        self.assertEqual(after["body"], before["body"])
        self.assertEqual(after["body_sha256"], before["body_sha256"])
        restarted = CorrectionDeliveryWorker(
            self.store.engine, self.settings, self.port, contract_confirmed=True
        )
        self.assertEqual(restarted.run_once(now=self.now), {})
        self.assertIsNotNone(self.port.lookup(self.source))

    def test_lookup_unavailable_retries_only_after_persisted_backoff(self):
        unavailable = MagicMock()
        unavailable.lookup.side_effect = ConnectionError("private transport detail")
        self.sender.port = unavailable
        self.assertEqual(self.sender.run_once(now=self.now), {"retry": 1})
        unavailable.send.assert_not_called()
        self.assertEqual(self.sender.run_once(now=self.now), {})
        restarted = CorrectionDeliveryWorker(
            self.store.engine, self.settings, self.port, contract_confirmed=True
        )
        self.assertEqual(
            restarted.run_once(now=self.now + timedelta(seconds=31)), {"delivered": 1}
        )
        self.assertEqual(self.row()["attempt_count"], 2)

    def test_wrong_epoch_does_not_claim_or_contact_b(self):
        self.sender.settings = settings_for(self.store.device_id, uuid4())
        self.sender.port = MagicMock()
        with self.assertRaises(FrontierUnavailable):
            self.sender.run_once(now=self.now)
        self.assertEqual(self.row()["attempt_count"], 0)
        self.sender.port.lookup.assert_not_called()

    def test_active_lease_is_not_reclaimed(self):
        with self.store.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == self.source)
                .values(
                    state="leased",
                    lease_token=uuid4(),
                    attempt_count=1,
                    lease_until_utc=self.now + timedelta(seconds=30),
                )
            )
        self.sender.port = MagicMock()
        self.assertEqual(self.sender.run_once(now=self.now), {})
        self.sender.port.lookup.assert_not_called()

    def test_invalid_internal_body_remains_held_without_b_request(self):
        before = self.row()
        with self.store.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == self.source)
                .values(body={**before["body"], "evidence": {}})
            )
        self.sender.port = MagicMock()
        self.assertEqual(self.sender.run_once(now=self.now), {"held": 1})
        self.assertEqual(self.row()["attempt_count"], 0)
        self.sender.port.send.assert_not_called()
