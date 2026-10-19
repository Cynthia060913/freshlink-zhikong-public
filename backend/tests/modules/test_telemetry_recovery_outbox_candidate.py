"""Persisted-observation to recovery-outbox tests; no B service is contacted."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, func, select, update

from app.modules.telemetry.outbox_candidate import outbox, stage_f03_candidate
from app.modules.telemetry.recovery_outbox_candidate import (
    stage_eligible_recovery_candidate,
)
from app.modules.telemetry.recovery_progress_candidate import (
    advance_persisted_recovery_window,
    recovery_progress,
)
from app.modules.telemetry.recovery_progress_candidate import (
    metadata as progress_metadata,
)
from app.modules.telemetry.recovery_window_candidate import (
    RecoveryRuleCandidate,
    RecoverySampleCandidate,
    RecoveryWindowCandidate,
)
from app.modules.telemetry.storage_candidate import (
    ingest_observation,
    metadata,
    observations,
)

_BASE = datetime(2026, 10, 19, tzinfo=UTC)
_DEVICE = UUID("11111111-1111-4111-8111-111111111111")
_WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
_BATCH = UUID("33333333-3333-4333-8333-333333333333")
_ORIGINAL = UUID("77777777-7777-4777-8777-777777777777")
_SNAPSHOT = UUID("88888888-8888-4888-8888-888888888888")


@dataclass
class Reception:
    raw_text: str
    reception_id: UUID
    received_at: datetime
    authenticated_device_id: UUID = _DEVICE
    warehouse_id: UUID = _WAREHOUSE

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


def _selection() -> dict:
    return {
        "kind": "temperature_high",
        "warehouse_id": str(_WAREHOUSE),
        "batch_id": str(_BATCH),
        "metric_code": "temperature",
        "snapshot_id": str(_SNAPSHOT),
        "rule_version": "temp-v1",
        "severity": "warning",
        "unit": "°C",
        "comparison": ">",
        "threshold": 8.0,
        "effective_start_at": "2026-10-18T00:00:00Z",
        "effective_end_at": None,
        "recovery": {"comparison": "<=", "threshold": 7.0, "min_samples": 3},
    }


class RecoveryOutboxCandidateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "telemetry.db"
        self.engine = create_engine(f"sqlite+pysqlite:///{self.db}")
        self.addCleanup(lambda: self.engine.dispose())
        metadata.create_all(self.engine)  # Only an isolated disposable test DB.
        progress_metadata.create_all(self.engine)
        self.original_observation_id = self.ingest(0, 11.0)
        anomaly = {
            "schema_version": "1.0",
            "source_event_id": str(_ORIGINAL),
            "kind": "temperature_high",
            "warehouse_id": str(_WAREHOUSE),
            "batch_id": str(_BATCH),
            "observation_ids": [str(self.original_observation_id)],
            "metric_code": "temperature",
            "sampled_at": "2026-10-19T00:00:00Z",
            "value": 11.0,
            "unit": "°C",
            "severity": "warning",
            "rule_snapshot_id": str(_SNAPSHOT),
            "rule_version": "temp-v1",
            "algorithm_version": "threshold-rule/1",
            "evidence": {"comparison": ">", "threshold": 8.0},
        }
        self.assertEqual(
            stage_f03_candidate(
                self.engine,
                source_event_id=_ORIGINAL,
                observation_id=self.original_observation_id,
                body=anomaly,
                now=_BASE,
            ),
            "staged",
        )
        self.initial = RecoveryWindowCandidate(
            original_source_event_id=_ORIGINAL,
            device_id=_DEVICE,
            warehouse_id=_WAREHOUSE,
            batch_id=_BATCH,
            anomaly_sampled_at=_BASE,
        )
        self.rule = RecoveryRuleCandidate("<=", 7.0, 3, 90)

    def ingest(self, second: int, temperature: float) -> UUID:
        sampled_at = _BASE + timedelta(seconds=second)
        payload = {
            "schema_version": "1.0",
            "device_id": str(_DEVICE),
            "message_id": str(uuid4()),
            "sampled_at": sampled_at.isoformat().replace("+00:00", "Z"),
            "metrics": [
                {"key": "temperature", "value": temperature, "unit": "°C"},
                {"key": "humidity", "value": 70, "unit": "%RH"},
            ],
        }
        created = ingest_observation(
            self.engine,
            Reception(json.dumps(payload, ensure_ascii=False), uuid4(), sampled_at),
        )
        self.assertEqual(created.status, "accepted_new")
        assert created.observation_id is not None
        return created.observation_id

    def progress(self, *seconds: int) -> tuple[UUID, ...]:
        for second in seconds:
            observation_id = self.ingest(second, 6.5)
            saved, _ = advance_persisted_recovery_window(
                self.engine,
                self.initial,
                self.rule,
                RecoverySampleCandidate(
                    observation_id=observation_id,
                    device_id=_DEVICE,
                    warehouse_id=_WAREHOUSE,
                    batch_id=_BATCH,
                    sampled_at=_BASE + timedelta(seconds=second),
                    temperature_c=6.5,
                    quality=("ok",),
                    duplicate="new",
                ),
            )
        return saved.observation_ids

    def stage(self, rule: dict | None = None):
        return stage_eligible_recovery_candidate(
            self.engine,
            original_source_event_id=_ORIGINAL,
            historical_rule=_selection() if rule is None else rule,
            now=_BASE + timedelta(minutes=3),
        )

    def test_original_must_be_delivered_and_three_samples_persisted(self) -> None:
        self.assertEqual(self.stage().status, "not_eligible")
        self.progress(10, 50)
        self.assertEqual(self.stage().status, "not_eligible")
        ids = self.progress(130)
        self.assertEqual(self.stage().status, "waiting_original")
        with self.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == _ORIGINAL)
                .values(state="delivered")
            )
        result = self.stage()
        self.assertEqual(result.status, "staged")
        self.assertIsNotNone(result.source_event_id)
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(outbox).where(
                        outbox.c.source_event_id == result.source_event_id
                    )
                )
                .mappings()
                .one()
            )
        self.assertEqual(row["body"]["observation_ids"], [str(value) for value in ids])
        self.assertEqual(row["body"]["related_source_event_id"], str(_ORIGINAL))
        self.assertEqual(row["body"]["rule_snapshot_id"], str(_SNAPSHOT))
        self.assertEqual(row["body"]["severity"], "warning")
        self.assertEqual(row["state"], "pending")  # Not a B-resolved alert.
        self.engine.dispose()
        self.engine = create_engine(f"sqlite+pysqlite:///{self.db}")
        again = self.stage()
        self.assertEqual(
            (again.status, again.source_event_id), ("duplicate", result.source_event_id)
        )
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(outbox)), 2
            )

    def test_changed_history_or_stored_observation_is_held_without_outbox(self) -> None:
        ids = self.progress(10, 50, 130)
        with self.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == _ORIGINAL)
                .values(state="delivered")
            )
        wrong = _selection()
        wrong["snapshot_id"] = str(uuid4())
        self.assertEqual(self.stage(wrong).status, "held_invalid_evidence")
        self.assertEqual(
            stage_eligible_recovery_candidate(
                self.engine,
                original_source_event_id=_ORIGINAL,
                historical_rule=None,
                now=_BASE + timedelta(minutes=3),
            ).status,
            "waiting_rule",
        )
        with self.engine.begin() as connection:
            connection.execute(
                update(observations)
                .where(observations.c.id == ids[-1])
                .values(quality=["missing_metric:temperature"])
            )
        self.assertEqual(self.stage().status, "held_invalid_evidence")
        with self.engine.begin() as connection:
            connection.execute(
                update(recovery_progress)
                .where(recovery_progress.c.original_source_event_id == _ORIGINAL)
                .values(observation_ids=["not-a-uuid"])
            )
        self.assertEqual(self.stage().status, "held_invalid_evidence")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(outbox)), 1
            )


if __name__ == "__main__":
    unittest.main()
