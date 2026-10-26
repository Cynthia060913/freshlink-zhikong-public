"""Owned PostgreSQL recovery claim/commit, B acceptance, rollback and restart."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError

from app.modules.iot.recovery_delivery_worker import RecoveryDeliveryWorker
from app.modules.iot.recovery_preparation_worker import RecoveryPreparationWorker
from app.modules.iot.stream_frontier import FrontierUnavailable
from app.modules.iot.stream_frontier_models import streams
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.storage_candidate import observations
from tests.api.routes import test_stream_frontier as checks
from tests.modules import test_gated_recovery_observation as fixtures
from tests.modules.test_telemetry_service import settings_for


def test_unconfirmed_recovery_does_not_touch_database_or_transport():
    engine, port = MagicMock(), MagicMock()
    sender = RecoveryDeliveryWorker(engine, MagicMock(), port)
    assert sender.run_once() == {}
    assert not engine.mock_calls and not port.mock_calls


def test_recovery_role_requires_its_own_confirmation_before_config_or_database():
    from app.modules.iot.telemetry_service import main

    with (
        patch(
            "sys.argv",
            ["telemetry_service", "--role", "deliver-recovery", "--config", "missing"],
        ),
        patch("app.modules.iot.telemetry_service.create_engine") as create,
    ):
        with unittest.TestCase().assertRaises(SystemExit) as failure:
            main()
        assert failure.exception.code == 2
        create.assert_not_called()


@unittest.skipUnless(os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") == "1", "owned PG only")
class RecoveryDeliveryTests(unittest.TestCase):
    save = fixtures.GatedRecoveryObservationTests.save
    rows = fixtures.GatedRecoveryObservationTests.rows

    def setUp(self):
        fixtures.GatedRecoveryObservationTests.setUp(self)
        self.settings = settings_for(self.store.device_id, self.store.epoch)
        self.now = checks.T0 + timedelta(minutes=1)
        for second in (10, 20, 30):
            self.save(6, second)
        RecoveryPreparationWorker(self.store.engine, self.settings).run_once(
            now=self.now
        )
        self.source = next(
            row["source_event_id"]
            for row in self.rows()
            if row["related_source_event_id"] == self.original
        )
        self.port = fixtures.LocalBPort(self.store.engine)
        self.sender = RecoveryDeliveryWorker(
            self.store.engine, self.settings, self.port, contract_confirmed=True
        )

    def child(self):
        return next(row for row in self.rows() if row["source_event_id"] == self.source)

    def test_committed_claim_lock_free_send_readback_and_restart(self):
        owner = self

        class NoDeviceLockPort(fixtures.LocalBPort):
            def send(self, body):
                with self.engine.begin() as connection:
                    connection.execute(
                        select(streams).with_for_update(nowait=True)
                    ).all()
                owner.assertEqual(owner.child()["state"], "leased")
                return super().send(body)

        self.sender.port = NoDeviceLockPort(self.store.engine)
        before = self.child()
        self.assertEqual(self.sender.run_once(now=self.now), {"delivered": 1})
        after = self.child()
        self.assertEqual((after["state"], after["attempt_count"]), ("delivered", 1))
        self.assertEqual(
            (after["body"], after["body_sha256"]),
            (before["body"], before["body_sha256"]),
        )
        self.assertEqual(self.port.lookup(self.original).alert_status, "resolved")
        self.assertEqual(
            RecoveryDeliveryWorker(
                self.store.engine, self.settings, self.port, contract_confirmed=True
            ).run_once(now=self.now),
            {},
        )

    def test_lookup_failure_backoff_survives_worker_restart(self):
        unavailable = MagicMock()
        unavailable.lookup.side_effect = ConnectionError("synthetic unavailable")
        self.sender.port = unavailable
        self.assertEqual(self.sender.run_once(now=self.now), {"retry": 1})
        unavailable.send.assert_not_called()
        self.assertEqual(self.sender.run_once(now=self.now), {})
        restarted = RecoveryDeliveryWorker(
            self.store.engine, self.settings, self.port, contract_confirmed=True
        )
        self.assertEqual(
            restarted.run_once(now=self.now + timedelta(seconds=31)), {"delivered": 1}
        )
        self.assertEqual(self.child()["attempt_count"], 2)

    def test_lost_post_reply_uses_same_source_receipt(self):
        class LostReplyPort(fixtures.LocalBPort):
            def send(self, body):
                super().send(body)
                raise TimeoutError("synthetic committed response loss")

        self.sender.port = LostReplyPort(self.store.engine)
        self.assertEqual(self.sender.run_once(now=self.now), {"delivered": 1})
        self.assertEqual(self.child()["attempt_count"], 1)
        self.assertEqual(self.port.lookup(self.original).alert_status, "resolved")

    def test_wrong_epoch_never_consumes_attempt_or_contacts_b(self):
        self.sender.settings = settings_for(self.store.device_id, uuid4())
        self.sender.port = MagicMock()
        with self.assertRaises(FrontierUnavailable):
            self.sender.run_once(now=self.now)
        self.assertEqual(self.child()["attempt_count"], 0)
        self.assertFalse(self.sender.port.mock_calls)

    def test_invalid_raw_held_without_attempt(self):
        with self.store.engine.begin() as connection:
            connection.execute(
                update(observations)
                .where(observations.c.id == self.child()["observation_id"])
                .values(raw_text="invalid synthetic JSON")
            )
        self.sender.port = MagicMock()
        self.assertEqual(self.sender.run_once(now=self.now), {"held": 1})
        self.assertEqual(self.child()["attempt_count"], 0)
        self.assertFalse(self.sender.port.mock_calls)

    def test_claim_commit_failure_rolls_back_and_sends_nothing(self):
        def fail_claim_commit(connection):
            state = connection.scalar(
                select(outbox.c.state).where(outbox.c.source_event_id == self.source)
            )
            if state == "leased":
                raise OperationalError(
                    "synthetic claim commit", {}, RuntimeError("synthetic failure")
                )

        self.sender.port = MagicMock()
        event.listen(self.store.engine, "commit", fail_claim_commit)
        try:
            with self.assertRaises(OperationalError):
                self.sender.run_once(now=self.now)
        finally:
            event.remove(self.store.engine, "commit", fail_claim_commit)
        self.assertEqual(
            (self.child()["state"], self.child()["attempt_count"]), ("pending", 0)
        )
        self.assertFalse(self.sender.port.mock_calls)

    def test_two_schedulers_share_single_database_claim(self):
        barrier = Barrier(2)

        def run():
            sender = RecoveryDeliveryWorker(
                self.store.engine, self.settings, self.port, contract_confirmed=True
            )
            barrier.wait(timeout=5)
            return sender.run_once(now=self.now)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(sum(result.get("delivered", 0) for result in results), 1)
        self.assertEqual(
            (self.child()["state"], self.child()["attempt_count"]), ("delivered", 1)
        )
