"""Actual A envelope to C isolated storage; no broker, app route or real PG."""

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, func, select

from app.modules.telemetry.storage_candidate import (
    evaluation_jobs,
    ingest_observation,
    metadata,
    observations,
    receptions,
)

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
RECEIVED = datetime(2026, 10, 16, 0, 5, tzinfo=UTC)
TOPIC = f"freshlink/v1/devices/{DEVICE}/telemetry"


def _payload(*, temperature: int = 11, sampled_at: str = "2026-10-16T00:04:00Z"):
    return {
        "schema_version": "1.0",
        "device_id": str(DEVICE),
        "message_id": str(uuid4()),
        "sampled_at": sampled_at,
        "metrics": [
            {"key": "temperature", "value": temperature, "unit": "°C"},
            {"key": "humidity", "value": 70, "unit": "%RH"},
        ],
    }


@unittest.skipIf(sys.version_info < (3, 14), "A ingress requires project Python 3.14")
class ActualIngressToStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "a-to-c.db"
        self.engine = create_engine(f"sqlite:///{self.path}")
        metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def _receive(self, payload: dict, when: datetime = RECEIVED):
        from app.modules.iot.ingress import DeviceContext, receive_telemetry

        return receive_telemetry(
            topic=TOPIC,
            payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            context=DeviceContext(device_id=DEVICE, warehouse_id=WAREHOUSE),
            received_at=when,
        )

    def _count(self, table) -> int:
        with self.engine.connect() as connection:
            return int(connection.scalar(select(func.count()).select_from(table)))

    def test_actual_a_envelope_commits_raw_then_deduplicates_redelivery(self) -> None:
        raw = _payload()
        first_envelope = self._receive(raw)
        first = ingest_observation(self.engine, first_envelope)
        self.assertEqual(first.status, "accepted_new")
        self.assertIsNotNone(first.observation_id)

        redelivery = self._receive(raw, RECEIVED + timedelta(seconds=1))
        self.assertNotEqual(redelivery.reception_id, first_envelope.reception_id)
        duplicate = ingest_observation(self.engine, redelivery)
        self.assertEqual(duplicate.status, "accepted_duplicate")
        self.assertEqual(duplicate.observation_id, first.observation_id)

        changed_raw = {
            **raw,
            "metrics": [{**raw["metrics"][0], "value": 12}, raw["metrics"][1]],
        }
        conflict = ingest_observation(
            self.engine, self._receive(changed_raw, RECEIVED + timedelta(seconds=2))
        )
        self.assertEqual(conflict.status, "conflict")
        self.assertEqual(conflict.observation_id, first.observation_id)
        self.assertEqual(self._count(observations), 1)
        self.assertEqual(self._count(receptions), 3)
        self.assertEqual(self._count(evaluation_jobs), 1)
        with self.engine.connect() as connection:
            row = connection.execute(select(observations)).mappings().one()
        self.assertEqual(row["raw_text"], first_envelope.raw_text)
        self.assertEqual(row["raw_payload"], raw)
        self.assertEqual(row["first_received_at_utc"].replace(tzinfo=UTC), RECEIVED)
        self.assertEqual(row["normalized"]["metrics"]["temperature"], 11)
        self.assertEqual(row["risk"]["state"], "unknown")
        self.assertEqual(row["risk"]["reason"], "no_threshold_rule")

        self.engine.dispose()
        self.engine = create_engine(f"sqlite:///{self.path}")
        self.assertEqual(self._count(observations), 1)
        self.assertEqual(self._count(receptions), 3)

    def test_bad_sample_time_and_unit_keep_raw_without_evaluation(self) -> None:
        bad_time = _payload(sampled_at="invalid-time")
        bad_unit = _payload()
        bad_unit["metrics"][0]["unit"] = "°F"
        for payload in (bad_time, bad_unit):
            with self.subTest(payload=payload):
                envelope = self._receive(payload)
                result = ingest_observation(self.engine, envelope)
                self.assertEqual(result.status, "accepted_new")
                with self.engine.connect() as connection:
                    row = (
                        connection.execute(
                            select(observations).where(
                                observations.c.id == result.observation_id
                            )
                        )
                        .mappings()
                        .one()
                    )
                    job = (
                        connection.execute(
                            select(evaluation_jobs).where(
                                evaluation_jobs.c.observation_id
                                == result.observation_id
                            )
                        )
                        .mappings()
                        .one()
                    )
                self.assertEqual(row["raw_text"], envelope.raw_text)
                self.assertEqual(row["risk"]["state"], "unknown")
                self.assertEqual(job["state"], "held_unknown")


if __name__ == "__main__":
    unittest.main()
