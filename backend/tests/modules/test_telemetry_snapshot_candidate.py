"""Test-only fixed history members; not an authenticated F06 HTTP service."""

import csv
import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError

from app.modules.telemetry.csv_preview import PREVIEW_COLUMNS
from app.modules.telemetry.history_candidate import HistoryFiltersCandidate
from app.modules.telemetry.snapshot_candidate import (
    create_snapshot_candidate,
    prune_expired_snapshots_candidate,
    read_snapshot_page_candidate,
    render_snapshot_csv_candidate,
    snapshot_members,
    snapshots,
)
from app.modules.telemetry.snapshot_cursor_candidate import (
    read_signed_snapshot_page_candidate,
)
from app.modules.telemetry.storage_candidate import (
    ingest_observation,
    metadata,
    observations,
    receptions,
)

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

    def _csv(self, snapshot_id: UUID, **overrides):
        options = {
            "snapshot_id": snapshot_id,
            "actor_id": ACTOR,
            "filters": HistoryFiltersCandidate(warehouse_id=WAREHOUSE),
            "policy_version": "active-user-read-v1",
            "now": NOW,
        }
        options.update(overrides)
        return list(
            csv.DictReader(
                StringIO(
                    render_snapshot_csv_candidate(self.engine, **options).decode(
                        "utf-8-sig"
                    )
                )
            )
        )

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
        old_csv = self._csv(fixed)
        self.assertEqual(len(old_csv), first_page.total)
        self.assertEqual(
            [row["observation_id"] for row in old_csv],
            [
                first_page.items[0]["observation_id"],
                old_second_page.items[0]["observation_id"],
            ],
        )
        self.assertEqual(old_csv[0]["raw_temperature_value"], "11")
        self.assertEqual(old_csv[0]["raw_temperature_unit"], "°C")
        self.assertEqual(old_csv[0]["temperature_c"], "11.0")
        self.assertEqual(old_csv[1]["risk_state"], "unknown")
        self.assertEqual(old_csv[1]["sampled_at"], "")
        csv_bytes = render_snapshot_csv_candidate(
            self.engine,
            snapshot_id=fixed,
            actor_id=ACTOR,
            filters=filters,
            policy_version="active-user-read-v1",
            now=NOW,
        )
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(
            next(csv.reader(StringIO(csv_bytes.decode("utf-8-sig")))),
            list(PREVIEW_COLUMNS),
        )

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
        self.assertEqual(len(self._csv(new, now=NOW + timedelta(seconds=1))), 3)

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
                with self.assertRaisesRegex(ValueError, "invalid_snapshot"):
                    self._csv(fixed, **override)
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
        self.assertEqual(self._csv(empty, filters=empty_filters), [])

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

        quality_filters = HistoryFiltersCandidate(
            quality_in=("ok", "invalid_time:sampled_at")
        )
        quality_snapshot = create_snapshot_candidate(
            self.engine,
            actor_id=ACTOR,
            filters=quality_filters,
            policy_version="active-user-read-v1",
            now=NOW,
        )
        reversed_tags = HistoryFiltersCandidate(
            quality_in=("invalid_time:sampled_at", "ok")
        )
        self.assertEqual(self._read(quality_snapshot, filters=reversed_tags).total, 2)
        self.assertEqual(len(self._csv(quality_snapshot, filters=reversed_tags)), 2)
        with self.assertRaisesRegex(ValueError, "invalid_snapshot"):
            self._csv(
                quality_snapshot,
                filters=HistoryFiltersCandidate(quality_in=("ok",)),
            )
        invalid_only = HistoryFiltersCandidate(quality_in=("invalid_time:sampled_at",))
        invalid_snapshot = create_snapshot_candidate(
            self.engine,
            actor_id=ACTOR,
            filters=invalid_only,
            policy_version="active-user-read-v1",
            now=NOW,
        )
        invalid_rows = self._csv(invalid_snapshot, filters=invalid_only)
        self.assertEqual(len(invalid_rows), 1)
        self.assertEqual(invalid_rows[0]["sampled_at"], "")
        self.assertEqual(invalid_rows[0]["risk_state"], "unknown")

    def test_csv_formula_text_uses_fixed_projection_and_handles_missing_data(
        self,
    ) -> None:
        bad = ingest_observation(
            self.engine,
            reception("2026-10-16T00:01:00Z", "=1+1", NOW - timedelta(minutes=4)),
        )
        self.assertEqual(bad.status, "accepted_new")
        fixed = create_snapshot_candidate(
            self.engine,
            actor_id=ACTOR,
            filters=HistoryFiltersCandidate(warehouse_id=WAREHOUSE),
            policy_version="active-user-read-v1",
            now=NOW,
        )
        row = self._csv(fixed)[0]
        self.assertEqual(row["raw_temperature_value"], "'=1+1")
        self.assertEqual(row["temperature_c"], "")
        self.assertEqual(row["risk_state"], "unknown")
        self.assertIn("invalid", row["quality"])

    def test_expired_snapshot_cleanup_is_bounded_and_preserves_observations(
        self,
    ) -> None:
        stored = ingest_observation(
            self.engine,
            reception("2026-10-16T00:01:00Z", 11, NOW - timedelta(minutes=4)),
        )
        self.assertEqual(stored.status, "accepted_new")
        filters = HistoryFiltersCandidate(warehouse_id=WAREHOUSE)
        snapshots_created = [
            create_snapshot_candidate(
                self.engine,
                actor_id=ACTOR,
                filters=filters,
                policy_version="active-user-read-v1",
                now=created,
            )
            for created in (
                NOW - timedelta(minutes=20),
                NOW - timedelta(minutes=16),
                NOW,
            )
        ]
        with self.assertRaisesRegex(ValueError, "aware"):
            prune_expired_snapshots_candidate(self.engine, now=datetime(2026, 10, 16))
        for invalid_limit in (0, True, 1001):
            with (
                self.subTest(limit=invalid_limit),
                self.assertRaisesRegex(ValueError, "limit"),
            ):
                prune_expired_snapshots_candidate(
                    self.engine, now=NOW, limit=invalid_limit
                )

        def fail_metadata_delete(_conn, _cursor, statement, _params, _context, _many):
            if "DELETE FROM telemetry_snapshot_candidate" in statement:
                raise OperationalError(statement, {}, RuntimeError("synthetic failure"))

        event.listen(self.engine, "before_cursor_execute", fail_metadata_delete)
        try:
            with self.assertRaises(OperationalError):
                prune_expired_snapshots_candidate(self.engine, now=NOW, limit=1)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_metadata_delete)
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(snapshots)), 3
            )
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(snapshot_members)), 3
            )

        self.assertEqual(
            prune_expired_snapshots_candidate(self.engine, now=NOW, limit=1), 1
        )
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(snapshots)), 2
            )
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(snapshot_members)), 2
            )
        self.assertEqual(
            prune_expired_snapshots_candidate(self.engine, now=NOW, limit=1), 1
        )
        self.assertEqual(prune_expired_snapshots_candidate(self.engine, now=NOW), 0)
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(snapshots)), 1
            )
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(snapshot_members)), 1
            )
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(observations)), 1
            )
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(receptions)), 1
            )
        for expired_id in snapshots_created[:2]:
            with self.assertRaisesRegex(ValueError, "invalid_snapshot"):
                self._read(expired_id)
        self.assertEqual(self._read(snapshots_created[2]).total, 1)

    def test_signed_cursor_rejects_tamper_scope_rotation_and_expiry(self) -> None:
        first = ingest_observation(
            self.engine,
            reception("2026-10-16T00:01:00Z", 11, NOW - timedelta(minutes=4)),
        )
        second = ingest_observation(
            self.engine,
            reception("2026-10-16T00:02:00Z", 7, NOW - timedelta(minutes=3)),
        )
        self.assertEqual(
            (first.status, second.status), ("accepted_new", "accepted_new")
        )
        filters = HistoryFiltersCandidate(quality_in=("ok", "invalid_time:sampled_at"))
        fixed = create_snapshot_candidate(
            self.engine,
            actor_id=ACTOR,
            filters=filters,
            policy_version="active-user-read-v1",
            now=NOW,
        )
        options = {
            "actor_id": ACTOR,
            "filters": filters,
            "policy_version": "active-user-read-v1",
            "now": NOW,
            "signing_key": b"k" * 32,
            "key_id": "cursor-v1",
            "limit": 1,
        }
        with self.assertRaisesRegex(ValueError, "snapshot_required"):
            read_signed_snapshot_page_candidate(self.engine, **options)
        page1 = read_signed_snapshot_page_candidate(
            self.engine, snapshot_id=fixed, **options
        )
        self.assertEqual(page1.total, 2)
        self.assertEqual(page1.items[0]["observation_id"], str(first.observation_id))
        self.assertIsNotNone(page1.next_cursor)
        token = page1.next_cursor
        page2 = read_signed_snapshot_page_candidate(
            self.engine,
            cursor=token,
            **{
                **options,
                "filters": HistoryFiltersCandidate(
                    quality_in=("invalid_time:sampled_at", "ok")
                ),
            },
        )
        self.assertEqual(page2.items[0]["observation_id"], str(second.observation_id))
        self.assertIsNone(page2.next_cursor)

        failures = (
            {"cursor": ("A" if token[0] != "A" else "B") + token[1:]},
            {"cursor": ""},
            {"cursor": "bad.cursor.extra"},
            {"cursor": "A" * 2049},
            {"cursor": token, "actor_id": uuid4()},
            {"cursor": token, "filters": HistoryFiltersCandidate(quality_in=("ok",))},
            {"cursor": token, "policy_version": "changed"},
            {"cursor": token, "signing_key": b"z" * 32},
            {"cursor": token, "key_id": "rotated"},
            {"cursor": token, "snapshot_id": uuid4()},
        )
        for override in failures:
            with (
                self.subTest(override=override),
                self.assertRaisesRegex(ValueError, "invalid_snapshot_cursor"),
            ):
                read_signed_snapshot_page_candidate(
                    self.engine, **{**options, **override}
                )
        with self.assertRaisesRegex(ValueError, "invalid_snapshot"):
            read_signed_snapshot_page_candidate(
                self.engine,
                cursor=token,
                **{**options, "now": NOW + timedelta(seconds=900)},
            )
        with self.assertRaisesRegex(ValueError, "at least 32 bytes"):
            read_signed_snapshot_page_candidate(
                self.engine, snapshot_id=fixed, **{**options, "signing_key": b"weak"}
            )


if __name__ == "__main__":
    unittest.main()
