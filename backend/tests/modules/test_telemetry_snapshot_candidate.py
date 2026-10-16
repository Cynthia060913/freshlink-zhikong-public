"""Test-only fixed history members; not an authenticated F06 HTTP service."""

import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, func, select

from app.modules.telemetry.history_candidate import HistoryFiltersCandidate
from app.modules.telemetry.snapshot_candidate import (
    create_snapshot_candidate,
    read_snapshot_page_candidate,
    snapshot_members,
    snapshots,
)
from app.modules.telemetry.storage_candidate import ingest_observation, metadata

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
ACTOR = UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 10, 16, 0, 5, tzinfo=UTC)


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


def reception(sampled_at: str, temperature: int, received_at: datetime):
    payload = {
        "schema_version": "1.0",
        "device_id": str(DEVICE),
        "message_id": str(uuid4()),
        "sampled_at": sampled_at,
        "metrics": [
            {"key": "temperature", "value": temperature, "unit": "°C"},
            {"key": "humidity", "value": 70, "unit": "%RH"},
        ],
    }
    return Reception(
        raw_text=json.dumps(payload, ensure_ascii=False),
        reception_id=uuid4(),
        received_at=received_at,
    )


class SnapshotCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "snapshot.db"
        self.engine = create_engine(f"sqlite:///{self.path}")
        metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def _read(self, snapshot_id: UUID, **overrides):
        options = {
            "snapshot_id": snapshot_id,
            "actor_id": ACTOR,
            "filters": HistoryFiltersCandidate(warehouse_id=WAREHOUSE),
            "policy_version": "active-user-read-v1",
            "now": NOW,
            "limit": 1,
        }
        options.update(overrides)
        return read_snapshot_page_candidate(self.engine, **options)

    def test_later_insert_with_earlier_time_cannot_enter_old_pages(self) -> None:
        first = ingest_observation(
            self.engine,
            reception("2026-10-16T00:01:00Z", 11, NOW - timedelta(minutes=4)),
        )
        second = ingest_observation(
            self.engine,
            reception("bad-time", 7, NOW - timedelta(minutes=3)),
        )
        self.assertEqual(
            (first.status, second.status), ("accepted_new", "accepted_new")
        )
        filters = HistoryFiltersCandidate(warehouse_id=WAREHOUSE)
        fixed = create_snapshot_candidate(
            self.engine,
            actor_id=ACTOR,
            filters=filters,
            policy_version="active-user-read-v1",
            now=NOW,
        )
        first_page = self._read(fixed)
        self.assertEqual(first_page.total, 2)
        self.assertEqual(
            first_page.items[0]["observation_id"], str(first.observation_id)
        )
        self.assertEqual(first_page.items[0]["temperature_c"], 11)
        self.assertEqual(first_page.next_after_ordinal, 0)

        # The new row carries an earlier trusted received_at and sampled_at,
        # but is inserted only after the snapshot committed. A genuinely
        # overlapping uncommitted T1 still needs a PostgreSQL concurrency test.
        late = ingest_observation(
            self.engine,
            reception("2026-10-16T00:00:30Z", 6, NOW - timedelta(minutes=5)),
        )
        self.assertEqual(late.status, "accepted_new")
        old_second_page = self._read(fixed, after_ordinal=0)
        self.assertEqual(old_second_page.total, 2)
        self.assertEqual(
            old_second_page.items[0]["observation_id"], str(second.observation_id)
        )
        self.assertIsNone(old_second_page.items[0]["sampled_at"])
        self.assertEqual(old_second_page.items[0]["risk_state"], "unknown")
        self.assertIsNone(old_second_page.next_after_ordinal)

        new = create_snapshot_candidate(
            self.engine,
            actor_id=ACTOR,
            filters=filters,
            policy_version="active-user-read-v1",
            now=NOW + timedelta(seconds=1),
        )
        new_page = self._read(new, now=NOW + timedelta(seconds=1))
        self.assertEqual(new_page.total, 3)
        self.assertEqual(new_page.items[0]["observation_id"], str(late.observation_id))

        self.engine.dispose()
        self.engine = create_engine(f"sqlite:///{self.path}")
        reopened = self._read(fixed)
        self.assertEqual(reopened.items, first_page.items)
        self.assertEqual(reopened.total, 2)
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(snapshot_members)), 5
            )

    def test_scope_expiry_limit_and_empty_snapshot_are_explicit(self) -> None:
        ingest_observation(
            self.engine,
            reception("2026-10-16T00:01:00Z", 11, NOW - timedelta(minutes=4)),
        )
        filters = HistoryFiltersCandidate(warehouse_id=WAREHOUSE)
        fixed = create_snapshot_candidate(
            self.engine,
            actor_id=ACTOR,
            filters=filters,
            policy_version="active-user-read-v1",
            now=NOW,
        )
        for override in (
            {"actor_id": uuid4()},
            {"filters": HistoryFiltersCandidate(device_id=uuid4())},
            {"policy_version": "changed-policy"},
            {"now": NOW + timedelta(seconds=900)},
        ):
            with self.subTest(override=override):
                with self.assertRaisesRegex(ValueError, "invalid_snapshot"):
                    self._read(fixed, **override)
        with self.assertRaisesRegex(ValueError, "invalid_snapshot_cursor"):
            self._read(fixed, after_ordinal=1)
        ingest_observation(
            self.engine,
            reception("bad-time", 7, NOW - timedelta(minutes=3)),
        )
        with self.assertRaisesRegex(ValueError, "snapshot_row_limit"):
            create_snapshot_candidate(
                self.engine,
                actor_id=ACTOR,
                filters=filters,
                policy_version="active-user-read-v1",
                now=NOW,
                max_members=1,
            )
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(snapshots)), 1
            )

        empty_filters = HistoryFiltersCandidate(device_id=uuid4())
        empty = create_snapshot_candidate(
            self.engine,
            actor_id=ACTOR,
            filters=empty_filters,
            policy_version="active-user-read-v1",
            now=NOW,
        )
        empty_page = self._read(empty, filters=empty_filters)
        self.assertEqual(empty_page.total, 0)
        self.assertEqual(empty_page.items, [])
        self.assertIsNone(empty_page.next_after_ordinal)

        local_time = NOW.astimezone(timezone(timedelta(hours=8)))
        utc_filters = HistoryFiltersCandidate(sampled_from=NOW - timedelta(minutes=5))
        same_filters = HistoryFiltersCandidate(
            sampled_from=local_time - timedelta(minutes=5)
        )
        matching = create_snapshot_candidate(
            self.engine,
            actor_id=ACTOR,
            filters=utc_filters,
            policy_version="active-user-read-v1",
            now=NOW,
        )
        self.assertEqual(self._read(matching, filters=same_filters).total, 1)


if __name__ == "__main__":
    unittest.main()
