"""Persisted C candidate history; no HTTP, actor, snapshot, or signed cursor."""

import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select

from app.modules.telemetry.history_candidate import (
    HistoryFiltersCandidate,
    list_history_candidate,
)
from app.modules.telemetry.storage_candidate import (
    ingest_observation,
    metadata,
    observations,
)

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
BASE = datetime(2026, 10, 16, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Reception:
    raw_text: str
    reception_id: UUID
    received_at: datetime
    authenticated_device_id: UUID = DEVICE
    warehouse_id: UUID = WAREHOUSE

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


def reception(message_id: UUID, sampled_at: str, temperature: int, minute: int):
    payload = {
        "schema_version": "1.0",
        "device_id": str(DEVICE),
        "message_id": str(message_id),
        "sampled_at": sampled_at,
        "metrics": [
            {"key": "temperature", "value": temperature, "unit": "°C"},
            {"key": "humidity", "value": 70, "unit": "%RH"},
        ],
    }
    return Reception(
        raw_text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        reception_id=uuid4(),
        received_at=BASE + timedelta(minutes=minute),
    )


class PersistedHistoryCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "history.db"
        self.engine = create_engine(f"sqlite:///{self.path}")
        metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def _fixture(self):
        records = [
            reception(uuid4(), "2026-10-16T00:01:00Z", 11, 5),
            reception(uuid4(), "2026-10-16T00:00:00Z", 4, 6),
            reception(uuid4(), "2026-10-16T00:01:00Z", 5, 7),
            reception(uuid4(), "bad-time", 7, 8),
        ]
        ids = []
        for item in records:
            result = ingest_observation(self.engine, item)
            self.assertEqual(result.status, "accepted_new")
            ids.append(result.observation_id)
        return records, ids

    def test_persisted_time_filters_keyset_and_invalid_time(self) -> None:
        records, ids = self._fixture()
        duplicated = ingest_observation(
            self.engine,
            reception(
                UUID(records[0].raw_payload["message_id"]),
                "2026-10-16T00:01:00Z",
                11,
                10,
            ),
        )
        self.assertEqual(duplicated.status, "accepted_duplicate")
        self.assertEqual(duplicated.observation_id, ids[0])

        filters = HistoryFiltersCandidate(device_id=DEVICE, warehouse_id=WAREHOUSE)
        all_rows = list_history_candidate(self.engine, filters, limit=100)
        self.assertIsNone(all_rows.next_after_observation_id)
        self.assertEqual(len(all_rows.items), 4)
        self.assertEqual(all_rows.items[0]["observation_id"], str(ids[1]))
        self.assertEqual(
            [row["observation_id"] for row in all_rows.items[1:3]],
            sorted((str(ids[0]), str(ids[2]))),
        )
        self.assertEqual(all_rows.items[-1]["observation_id"], str(ids[3]))
        self.assertIsNone(all_rows.items[-1]["sampled_at"])
        self.assertEqual(all_rows.items[-1]["risk_state"], "unknown")
        self.assertIn("invalid_time:sampled_at", all_rows.items[-1]["quality"])
        self.assertEqual(all_rows.items[0]["received_at"], "2026-10-16T00:06:00Z")
        first_high = next(
            row for row in all_rows.items if row["observation_id"] == str(ids[0])
        )
        self.assertEqual(first_high["temperature_c"], 11)
        self.assertEqual(first_high["received_at"], "2026-10-16T00:05:00Z")
        self.assertEqual(first_high["risk_state"], "unknown")

        seen = []
        after = None
        while True:
            page = list_history_candidate(
                self.engine, filters, limit=1, after_observation_id=after
            )
            seen.extend(row["observation_id"] for row in page.items)
            after = page.next_after_observation_id
            if after is None:
                break
        self.assertEqual(seen, [row["observation_id"] for row in all_rows.items])
        self.assertEqual(len(set(seen)), 4)

        sampled = list_history_candidate(
            self.engine,
            HistoryFiltersCandidate(
                sampled_from=BASE + timedelta(minutes=1),
                sampled_to=BASE + timedelta(minutes=2),
            ),
        )
        self.assertEqual(
            {row["observation_id"] for row in sampled.items},
            {str(ids[0]), str(ids[2])},
        )
        received = list_history_candidate(
            self.engine,
            HistoryFiltersCandidate(
                received_from=BASE + timedelta(minutes=6),
                received_to=BASE + timedelta(minutes=8),
            ),
        )
        self.assertEqual(
            {row["observation_id"] for row in received.items},
            {str(ids[1]), str(ids[2])},
        )
        self.assertEqual(
            list_history_candidate(
                self.engine, HistoryFiltersCandidate(device_id=uuid4())
            ).items,
            [],
        )
        self.assertEqual(
            list_history_candidate(
                self.engine, HistoryFiltersCandidate(warehouse_id=uuid4())
            ).items,
            [],
        )
        with self.engine.connect() as connection:
            original_raw = connection.execute(
                select(observations.c.raw_text).where(observations.c.id == ids[0])
            ).scalar_one()
        self.assertEqual(original_raw, records[0].raw_text)

        self.engine.dispose()
        self.engine = create_engine(f"sqlite:///{self.path}")
        reopened = list_history_candidate(self.engine, filters)
        self.assertEqual(reopened.items, all_rows.items)

    def test_invalid_filter_and_cursor_are_rejected(self) -> None:
        self._fixture()
        with self.assertRaisesRegex(ValueError, "limit"):
            list_history_candidate(self.engine, HistoryFiltersCandidate(), limit=0)
        with self.assertRaisesRegex(ValueError, "limit"):
            list_history_candidate(self.engine, HistoryFiltersCandidate(), limit=True)
        with self.assertRaisesRegex(ValueError, "aware"):
            list_history_candidate(
                self.engine,
                HistoryFiltersCandidate(sampled_from=datetime(2026, 10, 16)),
            )
        with self.assertRaisesRegex(ValueError, "before"):
            list_history_candidate(
                self.engine,
                HistoryFiltersCandidate(
                    sampled_from=BASE + timedelta(minutes=1), sampled_to=BASE
                ),
            )
        with self.assertRaisesRegex(ValueError, "selection"):
            list_history_candidate(
                self.engine,
                HistoryFiltersCandidate(device_id=DEVICE),
                after_observation_id=uuid4(),
            )
        with self.assertRaisesRegex(ValueError, "selection"):
            list_history_candidate(
                self.engine,
                HistoryFiltersCandidate(device_id=uuid4()),
                after_observation_id=UUID(
                    list_history_candidate(
                        self.engine, HistoryFiltersCandidate()
                    ).items[0]["observation_id"]
                ),
            )


if __name__ == "__main__":
    unittest.main()
