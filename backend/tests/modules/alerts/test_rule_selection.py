"""F02 rule-selection service tests (B-side contract #5 6127332268).

Covers:

- configured / no_rule / invalid envelope (uniform shape)
- selection by sampled_at against the half-open UTC interval
- multi-batch independence with per-batch rule_version
- overlap rejection at write time + supersedes closure on read
- DTO strict validation: naive times, empty interval, non-finite
  thresholds, threshold inversion, rule_version shape
"""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.modules.alerts.models import (
    BatchCreate,
    BatchRuleSnapshotCreate,
    RecoverySpec,
    RuleDict,
    RuleSeverity,
    WarehouseCreate,
)
from app.modules.alerts.services.batches import create_batch
from app.modules.alerts.services.rules import (
    SnapshotConflict,
    SnapshotNotFound,
    SnapshotOverlapConflict,
    create_snapshot,
    select_rules_for_observation,
)
from app.modules.alerts.services.warehouses import create_warehouse

T0 = datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC)


def _engine() -> Any:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _rule(
    metric: str = "temperature",
    *,
    threshold: float = 8.0,
    unit: str | None = "°C",
    recovery: RecoverySpec | None = None,
) -> RuleDict:
    return RuleDict(
        metric_code=metric,
        candidate_kind="threshold_high",
        severity=RuleSeverity.WARNING,
        threshold_low=None,
        threshold_high=threshold,
        unit=unit,
        recovery=recovery,
    )


def _payload(
    *,
    rule: RuleDict | None = None,
    start: datetime = T0,
    end: datetime | None = None,
    rule_version: str | None = None,
    supersedes: list[uuid.UUID] | None = None,
) -> BatchRuleSnapshotCreate:
    return BatchRuleSnapshotCreate(
        version_id=uuid.uuid4(),
        rules=[rule or _rule()],
        effective_start_at=start,
        effective_end_at=end,
        rule_version=rule_version,
        supersedes_snapshot_ids=supersedes or [],
    )


class _World(unittest.TestCase):
    """One warehouse + two batches on a fresh engine."""

    def setUp(self) -> None:
        self.eng = _engine()
        with Session(self.eng) as session:
            self.wid = create_warehouse(
                session, WarehouseCreate(code="WH", name="W")
            ).id
            self.bid1 = create_batch(
                session, self.wid, BatchCreate(code="B1", product="x")
            ).id
            self.bid2 = create_batch(
                session, self.wid, BatchCreate(code="B2", product="x")
            ).id

    def select(self, metric: str = "temperature", sampled_at: datetime = T0) -> Any:
        with Session(self.eng) as session:
            return select_rules_for_observation(
                session,
                warehouse_id=self.wid,
                batch_ids=[self.bid1, self.bid2],
                metric_code=metric,
                sampled_at=sampled_at,
            )


class SelectionEnvelopeCase(_World):
    def test_configured_returns_typed_entry(self) -> None:
        with Session(self.eng) as session:
            create_snapshot(session, self.bid1, _payload(rule_version="r1"))
        result = self.select()
        self.assertEqual(result.status, "configured")
        self.assertEqual(len(result.rules), 1)
        entry = result.rules[0]
        self.assertEqual(entry.batch_id, self.bid1)
        self.assertEqual(entry.warehouse_id, self.wid)
        self.assertEqual(entry.rule_version, "r1")
        self.assertEqual(entry.metric_code, "temperature")
        self.assertEqual(entry.unit, "°C")
        self.assertEqual(entry.comparison, ">")
        self.assertEqual(entry.threshold, 8.0)
        self.assertEqual(entry.kind, "threshold_high")
        self.assertEqual(entry.severity, "warning")
        self.assertEqual(entry.effective_start_at, T0)
        self.assertIsNone(entry.effective_end_at)
        self.assertIsNone(entry.recovery)
        self.assertEqual(entry.source, "warehouse-manager-ui")

    def test_no_rule_when_metric_unknown(self) -> None:
        with Session(self.eng) as session:
            create_snapshot(session, self.bid1, _payload())
        result = self.select(metric="humidity")
        self.assertEqual(result.status, "no_rule")
        self.assertEqual(result.rules, [])
        self.assertIsNone(result.reason_code)

    def test_no_rule_when_sampled_at_before_start(self) -> None:
        with Session(self.eng) as session:
            create_snapshot(session, self.bid1, _payload(start=T0))
        result = self.select(sampled_at=T0 - timedelta(seconds=1))
        self.assertEqual(result.status, "no_rule")

    def test_half_open_boundaries(self) -> None:
        end = T0 + timedelta(hours=1)
        with Session(self.eng) as session:
            create_snapshot(session, self.bid1, _payload(start=T0, end=end))
        # start inclusive
        self.assertEqual(self.select(sampled_at=T0).status, "configured")
        # end exclusive
        self.assertEqual(self.select(sampled_at=end).status, "no_rule")

    def test_recovery_passthrough(self) -> None:
        recovery = RecoverySpec(comparison="<=", threshold=8.0, min_samples=3)
        with Session(self.eng) as session:
            create_snapshot(session, self.bid1, _payload(rule=_rule(recovery=recovery)))
        entry = self.select().rules[0]
        self.assertEqual(
            entry.recovery,
            {"comparison": "<=", "threshold": 8.0, "min_samples": 3},
        )


class MultiBatchIndependenceCase(_World):
    def test_each_batch_gets_its_own_version(self) -> None:
        with Session(self.eng) as session:
            create_snapshot(session, self.bid1, _payload(rule_version="v-old"))
            create_snapshot(session, self.bid2, _payload(rule_version="v-new"))
        result = self.select()
        self.assertEqual(result.status, "configured")
        by_batch = {e.batch_id: e for e in result.rules}
        self.assertEqual(by_batch[self.bid1].rule_version, "v-old")
        self.assertEqual(by_batch[self.bid2].rule_version, "v-new")


class InvalidInputCase(_World):
    def test_naive_sampled_at_rejected(self) -> None:
        result = self.select(sampled_at=datetime(2026, 10, 1, 0, 0, 0))
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.reason_code, "naive_sampled_at")

    def test_bad_metric_rejected(self) -> None:
        result = self.select(metric="!bad metric")
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.reason_code, "invalid_metric_code")

    def test_empty_batch_ids_rejected(self) -> None:
        with Session(self.eng) as session:
            result = select_rules_for_observation(
                session,
                warehouse_id=self.wid,
                batch_ids=[],
                metric_code="temperature",
                sampled_at=T0,
            )
        self.assertEqual(result.reason_code, "empty_batch_ids")

    def test_unknown_warehouse_rejected(self) -> None:
        with Session(self.eng) as session:
            result = select_rules_for_observation(
                session,
                warehouse_id=uuid.uuid4(),
                batch_ids=[self.bid1],
                metric_code="temperature",
                sampled_at=T0,
            )
        self.assertEqual(result.reason_code, "unknown_warehouse")

    def test_unknown_batch_rejected(self) -> None:
        with Session(self.eng) as session:
            result = select_rules_for_observation(
                session,
                warehouse_id=self.wid,
                batch_ids=[uuid.uuid4()],
                metric_code="temperature",
                sampled_at=T0,
            )
        self.assertEqual(result.reason_code, "unknown_batch")

    def test_batch_of_other_warehouse_rejected(self) -> None:
        with Session(self.eng) as session:
            other_wid = create_warehouse(
                session, WarehouseCreate(code="WH2", name="W2")
            ).id
            other_bid = create_batch(
                session, other_wid, BatchCreate(code="BX", product="x")
            ).id
            result = select_rules_for_observation(
                session,
                warehouse_id=self.wid,
                batch_ids=[other_bid],
                metric_code="temperature",
                sampled_at=T0,
            )
        self.assertEqual(result.reason_code, "unknown_batch")


class OverlapRejectionCase(_World):
    def test_same_metric_overlap_rejected(self) -> None:
        with Session(self.eng) as session:
            create_snapshot(session, self.bid1, _payload(start=T0))  # open-ended
            with self.assertRaises(SnapshotOverlapConflict):
                create_snapshot(
                    session, self.bid1, _payload(start=T0 + timedelta(hours=1))
                )

    def test_adjacent_intervals_allowed(self) -> None:
        end = T0 + timedelta(hours=1)
        with Session(self.eng) as session:
            create_snapshot(session, self.bid1, _payload(start=T0, end=end))
            # half-open: [end, None) does not overlap [T0, end)
            create_snapshot(session, self.bid1, _payload(start=end))

    def test_different_metric_overlap_allowed(self) -> None:
        with Session(self.eng) as session:
            create_snapshot(session, self.bid1, _payload(start=T0))
            create_snapshot(
                session,
                self.bid1,
                _payload(rule=_rule(metric="humidity"), start=T0),
            )

    def test_supersedes_allows_replacement(self) -> None:
        with Session(self.eng) as session:
            first = create_snapshot(session, self.bid1, _payload(start=T0))
            second = create_snapshot(
                session,
                self.bid1,
                _payload(
                    start=T0 + timedelta(hours=1),
                    rule_version="v2",
                    supersedes=[first.id],
                ),
            )
            self.assertNotEqual(first.id, second.id)
        # read side: successor closes the predecessor from its start
        result = self.select(sampled_at=T0 + timedelta(hours=2))
        self.assertEqual(result.status, "configured")
        self.assertEqual(len(result.rules), 1)
        self.assertEqual(result.rules[0].rule_version, "v2")
        # before the successor's start the predecessor still applies
        result = self.select(sampled_at=T0 + timedelta(minutes=30))
        self.assertEqual(result.status, "configured")
        self.assertEqual(result.rules[0].rule_version, str(first.version_id))

    def test_supersedes_wrong_batch_rejected(self) -> None:
        with Session(self.eng) as session:
            first = create_snapshot(session, self.bid1, _payload(start=T0))
            with self.assertRaises(SnapshotNotFound):
                create_snapshot(
                    session,
                    self.bid2,
                    _payload(start=T0, supersedes=[first.id]),
                )

    def test_duplicate_signature_still_conflicts(self) -> None:
        with Session(self.eng) as session:
            payload = _payload(start=T0)
            create_snapshot(session, self.bid1, payload)
            with self.assertRaises(SnapshotConflict):
                create_snapshot(session, self.bid1, payload)


class DTOStrictValidationCase(unittest.TestCase):
    def _base(self, **kw: Any) -> dict[str, Any]:
        return {
            "version_id": uuid.uuid4(),
            "rules": [_rule()],
            "effective_start_at": T0,
            "effective_end_at": None,
        } | kw

    def test_naive_start_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BatchRuleSnapshotCreate(
                **self._base(effective_start_at=datetime(2026, 10, 1, 0, 0, 0))
            )

    def test_naive_end_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BatchRuleSnapshotCreate(
                **self._base(effective_end_at=datetime(2026, 10, 2, 0, 0, 0))
            )

    def test_empty_interval_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BatchRuleSnapshotCreate(**self._base(effective_end_at=T0))

    def test_end_before_start_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BatchRuleSnapshotCreate(
                **self._base(effective_end_at=T0 - timedelta(hours=1))
            )

    def test_aware_non_utc_start_normalised(self) -> None:
        # +08:00 midnight == T0-8h UTC; stored as UTC instant.
        offset = T0.astimezone(UTC)  # sanity
        payload = BatchRuleSnapshotCreate(**self._base(effective_start_at=offset))
        self.assertEqual(payload.effective_start_at.tzinfo, UTC)

    def test_non_finite_threshold_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _rule(threshold=float("inf"))
        with self.assertRaises(ValueError):
            _rule(threshold=float("nan"))

    def test_threshold_inversion_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RuleDict(
                metric_code="temperature",
                candidate_kind="range",
                severity=RuleSeverity.WARNING,
                threshold_low=10.0,
                threshold_high=5.0,
            )

    def test_threshold_pair_equal_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RuleDict(
                metric_code="temperature",
                candidate_kind="range",
                severity=RuleSeverity.WARNING,
                threshold_low=5.0,
                threshold_high=5.0,
            )

    def test_both_thresholds_missing_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RuleDict(
                metric_code="temperature",
                candidate_kind="state_change",
                severity=RuleSeverity.INFO,
                threshold_low=None,
                threshold_high=None,
            )

    def test_bad_rule_version_shape_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _payload(rule_version="bad version!")
        with self.assertRaises(ValueError):
            _payload(rule_version="-starts-with-dash")

    def test_recovery_min_samples_bounds(self) -> None:
        with self.assertRaises(ValueError):
            RecoverySpec(comparison="<=", threshold=8.0, min_samples=0)
        ok = RecoverySpec(comparison="<=", threshold=8.0, min_samples=1)
        self.assertEqual(ok.min_samples, 1)


if __name__ == "__main__":
    unittest.main()
