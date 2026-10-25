"""C correction candidate tests: durable identity, replay and send hold only."""

import hashlib
import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select

from app.modules.telemetry.data_quality_correction_candidate import (
    _correction_source_id,
)
from app.modules.telemetry.outbox_candidate import (
    claim_candidate,
    outbox,
)
from app.modules.telemetry.recovery_reception_evidence_candidate import (
    has_unresolved_reception_evidence_candidate,
)
from app.modules.telemetry.storage_candidate import (
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


class DataQualityCorrectionCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "correction.db"
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def _rows(self, table) -> list[dict]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(table)).mappings()]

    def _seed_delivered_anomaly(
        self, observation_id: UUID, *, existing_observation: bool = False
    ) -> UUID:
        original_id = uuid4()
        sampled_at = TIME.isoformat().replace("+00:00", "Z")
        body = {
            "schema_version": "1.0",
            "source_event_id": str(original_id),
            "kind": "temperature_high",
            "warehouse_id": str(WAREHOUSE),
            "batch_id": str(uuid4()),
            "observation_ids": [str(observation_id)],
            "metric_code": "temperature",
            "sampled_at": sampled_at,
            "value": 11,
            "unit": "°C",
            "severity": "warning",
            "rule_snapshot_id": str(uuid4()),
            "rule_version": "rule-1",
            "algorithm_version": "threshold/v1",
            "evidence": {},
            "related_source_event_id": None,
        }
        digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.engine.begin() as connection:
            if not existing_observation:
                connection.execute(
                    observations.insert().values(
                        id=observation_id,
                        device_id=DEVICE,
                        message_id=uuid4(),
                        warehouse_id=WAREHOUSE,
                        content_sha256="a" * 64,
                        raw_text='{"temperature":11}',
                        raw_payload={"temperature": 11},
                        sampled_at_utc=TIME,
                        first_received_at_utc=TIME,
                        normalized={"sampled_at": sampled_at},
                        quality=[],
                        risk={"state": "high"},
                    )
                )
            connection.execute(
                outbox.insert().values(
                    source_event_id=original_id,
                    related_source_event_id=None,
                    observation_id=observation_id,
                    body_sha256=digest,
                    body=body,
                    state="delivered",
                    attempt_count=1,
                    next_attempt_at_utc=TIME,
                )
            )
        return original_id

    def _corrections(self, original_id: UUID) -> list[dict]:
        with self.engine.connect() as connection:
            return list(
                connection.execute(
                    select(outbox).where(
                        outbox.c.related_source_event_id == original_id
                    )
                )
                .mappings()
                .all()
            )

    def test_late_invalid_persists_stable_held_correction_and_blocks_recovery(self):
        original_id = self._seed_delivered_anomaly(uuid4())
        bad = _reception(
            _raw(schema_version="2.0"), received_at=TIME + timedelta(seconds=1)
        )

        first = ingest_observation(self.engine, bad)
        self.assertEqual(first.status, "invalid")
        rows = self._corrections(original_id)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        expected_id = _correction_source_id(
            original_id,
            f"reception:{bad.reception_id}:{self._rows(receptions)[0]['receipt_sha256']}",
        )
        self.assertEqual(row["source_event_id"], expected_id)
        self.assertEqual(row["state"], "held_contract")
        self.assertEqual(row["attempt_count"], 0)
        self.assertEqual(
            row["last_error_code"], "f03_data_quality_contract_unconfirmed"
        )
        self.assertEqual(row["body"]["kind"], "data_quality")
        self.assertEqual(row["body"]["related_source_event_id"], str(original_id))
        self.assertNotIn("raw_text", row["body"])

        self.assertIsNone(
            claim_candidate(
                self.engine, source_event_id=expected_id, now=bad.received_at
            )
        )
        with self.engine.connect() as connection:
            self.assertTrue(
                has_unresolved_reception_evidence_candidate(
                    connection,
                    device_id=DEVICE,
                    warehouse_id=WAREHOUSE,
                    original_received_at=TIME,
                )
            )

        replay = ingest_observation(self.engine, bad)
        self.assertEqual(replay.status, "invalid")
        self.assertEqual(len(self._corrections(original_id)), 1)
        self.assertEqual(
            len(
                [
                    row
                    for row in self._rows(outbox)
                    if row["related_source_event_id"] == original_id
                ]
            ),
            1,
        )

    def test_reused_reception_id_conflict_is_stable_and_never_claimable(self):
        initial = _reception(_raw())
        accepted = ingest_observation(self.engine, initial)
        self.assertEqual(accepted.status, "accepted_new")
        original_id = self._seed_delivered_anomaly(
            accepted.observation_id, existing_observation=True
        )
        tampered = _reception(
            _raw(extra="tampered"),
            reception_id=initial.reception_id,
            received_at=TIME + timedelta(seconds=2),
        )

        first = ingest_observation(self.engine, tampered)
        self.assertEqual(first.status, "conflict")
        rows = self._corrections(original_id)
        self.assertEqual(len(rows), 1)
        correction_id = rows[0]["source_event_id"]
        self.assertEqual(
            rows[0]["body"]["evidence"]["reason_code"], "reception_id_reuse"
        )
        self.assertEqual(rows[0]["state"], "held_contract")
        self.assertEqual(
            claim_candidate(
                self.engine, source_event_id=correction_id, now=tampered.received_at
            ),
            None,
        )
        with self.engine.connect() as connection:
            self.assertTrue(
                has_unresolved_reception_evidence_candidate(
                    connection,
                    device_id=DEVICE,
                    warehouse_id=WAREHOUSE,
                    original_received_at=TIME,
                )
            )

        replay = ingest_observation(self.engine, tampered)
        self.assertEqual(replay.status, "conflict")
        self.assertEqual(
            self._corrections(original_id)[0]["source_event_id"], correction_id
        )
        self.assertEqual(len(self._rows(reception_reuse_conflicts)), 1)
        self.assertEqual(
            len(
                [
                    row
                    for row in self._rows(outbox)
                    if row["related_source_event_id"] == original_id
                ]
            ),
            1,
        )

    def test_late_changed_message_conflict_is_stable_and_never_claimable(self):
        initial = _reception(_raw())
        accepted = ingest_observation(self.engine, initial)
        self.assertEqual(accepted.status, "accepted_new")
        original_id = self._seed_delivered_anomaly(
            accepted.observation_id, existing_observation=True
        )
        changed_payload = _raw()
        changed_payload["metrics"][0]["value"] = 12
        changed = _reception(changed_payload, received_at=TIME + timedelta(seconds=3))

        first = ingest_observation(self.engine, changed)
        self.assertEqual(first.status, "conflict")
        rows = self._corrections(original_id)
        self.assertEqual(len(rows), 1)
        correction_id = rows[0]["source_event_id"]
        self.assertEqual(
            rows[0]["body"]["evidence"]["reason_code"], "conflicting_message"
        )
        self.assertEqual(rows[0]["state"], "held_contract")
        self.assertIsNone(
            claim_candidate(
                self.engine, source_event_id=correction_id, now=changed.received_at
            )
        )
        with self.engine.connect() as connection:
            self.assertTrue(
                has_unresolved_reception_evidence_candidate(
                    connection,
                    device_id=DEVICE,
                    warehouse_id=WAREHOUSE,
                    original_received_at=TIME,
                )
            )

        replay = ingest_observation(self.engine, changed)
        self.assertEqual(replay.status, "conflict")
        self.assertEqual(
            self._corrections(original_id)[0]["source_event_id"], correction_id
        )


if __name__ == "__main__":
    unittest.main()
