"""Real PG private schemas; fixtures are NOT formal migrations or full E1."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError

from app.modules.alerts.models.warehouse import Warehouse
from app.modules.iot.models import Device
from app.modules.iot.telemetry_inbox import claim_next, deliver_claim
from app.modules.iot.telemetry_storage_bridge import GatedTelemetrySink
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.storage_candidate import metadata as c_metadata
from app.modules.telemetry.storage_candidate import observations
from tests.api.routes import test_stream_frontier as checks


@unittest.skipUnless(
    os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") == "1", "strict owned PG only"
)
class GatedTelemetryStorageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = contextmanager(checks.store.__wrapped__)()
        self.store = self.fixture.__enter__()
        self.addCleanup(self.fixture.__exit__, None, None, None)
        c_metadata.create_all(self.store.engine)
        self.addCleanup(c_metadata.drop_all, self.store.engine)
        Warehouse.__table__.create(self.store.engine)
        self.addCleanup(Warehouse.__table__.drop, self.store.engine)
        with self.store.engine.begin() as conn:
            conn.execute(
                Warehouse.__table__.insert().values(
                    id=self.store.warehouse_id,
                    code="bridge-test",
                    name="Bridge test",
                    created_at=checks.T0,
                )
            )
        self.sink = GatedTelemetrySink(
            self.store.engine, frontier_epochs={self.store.device_id: self.store.epoch}
        )

    def frame(self):
        return checks.inbox_checks.frame(
            self.store,
            extra={
                "metrics": [
                    {"key": "temperature", "value": 11, "unit": "°C"},
                    {"key": "humidity", "value": 70, "unit": "%RH"},
                ]
            },
        )

    def count(self):
        with self.store.engine.connect() as conn:
            return conn.scalar(select(func.count()).select_from(observations))

    def test_real_inbox_delivery_commits_c_then_terminal_frontier(self):
        envelope = self.frame()
        self.assertEqual(checks.admit(self.store, envelope).status, "queued")
        claim = claim_next(self.store.engine, now=checks.T0)
        self.assertIsNotNone(claim)
        result = deliver_claim(
            self.store.engine,
            claim,
            self.sink,
            now=checks.T0,
            frontier_epoch=self.store.epoch,
        )
        self.assertEqual(result.status, "delivered")
        self.assertEqual(self.count(), 1)
        self.assertIsNotNone(checks.stamp(self.store))
        replay = self.sink(envelope)
        self.assertEqual(replay.status, "accepted_new")
        self.assertEqual(replay.observation_id, result.observation_id)
        self.assertEqual(self.count(), 1)

    def test_c_failure_rolls_back_observation_and_evidence_revision(self):
        from app.modules.iot import telemetry_storage_bridge as bridge

        real = bridge.ingest_observation_on_connection_candidate

        def fail(conn, envelope):
            real(conn, envelope)
            self.assertEqual(self.count(), 0)
            raise OperationalError("synthetic", {}, RuntimeError("private fault"))

        before = checks.state(self.store)["evidence_revision"]
        with patch.object(
            bridge, "ingest_observation_on_connection_candidate", side_effect=fail
        ):
            result = self.sink(self.frame())
        self.assertEqual(result.status, "retryable_failure")
        self.assertEqual(self.count(), 0)
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)

    def test_commit_failure_never_returns_provisional_success(self):
        def fail(_conn):
            raise OperationalError("commit", {}, RuntimeError("synthetic"))

        event.listen(self.store.engine, "commit", fail)
        try:
            result = self.sink(self.frame())
        finally:
            event.remove(self.store.engine, "commit", fail)
        self.assertEqual(result.status, "retryable_failure")
        self.assertEqual(self.count(), 0)
        self.assertEqual(checks.state(self.store)["evidence_revision"], 0)

    def test_wrong_epoch_or_scope_does_not_write(self):
        wrong = GatedTelemetrySink(
            self.store.engine, frontier_epochs={self.store.device_id: uuid4()}
        )
        self.assertEqual(wrong(self.frame()).status, "retryable_failure")
        self.assertEqual(
            self.sink(replace(self.frame(), authenticated_device_id=uuid4())).status,
            "retryable_failure",
        )
        self.assertEqual(self.count(), 0)
        self.assertEqual(checks.state(self.store)["evidence_revision"], 0)

    def test_changed_binding_and_actuator_refused(self):
        for fields in (
            {"warehouse_id": uuid4()},
            {"warehouse_id": self.store.warehouse_id, "kind": "cooling_actuator"},
        ):
            with self.store.engine.begin() as conn:
                conn.execute(
                    update(Device)
                    .where(Device.id == self.store.device_id)
                    .values(**fields)
                )
            self.assertEqual(self.sink(self.frame()).status, "retryable_failure")
        self.assertEqual(self.count(), 0)

    def test_missing_real_warehouse_refused(self):
        from sqlalchemy import delete

        with self.store.engine.begin() as conn:
            conn.execute(
                delete(Warehouse).where(Warehouse.id == self.store.warehouse_id)
            )
        self.assertEqual(self.sink(self.frame()).status, "retryable_failure")
        self.assertEqual(self.count(), 0)

    def test_incomplete_quality_commits_raw_but_holds_recovery(self):
        result = self.sink(checks.inbox_checks.frame(self.store))
        self.assertEqual(result.status, "accepted_new")
        self.assertEqual(self.count(), 1)
        self.assertEqual(
            checks.state(self.store)["gap_reason"], "unsettled_bad_evidence"
        )

    def test_scope_is_copied_and_bounded(self):
        scope = {self.store.device_id: self.store.epoch}
        sink = GatedTelemetrySink(self.store.engine, frontier_epochs=scope)
        scope.clear()
        self.assertEqual(sink(self.frame()).status, "accepted_new")
        for bad in (
            {},
            {"device": self.store.epoch},
            {uuid4(): uuid4() for _ in range(15)},
        ):
            with self.assertRaises(ValueError):
                GatedTelemetrySink(self.store.engine, frontier_epochs=bad)

    def test_same_device_pg_lock_blocks_c_write_until_release(self):
        from app.modules.iot.stream_frontier import begin_evidence_write

        entered = Event()

        def seen(_conn, _cursor, statement, _params, _context, _many):
            if "iot_stream_frontier" in statement and "FOR UPDATE" in statement:
                entered.set()

        writer = self.store.engine.connect()
        transaction = writer.begin()
        begin_evidence_write(
            writer,
            device_id=self.store.device_id,
            warehouse_id=self.store.warehouse_id,
            epoch=self.store.epoch,
        )
        event.listen(self.store.engine, "before_cursor_execute", seen)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.sink, self.frame())
                try:
                    self.assertTrue(entered.wait(3))
                    self.assertEqual(self.count(), 0)
                    self.assertFalse(future.done())
                finally:
                    transaction.rollback()
                self.assertEqual(future.result(timeout=6).status, "accepted_new")
        finally:
            event.remove(self.store.engine, "before_cursor_execute", seen)
            if transaction.is_active:
                transaction.rollback()
            writer.close()
        self.assertEqual(self.count(), 1)

    def test_conflicting_same_message_keeps_original_raw_and_holds(self):
        envelope = self.frame()
        first = self.sink(envelope)
        changed = replace(
            envelope,
            reception_id=uuid4(),
            raw_text=envelope.raw_text.replace('"value": 11', '"value": 12'),
        )
        result = self.sink(changed)
        self.assertEqual(result.status, "conflict")
        self.assertEqual(result.observation_id, first.observation_id)
        with self.store.engine.connect() as conn:
            raw = conn.scalar(select(observations.c.raw_text))
        self.assertEqual(raw, envelope.raw_text)
        self.assertEqual(self.count(), 1)
        self.assertEqual(
            checks.state(self.store)["gap_reason"], "unsettled_bad_evidence"
        )

    def recovery_fixture(self):
        """Actual A/C ingestion, synthetic outbox business state; no B/send."""
        observation_ids = []
        for _ in range(2):
            envelope = self.frame()
            self.assertEqual(checks.admit(self.store, envelope).status, "queued")
            claim = claim_next(self.store.engine, now=checks.T0)
            result = deliver_claim(
                self.store.engine,
                claim,
                self.sink,
                now=checks.T0,
                frontier_epoch=self.store.epoch,
            )
            self.assertEqual(result.status, "delivered")
            observation_ids.append(result.observation_id)
        original_id, recovery_id = uuid4(), uuid4()
        with self.store.engine.begin() as conn:
            for source_id, observation_id, kind, related, status in (
                (
                    original_id,
                    observation_ids[0],
                    "temperature_high",
                    None,
                    "delivered",
                ),
                (
                    recovery_id,
                    observation_ids[1],
                    "temperature_recovered",
                    original_id,
                    "pending",
                ),
            ):
                body = {
                    "source_event_id": str(source_id),
                    "kind": kind,
                    "warehouse_id": str(self.store.warehouse_id),
                    "observation_ids": [str(observation_id)],
                }
                if related is not None:
                    body["related_source_event_id"] = str(related)
                conn.execute(
                    outbox.insert().values(
                        source_event_id=source_id,
                        related_source_event_id=related,
                        observation_id=observation_id,
                        body_sha256="0" * 64,
                        body=body,
                        state=status,
                        attempt_count=0,
                        next_attempt_at_utc=checks.T0,
                    )
                )
        return original_id, recovery_id

    def freeze(self, conn, original_id, recovery_id, stamp):
        from app.modules.telemetry.recovery_outbox_candidate import (
            freeze_recovery_frontier_on_connection_candidate,
        )

        return freeze_recovery_frontier_on_connection_candidate(
            conn,
            original_source_event_id=original_id,
            recovery_source_event_id=recovery_id,
            stamp=stamp,
        )

    def gate(self, conn):
        from app.modules.iot.stream_frontier import begin_evidence_write

        return begin_evidence_write(
            conn,
            device_id=self.store.device_id,
            warehouse_id=self.store.warehouse_id,
            epoch=self.store.epoch,
        )

    def frozen_revision(self, recovery_id):
        with self.store.engine.connect() as conn:
            return conn.scalar(
                select(outbox.c.frozen_evidence_revision).where(
                    outbox.c.source_event_id == recovery_id
                )
            )

    def test_real_frontier_freeze_rollback_commit_and_same_unit_replay(self):
        original, recovery = self.recovery_fixture()
        before = checks.state(self.store)["evidence_revision"]
        with self.store.engine.connect() as writer:
            transaction = writer.begin()
            self.gate(writer)
            stamp = checks.stamp(self.store, writer)
            self.assertEqual(self.freeze(writer, original, recovery, stamp), "frozen")
            self.assertEqual(
                self.freeze(writer, original, recovery, stamp), "duplicate"
            )
            self.assertIsNone(self.frozen_revision(recovery))
            transaction.rollback()
        self.assertEqual(checks.state(self.store)["evidence_revision"], before)
        self.assertIsNone(self.frozen_revision(recovery))
        with self.store.engine.begin() as writer:
            revision = self.gate(writer)
            stamp = checks.stamp(self.store, writer)
            self.assertEqual(stamp.evidence_revision, revision)
            self.assertEqual(self.freeze(writer, original, recovery, stamp), "frozen")
        # A fresh connection reads only the committed frozen stamp.
        self.assertEqual(self.frozen_revision(recovery), before + 1)

    def test_real_freeze_holds_device_lock_and_new_write_invalidates_stamp(self):
        original, recovery = self.recovery_fixture()
        entered = Event()

        def seen(_conn, _cursor, statement, _params, _context, _many):
            if "iot_stream_frontier" in statement and "FOR UPDATE" in statement:
                entered.set()

        with self.store.engine.connect() as writer:
            transaction = writer.begin()
            self.gate(writer)
            stamp = checks.stamp(self.store, writer)
            self.assertEqual(self.freeze(writer, original, recovery, stamp), "frozen")
            event.listen(self.store.engine, "before_cursor_execute", seen)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.sink, self.frame())
                    try:
                        self.assertTrue(entered.wait(3))
                        self.assertFalse(future.done())
                        self.assertEqual(self.count(), 2)
                        self.assertIsNone(self.frozen_revision(recovery))
                    finally:
                        transaction.commit()
                    self.assertEqual(future.result(timeout=6).status, "accepted_new")
            finally:
                event.remove(self.store.engine, "before_cursor_execute", seen)
                if transaction.is_active:
                    transaction.rollback()
        self.assertGreater(
            checks.state(self.store)["evidence_revision"], stamp.evidence_revision
        )
        with self.store.engine.begin() as conn:
            with self.assertRaisesRegex(ValueError, "incomplete or changed"):
                self.freeze(conn, original, recovery, stamp)
        self.assertEqual(self.frozen_revision(recovery), stamp.evidence_revision)

    def test_real_bad_evidence_blocks_unfrozen_recovery(self):
        original, recovery = self.recovery_fixture()
        stamp = checks.stamp(self.store)
        self.assertEqual(
            self.sink(checks.inbox_checks.frame(self.store)).status, "accepted_new"
        )
        self.assertIsNone(checks.stamp(self.store))
        with self.store.engine.begin() as conn:
            with self.assertRaisesRegex(ValueError, "incomplete or changed"):
                self.freeze(conn, original, recovery, stamp)
        self.assertIsNone(self.frozen_revision(recovery))

    def test_real_wrong_epoch_refused_without_frozen_write(self):
        from app.modules.iot.stream_frontier import FrontierUnavailable

        original, recovery = self.recovery_fixture()
        stamp = replace(checks.stamp(self.store), stream_epoch=uuid4())
        with self.store.engine.begin() as conn:
            with self.assertRaises(FrontierUnavailable):
                self.freeze(conn, original, recovery, stamp)
        self.assertIsNone(self.frozen_revision(recovery))

    def test_real_new_gate_cannot_overwrite_previously_frozen_stamp(self):
        original, recovery = self.recovery_fixture()
        with self.store.engine.begin() as conn:
            self.gate(conn)
            first = checks.stamp(self.store, conn)
            self.assertEqual(self.freeze(conn, original, recovery, first), "frozen")
        with self.store.engine.begin() as conn:
            self.gate(conn)
            changed = checks.stamp(self.store, conn)
            with self.assertRaisesRegex(ValueError, "different or partial"):
                self.freeze(conn, original, recovery, changed)
        self.assertEqual(self.frozen_revision(recovery), first.evidence_revision)


if __name__ == "__main__":
    unittest.main()
