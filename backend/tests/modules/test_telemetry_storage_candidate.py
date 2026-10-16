"""Isolated SQLite transaction checks; not PostgreSQL or app integration."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError

from app.modules.telemetry.storage_candidate import (
    evaluation_jobs,
    ingest_observation,
    metadata,
    observations,
    reception_reuse_conflicts,
    receptions,
)

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
MESSAGE = UUID("44444444-4444-4444-8444-444444444444")
TIME = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Reception:
    raw_text: str
    reception_id: UUID
    received_at: datetime = TIME
    authenticated_device_id: UUID = DEVICE
    warehouse_id: UUID = WAREHOUSE

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


def _raw(**changes: object) -> dict:
    payload = {
        "schema_version": "1.0",
        "device_id": str(DEVICE),
        "message_id": str(MESSAGE),
        "sampled_at": "2026-10-15T00:00:00Z",
        "metrics": [
            {"key": "temperature", "value": 11, "unit": "°C"},
            {"key": "humidity", "value": 70, "unit": "%RH"},
        ],
    }
    payload.update(changes)
    return payload


def _reception(payload: dict, **changes: object) -> Reception:
    return Reception(
        raw_text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        reception_id=changes.pop("reception_id", uuid4()),
        **changes,
    )


class StorageCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "telemetry.db"
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def _rows(self, table) -> list[dict]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(table)).mappings()]

    def test_new_observation_retains_exact_hot_raw_and_trusted_receipt(self) -> None:
        payload = _raw(received_at="1900-01-01T00:00:00Z")
        receipt = _reception(payload)
        result = ingest_observation(self.engine, receipt)

        self.assertEqual(result.status, "accepted_new")
        self.assertIsNotNone(result.observation_id)
        stored = self._rows(observations)[0]
        self.assertEqual(stored["raw_text"], receipt.raw_text)
        self.assertEqual(stored["raw_payload"], payload)
        self.assertEqual(stored["normalized"]["metrics"]["temperature"], 11)
        self.assertEqual(stored["risk"]["state"], "unknown")
        self.assertEqual(stored["risk"]["reason"], "no_threshold_rule")
        self.assertEqual(
            self._rows(receptions)[0]["received_at_utc"], "2026-10-15T00:00:00Z"
        )
        self.assertEqual(self._rows(evaluation_jobs)[0]["state"], "pending")

    def test_redelivery_and_same_reception_retry_are_idempotent_across_reopen(
        self,
    ) -> None:
        first = _reception(_raw())
        created = ingest_observation(self.engine, first)
        self.engine.dispose()
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        repeated_call = ingest_observation(self.engine, first)
        redelivery = _reception(_raw(), received_at=TIME + timedelta(minutes=1))
        duplicated = ingest_observation(self.engine, redelivery)

        self.assertEqual(repeated_call, created)
        self.assertEqual(duplicated.status, "accepted_duplicate")
        self.assertEqual(duplicated.observation_id, created.observation_id)
        self.assertEqual(len(self._rows(observations)), 1)
        self.assertEqual(len(self._rows(receptions)), 2)
        self.assertEqual(len(self._rows(evaluation_jobs)), 1)
        self.assertNotEqual(self._rows(receptions)[0]["raw_text"], "")

    def test_same_message_changed_content_is_conflict_and_preserves_original(
        self,
    ) -> None:
        first = _reception(_raw())
        created = ingest_observation(self.engine, first)
        changed = _raw(extra="changed")
        conflict = ingest_observation(self.engine, _reception(changed))

        self.assertEqual(conflict.status, "conflict")
        self.assertEqual(conflict.observation_id, created.observation_id)
        self.assertEqual(self._rows(observations)[0]["raw_text"], first.raw_text)
        self.assertEqual(len(self._rows(receptions)), 2)
        self.assertEqual(self._rows(receptions)[1]["outcome"], "conflict")
        self.assertEqual(len(self._rows(evaluation_jobs)), 1)

    def test_reused_reception_id_with_different_raw_is_explicit_conflict(self) -> None:
        original = _reception(_raw())
        ingest_observation(self.engine, original)
        changed = _raw(extra="changed")
        tampered = _reception(changed, reception_id=original.reception_id)
        result = ingest_observation(self.engine, tampered)
        self.assertEqual(result.status, "conflict")
        self.assertEqual(result.reason, "reception_id_reuse")
        self.assertEqual(len(self._rows(receptions)), 1)
        self.assertEqual(self._rows(receptions)[0]["raw_text"], original.raw_text)
        attempt = self._rows(reception_reuse_conflicts)[0]
        self.assertEqual(attempt["claimed_reception_id"], original.reception_id)
        self.assertEqual(attempt["raw_text"], tampered.raw_text)
        self.assertEqual(attempt["device_id"], DEVICE)
        self.assertEqual(attempt["warehouse_id"], WAREHOUSE)

        self.assertEqual(ingest_observation(self.engine, tampered), result)
        self.assertEqual(len(self._rows(reception_reuse_conflicts)), 1)
        second_attempt = _reception(
            _raw(extra="another"), reception_id=original.reception_id
        )
        self.assertEqual(
            ingest_observation(self.engine, second_attempt).status, "conflict"
        )
        self.assertEqual(
            {row["raw_text"] for row in self._rows(reception_reuse_conflicts)},
            {tampered.raw_text, second_attempt.raw_text},
        )
        self.engine.dispose()
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        self.assertEqual(len(self._rows(reception_reuse_conflicts)), 2)
        self.assertEqual(len(self._rows(observations)), 1)
        self.assertEqual(len(self._rows(evaluation_jobs)), 1)

    def test_reused_reception_audit_write_failure_returns_retryable(self) -> None:
        original = _reception(_raw())
        self.assertEqual(
            ingest_observation(self.engine, original).status, "accepted_new"
        )
        tampered = _reception(_raw(extra="changed"), reception_id=original.reception_id)
        broken = False

        def fail_audit(_conn, _cursor, statement, _params, _context, _many):
            nonlocal broken
            if (
                not broken
                and "INSERT INTO telemetry_reception_reuse_candidate" in statement
            ):
                broken = True
                raise OperationalError(statement, {}, RuntimeError("synthetic failure"))

        event.listen(self.engine, "before_cursor_execute", fail_audit)
        try:
            failed = ingest_observation(self.engine, tampered)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_audit)
        self.assertTrue(broken)
        self.assertEqual(failed.status, "retryable_failure")
        self.assertEqual(self._rows(reception_reuse_conflicts), [])
        self.assertEqual(ingest_observation(self.engine, tampered).status, "conflict")
        self.assertEqual(len(self._rows(reception_reuse_conflicts)), 1)

    def test_bad_time_and_unit_are_retained_as_unknown(self) -> None:
        for payload in (
            _raw(sampled_at="not-a-time"),
            _raw(metrics=[{"key": "temperature", "value": 11, "unit": "°F"}]),
            _raw(metrics=[{"key": "humidity", "value": 70, "unit": "%RH"}]),
        ):
            with self.subTest(payload=payload):
                payload["message_id"] = str(uuid4())
                receipt = _reception(payload)
                result = ingest_observation(self.engine, receipt)
                self.assertEqual(result.status, "accepted_new")
                row = next(
                    item
                    for item in self._rows(observations)
                    if item["id"] == result.observation_id
                )
                self.assertEqual(row["raw_text"], receipt.raw_text)
                self.assertEqual(row["risk"]["state"], "unknown")
                self.assertEqual(
                    next(
                        item
                        for item in self._rows(evaluation_jobs)
                        if item["observation_id"] == result.observation_id
                    )["state"],
                    "held_unknown",
                )

    def test_invalid_business_key_retains_reception_only(self) -> None:
        result = ingest_observation(self.engine, _reception(_raw(message_id="bad")))
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.reason, "invalid_message_id")
        self.assertEqual(len(self._rows(observations)), 0)
        self.assertEqual(len(self._rows(receptions)), 1)
        self.assertEqual(len(self._rows(evaluation_jobs)), 0)

    def test_bad_metric_shape_cannot_be_queued_for_rule_evaluation(self) -> None:
        payload = _raw(
            metrics=[{"key": "temperature", "value": 11, "unit": "°C"}, "bad-entry"]
        )
        result = ingest_observation(self.engine, _reception(payload))
        self.assertEqual(result.status, "accepted_new")
        stored = self._rows(observations)[0]
        self.assertEqual(stored["raw_payload"]["metrics"], payload["metrics"])
        self.assertEqual(stored["risk"]["state"], "unknown")
        self.assertEqual(stored["risk"]["reason"], "invalid_metric_shape")
        self.assertEqual(self._rows(evaluation_jobs)[0]["state"], "held_unknown")

    def test_mid_transaction_storage_failure_rolls_back_and_can_retry(self) -> None:
        receipt = _reception(_raw())
        broken = False

        def fail_job_insert(
            _conn, _cursor, statement, _params, _context, _many
        ) -> None:
            nonlocal broken
            if not broken and "INSERT INTO telemetry_evaluation_candidate" in statement:
                broken = True
                raise OperationalError(statement, {}, RuntimeError("simulated failure"))

        event.listen(self.engine, "before_cursor_execute", fail_job_insert)
        try:
            failed = ingest_observation(self.engine, receipt)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_job_insert)
        self.assertEqual(failed.status, "retryable_failure")
        self.assertEqual(failed.reason, "storage_unavailable")
        for table in (observations, receptions, evaluation_jobs):
            with self.engine.connect() as connection:
                self.assertEqual(
                    connection.scalar(select(func.count()).select_from(table)), 0
                )
        self.assertEqual(
            ingest_observation(self.engine, receipt).status, "accepted_new"
        )
        self.assertEqual(len(self._rows(observations)), 1)

    def test_concurrent_reception_of_one_message_never_double_writes(self) -> None:
        first = _reception(_raw())
        second = _reception(_raw(), received_at=TIME + timedelta(seconds=1))
        barrier = Barrier(2)

        def receive(receipt: Reception):
            barrier.wait(timeout=5)
            return ingest_observation(self.engine, receipt)

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(receive, receipt) for receipt in (first, second)]
            outcomes = [task.result(timeout=10) for task in pending]

        # SQLite can report a write lock for a competing transaction. Replaying
        # that same receipt after the lock clears must resolve to the first row.
        for index, outcome in enumerate(outcomes):
            if outcome.status == "retryable_failure":
                outcomes[index] = ingest_observation(
                    self.engine, (first, second)[index]
                )

        self.assertCountEqual(
            [outcome.status for outcome in outcomes],
            ["accepted_new", "accepted_duplicate"],
        )
        self.assertEqual(outcomes[0].observation_id, outcomes[1].observation_id)
        self.assertEqual(len(self._rows(observations)), 1)
        self.assertEqual(len(self._rows(receptions)), 2)
        self.assertEqual(len(self._rows(evaluation_jobs)), 1)


if __name__ == "__main__":
    unittest.main()
