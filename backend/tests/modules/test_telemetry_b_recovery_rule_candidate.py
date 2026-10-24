"""C's recovery lookup against B's actual historical F02 service (SQLite)."""

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine

from app.modules.telemetry.outbox_candidate import stage_f03_candidate
from app.modules.telemetry.recovery_rule_candidate import bind_original_recovery_rule
from app.modules.telemetry.recovery_tick_candidate import (
    LocalBHistoricalRecoveryRuleCandidate,
)
from app.modules.telemetry.storage_candidate import ingest_observation, metadata

SAMPLED = datetime(2026, 10, 19, tzinfo=UTC)


@dataclass(frozen=True)
class _Reception:
    raw_text: str
    reception_id: UUID
    authenticated_device_id: UUID
    warehouse_id: UUID
    received_at: datetime = SAMPLED

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


class ActualBRecoveryRuleTests(unittest.TestCase):
    def test_original_snapshot_and_recovery_condition_are_selected(self) -> None:
        if sys.version_info < (3, 14):  # noqa: UP036 - B service uses 3.14 syntax
            self.skipTest("B F02 service needs project Python 3.14")

        from sqlmodel import Session

        from app.modules.alerts.models import (
            Batch,
            BatchRuleSnapshot,
            BatchRuleSnapshotCreate,
            Warehouse,
        )
        from app.modules.alerts.services.rules import create_snapshot

        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'unified.db'}")
            try:
                metadata.create_all(engine)
                Warehouse.__table__.create(engine)
                Batch.__table__.create(engine)
                BatchRuleSnapshot.__table__.create(engine)
                device, warehouse, batch = uuid4(), uuid4(), uuid4()
                with Session(engine) as session:
                    session.add(
                        Warehouse(id=warehouse, code="c-recovery", name="Recovery")
                    )
                    session.flush()
                    session.add(
                        Batch(
                            id=batch,
                            warehouse_id=warehouse,
                            code="apple-recovery",
                            product="apple",
                            started_at=SAMPLED - timedelta(days=1),
                        )
                    )
                    session.commit()
                    snapshot = create_snapshot(
                        session,
                        batch,
                        BatchRuleSnapshotCreate(
                            version_id=uuid4(),
                            rule_version="recovery-v1",
                            effective_start_at=SAMPLED - timedelta(days=1),
                            rules=[
                                {
                                    "metric_code": "temperature",
                                    "candidate_kind": "temperature_high",
                                    "severity": "warning",
                                    "threshold_high": 8,
                                    "unit": "°C",
                                    "recovery": {
                                        "comparison": "<=",
                                        "threshold": 7,
                                        "min_samples": 3,
                                    },
                                }
                            ],
                        ),
                    )
                    snapshot_id = snapshot.id
                raw = {
                    "schema_version": "1.0",
                    "device_id": str(device),
                    "message_id": str(uuid4()),
                    "sampled_at": "2026-10-19T00:00:00Z",
                    "metrics": [
                        {"key": "temperature", "value": 13, "unit": "°C"},
                        {"key": "humidity", "value": 75, "unit": "%RH"},
                    ],
                }
                saved = ingest_observation(
                    engine,
                    _Reception(
                        json.dumps(raw, ensure_ascii=False), uuid4(), device, warehouse
                    ),
                )
                self.assertEqual(saved.status, "accepted_new")
                assert saved.observation_id is not None
                source = uuid4()
                original = {
                    "schema_version": "1.0",
                    "source_event_id": str(source),
                    "kind": "temperature_high",
                    "warehouse_id": str(warehouse),
                    "batch_id": str(batch),
                    "observation_ids": [str(saved.observation_id)],
                    "metric_code": "temperature",
                    "sampled_at": raw["sampled_at"],
                    "value": 13,
                    "unit": "°C",
                    "severity": "warning",
                    "rule_snapshot_id": str(snapshot_id),
                    "rule_version": "recovery-v1",
                    "algorithm_version": "threshold-rule/1",
                    "evidence": {"comparison": ">", "threshold": 8, "quality": ["ok"]},
                }
                self.assertEqual(
                    stage_f03_candidate(
                        engine,
                        source_event_id=source,
                        observation_id=saved.observation_id,
                        body=original,
                        now=SAMPLED,
                    ),
                    "staged",
                )
                selected = LocalBHistoricalRecoveryRuleCandidate(engine).for_original(
                    source, SAMPLED
                )
                self.assertIsNotNone(selected)
                assert selected is not None
                self.assertEqual(selected["snapshot_id"], str(snapshot_id))
                self.assertEqual(selected["kind"], "threshold_high")
                bound = bind_original_recovery_rule(original, selected)
                self.assertIsNotNone(bound)
                assert bound is not None
                self.assertEqual(
                    (bound.comparison, bound.threshold_c, bound.min_samples),
                    ("<=", 7, 3),
                )
                with self.assertRaises(ValueError):
                    LocalBHistoricalRecoveryRuleCandidate(engine).for_original(
                        source, SAMPLED + timedelta(seconds=1)
                    )
            finally:
                engine.dispose()
