"""C isolated outbox persistence; never sends to B or touches shared tables."""

import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.modules.telemetry.outbox_candidate import (
    claim_candidate,
    finish_candidate,
    outbox,
    outbox_conflicts,
    stage_candidate,
)
from app.modules.telemetry.storage_candidate import ingest_observation, metadata

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Reception:
    raw_text: str
    reception_id: UUID
    received_at: datetime = NOW
    authenticated_device_id: UUID = DEVICE
    warehouse_id: UUID = WAREHOUSE

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


class CandidateOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "outbox.db"
        self.engine: Engine = create_engine(f"sqlite:///{self.path}")
        metadata.create_all(self.engine)
        payload = {
            "schema_version": "1.0",
            "device_id": str(DEVICE),
            "message_id": str(uuid4()),
            "sampled_at": "2026-10-17T00:00:00Z",
            "metrics": [{"key": "temperature", "value": 11, "unit": "°C"}],
        }
        observation = ingest_observation(
            self.engine,
            Reception(json.dumps(payload, ensure_ascii=False), uuid4()),
        )
        self.assertEqual(observation.status, "accepted_new")
        assert observation.observation_id is not None
        self.observation_id = observation.observation_id
        self.source_event_id = uuid4()
        self.body = {
            "source_event_id": str(self.source_event_id),
            "observation_ids": [str(self.observation_id)],
            "kind": "temperature_high",
            "evidence": {"temperature_c": 11, "quality": ["ok"]},
        }

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def _stage(self, body: dict | None = None) -> str:
        return stage_candidate(
            self.engine,
            source_event_id=self.source_event_id,
            observation_id=self.observation_id,
            body=self.body if body is None else body,
            now=NOW,
        )

    def _row(self):
        with self.engine.connect() as connection:
            return connection.execute(select(outbox)).mappings().one()

    def test_body_and_source_are_immutable_across_duplicate_conflict_and_reopen(
        self,
    ) -> None:
        self.assertEqual(self._stage(), "staged")
        self.assertEqual(self._stage(), "duplicate")
        changed = json.loads(json.dumps(self.body))
        changed["evidence"]["temperature_c"] = 12
        self.assertEqual(self._stage(changed), "conflict")
        self.assertEqual(self._stage(changed), "conflict")
        with self.engine.connect() as connection:
            rejected = connection.execute(select(outbox_conflicts)).mappings().one()
            attempts = connection.scalar(
                select(func.count()).select_from(outbox_conflicts)
            )
        self.assertEqual(attempts, 1)
        self.assertEqual(rejected["body"]["evidence"]["temperature_c"], 12)
        self.body["evidence"]["temperature_c"] = 99
        self.assertEqual(self._row()["body"]["evidence"]["temperature_c"], 11)
        self.engine.dispose()
        self.engine = create_engine(f"sqlite:///{self.path}")
        row = self._row()
        self.assertEqual(row["source_event_id"], self.source_event_id)
        self.assertEqual(row["observation_id"], self.observation_id)
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["attempt_count"], 0)

    def test_timeout_retries_same_body_and_stale_lease_cannot_finish(self) -> None:
        self.assertEqual(self._stage(), "staged")
        first = claim_candidate(
            self.engine, source_event_id=self.source_event_id, now=NOW
        )
        assert first is not None
        self.assertEqual(first.attempt_count, 1)
        self.assertEqual(first.body, self.body)
        self.assertIsNone(
            claim_candidate(self.engine, source_event_id=self.source_event_id, now=NOW)
        )
        self.engine.dispose()
        self.engine = create_engine(f"sqlite:///{self.path}")
        later = NOW + timedelta(seconds=31)
        second = claim_candidate(
            self.engine, source_event_id=self.source_event_id, now=later
        )
        assert second is not None
        self.assertEqual(second.attempt_count, 2)
        self.assertEqual(second.body, first.body)
        self.assertNotEqual(second.lease_token, first.lease_token)
        self.assertFalse(
            finish_candidate(
                self.engine, claim=first, disposition="delivered", now=later
            )
        )
        self.assertTrue(
            finish_candidate(
                self.engine,
                claim=second,
                disposition="retry",
                error_code="b_timeout_unresolved",
                retry_after_seconds=60,
                now=later,
            )
        )
        self.assertIsNone(
            claim_candidate(
                self.engine,
                source_event_id=self.source_event_id,
                now=later + timedelta(seconds=59),
            )
        )
        third = claim_candidate(
            self.engine,
            source_event_id=self.source_event_id,
            now=later + timedelta(seconds=60),
        )
        assert third is not None
        self.assertEqual(third.attempt_count, 3)
        self.assertEqual(third.body, first.body)
        self.assertTrue(
            finish_candidate(
                self.engine, claim=third, disposition="delivered", now=later
            )
        )
        self.assertEqual(self._row()["state"], "delivered")
        self.assertIsNone(
            claim_candidate(
                self.engine,
                source_event_id=self.source_event_id,
                now=later + timedelta(hours=1),
            )
        )

    def test_permanent_rejection_is_retained_without_more_claims(self) -> None:
        self._stage()
        claim = claim_candidate(
            self.engine, source_event_id=self.source_event_id, now=NOW
        )
        assert claim is not None
        self.assertTrue(
            finish_candidate(
                self.engine,
                claim=claim,
                disposition="rejected",
                error_code="invalid_candidate",
                now=NOW,
            )
        )
        self.assertEqual(self._row()["last_error_code"], "invalid_candidate")
        self.assertEqual(self._row()["state"], "rejected")
        self.assertIsNone(
            claim_candidate(
                self.engine,
                source_event_id=self.source_event_id,
                now=NOW + timedelta(hours=1),
            )
        )

    def test_conflict_audit_failure_does_not_report_success(self) -> None:
        self.assertEqual(self._stage(), "staged")
        changed = {**self.body, "kind": "humidity_high"}

        def fail_audit(_conn, _cursor, statement, _params, _context, _many):
            if "INSERT INTO telemetry_candidate_outbox_conflict_candidate" in statement:
                raise OperationalError(statement, {}, RuntimeError("synthetic failure"))

        event.listen(self.engine, "before_cursor_execute", fail_audit)
        try:
            with self.assertRaises(OperationalError):
                self._stage(changed)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_audit)
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(outbox_conflicts)),
                0,
            )
        self.assertEqual(self._row()["body"]["kind"], "temperature_high")
        self.assertEqual(self._stage(changed), "conflict")

    def test_invalid_body_and_retry_parameters_fail_before_writing(self) -> None:
        with self.assertRaisesRegex(ValueError, "source_event_id mismatch"):
            self._stage({**self.body, "source_event_id": str(uuid4())})
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            self._stage({**self.body, "evidence": {"temperature_c": float("nan")}})
        with self.assertRaisesRegex(ValueError, "reference its observation"):
            self._stage({**self.body, "observation_ids": []})
        self.assertEqual(self._stage(), "staged")
        with self.assertRaisesRegex(ValueError, "lease_seconds"):
            claim_candidate(
                self.engine,
                source_event_id=self.source_event_id,
                now=NOW,
                lease_seconds=0,
            )
        claim = claim_candidate(
            self.engine, source_event_id=self.source_event_id, now=NOW
        )
        assert claim is not None
        with self.assertRaisesRegex(ValueError, "requires error_code"):
            finish_candidate(self.engine, claim=claim, disposition="retry", now=NOW)
        self.assertEqual(self._row()["state"], "leased")


if __name__ == "__main__":
    unittest.main()
