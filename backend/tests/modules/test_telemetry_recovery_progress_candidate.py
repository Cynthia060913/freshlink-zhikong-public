"""Restart and retry behavior of an isolated C recovery-window table."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from app.modules.telemetry.recovery_progress_candidate import (
    advance_persisted_recovery_window,
    load_recovery_progress,
    metadata,
)
from app.modules.telemetry.recovery_window_candidate import (
    RecoveryRuleCandidate,
    RecoverySampleCandidate,
    RecoveryWindowCandidate,
)

_BASE = datetime(2026, 10, 19, tzinfo=UTC)
_DEVICE = UUID("11111111-1111-4111-8111-111111111111")
_WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
_BATCH = UUID("33333333-3333-4333-8333-333333333333")


class RecoveryProgressCandidateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "recovery.db"
        self.engine = create_engine(f"sqlite+pysqlite:///{self.database}")
        self.addCleanup(lambda: self.engine.dispose())
        metadata.create_all(self.engine)  # Isolated test DB only; never production.
        self.initial = RecoveryWindowCandidate(
            original_source_event_id=UUID("77777777-7777-4777-8777-777777777777"),
            device_id=_DEVICE,
            warehouse_id=_WAREHOUSE,
            batch_id=_BATCH,
            anomaly_sampled_at=_BASE,
        )
        self.rule = RecoveryRuleCandidate("<=", 7.0, 3, 90)

    def sample(
        self, number: int, second: int, **changes: object
    ) -> RecoverySampleCandidate:
        result = RecoverySampleCandidate(
            observation_id=UUID(int=number),
            device_id=_DEVICE,
            warehouse_id=_WAREHOUSE,
            batch_id=_BATCH,
            sampled_at=_BASE + timedelta(seconds=second),
            temperature_c=6.5,
            quality=("ok",),
            duplicate="new",
        )
        return replace(result, **changes)

    def advance(
        self, sample: RecoverySampleCandidate
    ) -> tuple[RecoveryWindowCandidate, str]:
        return advance_persisted_recovery_window(
            self.engine, self.initial, self.rule, sample
        )

    def test_restart_retains_three_distinct_samples_and_freezes_evidence(self) -> None:
        self.assertIsNone(
            load_recovery_progress(self.engine, self.initial.original_source_event_id)
        )
        first, decision = self.advance(self.sample(1, 10))
        self.assertEqual(
            (decision, first.observation_ids), ("progress", (UUID(int=1),))
        )
        self.engine.dispose()
        self.engine = create_engine(f"sqlite+pysqlite:///{self.database}")
        restored = load_recovery_progress(
            self.engine, self.initial.original_source_event_id
        )
        self.assertEqual(restored, first)
        self.advance(self.sample(2, 50))
        ready, decision = self.advance(self.sample(3, 130))
        self.assertEqual(decision, "eligible")
        self.assertEqual(ready.observation_ids, (UUID(int=1), UUID(int=2), UUID(int=3)))
        frozen, decision = self.advance(self.sample(4, 140))
        self.assertEqual((frozen, decision), (ready, "frozen"))
        self.assertEqual(
            load_recovery_progress(self.engine, self.initial.original_source_event_id),
            ready,
        )

    def test_duplicate_conflict_late_and_missing_data_cannot_prove_recovery(
        self,
    ) -> None:
        self.advance(self.sample(1, 10))
        duplicate, decision = self.advance(self.sample(1, 10, duplicate="duplicate"))
        self.assertEqual(
            (decision, duplicate.observation_ids), ("duplicate", (UUID(int=1),))
        )
        conflict, decision = self.advance(self.sample(2, 20, duplicate="conflict"))
        self.assertEqual((decision, conflict.observation_ids), ("reset", ()))
        late, decision = self.advance(self.sample(3, 15))
        self.assertEqual((decision, late), ("late", conflict))
        self.advance(self.sample(4, 30))
        missing, decision = self.advance(
            self.sample(5, 40, quality=("missing_metric:temperature",))
        )
        self.assertEqual((decision, missing.observation_ids), ("reset", ()))
        self.assertFalse(missing.eligible)
        self.engine.dispose()
        self.engine = create_engine(f"sqlite+pysqlite:///{self.database}")
        self.assertEqual(
            load_recovery_progress(self.engine, self.initial.original_source_event_id),
            missing,
        )

    def test_original_identity_and_rule_are_immutable(self) -> None:
        first, _ = self.advance(self.sample(1, 10))
        for changed_initial, changed_rule in (
            (replace(self.initial, batch_id=UUID(int=8)), self.rule),
            (self.initial, replace(self.rule, threshold_c=8.0)),
            (self.initial, replace(self.rule, min_samples=4)),
        ):
            with self.subTest(
                changed_initial=changed_initial, changed_rule=changed_rule
            ):
                with self.assertRaises(ValueError):
                    advance_persisted_recovery_window(
                        self.engine, changed_initial, changed_rule, self.sample(2, 20)
                    )
                self.assertEqual(
                    load_recovery_progress(
                        self.engine, self.initial.original_source_event_id
                    ),
                    first,
                )

    def test_no_tables_are_created_by_the_port(self) -> None:
        other = create_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(other.dispose)
        with self.assertRaises(OperationalError):
            advance_persisted_recovery_window(
                other, self.initial, self.rule, self.sample(1, 10)
            )


if __name__ == "__main__":
    unittest.main()
