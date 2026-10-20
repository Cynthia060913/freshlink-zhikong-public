"""Persisted-observation to recovery-outbox tests; no B service is contacted."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import create_engine, func, inspect, select, update
from sqlalchemy.exc import OperationalError

from app.modules.telemetry.delivery_candidate import (
    RemoteCandidateReceipt,
    deliver_once_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox, stage_f03_candidate
from app.modules.telemetry.recovery_observation_candidate import (
    advance_saved_recovery_observation_candidate,
)
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
from app.modules.telemetry.recovery_tick_candidate import (
    stage_recovery_page_candidate,
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
_SECOND = UUID("99999999-9999-4999-8999-999999999999")


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


@dataclass
class HistoricalRules:
    selected: dict | None = None
    failure: Exception | None = None

    def __post_init__(self) -> None:
        self.calls: list[tuple[UUID, datetime]] = []

    def for_original(
        self, original_source_event_id: UUID, anomaly_sampled_at: datetime
    ) -> dict | None:
        self.calls.append((original_source_event_id, anomaly_sampled_at))
        if self.failure is not None:
            raise self.failure
        return self.selected


class RecoveryReceiptPort:
    def __init__(self, recovery_id: UUID, digest: str) -> None:
        self.recovery_id = recovery_id
        self.digest = digest
        self.original_status: Literal["open", "resolved"] = "open"
        self.sent = 0

    def lookup(self, source_event_id: UUID) -> RemoteCandidateReceipt | None:
        if source_event_id == _ORIGINAL:
            return RemoteCandidateReceipt(
                _ORIGINAL, "accepted_duplicate", alert_status=self.original_status
            )
        if source_event_id == self.recovery_id and self.sent:
            return RemoteCandidateReceipt(
                self.recovery_id, "accepted_duplicate", self.digest
            )
        return None

    def send(self, body: dict) -> RemoteCandidateReceipt:
        if body["source_event_id"] != str(self.recovery_id):
            raise AssertionError("unexpected recovery source")
        self.sent += 1
        return RemoteCandidateReceipt(self.recovery_id, "accepted_new", self.digest)


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

    def ingest(
        self,
        second: int,
        temperature: float,
        *,
        temperature_unit: str = "°C",
        include_temperature: bool = True,
    ) -> UUID:
        sampled_at = _BASE + timedelta(seconds=second)
        payload = {
            "schema_version": "1.0",
            "device_id": str(_DEVICE),
            "message_id": str(uuid4()),
            "sampled_at": sampled_at.isoformat().replace("+00:00", "Z"),
            "metrics": (
                [{"key": "temperature", "value": temperature, "unit": temperature_unit}]
                if include_temperature
                else []
            )
            + [{"key": "humidity", "value": 70, "unit": "%RH"}],
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

    def test_saved_observations_advance_and_replay_without_extra_evidence(self) -> None:
        ids = [self.ingest(second, 6.5) for second in (10, 50, 130)]
        decisions = []
        for observation_id in ids:
            result = advance_saved_recovery_observation_candidate(
                self.engine,
                original_source_event_id=_ORIGINAL,
                observation_id=observation_id,
                batch_id=_BATCH,
                historical_rule=_selection(),
            )
            decisions.append((result.status, result.decision))
        self.assertEqual(
            decisions,
            [
                ("advanced", "progress"),
                ("advanced", "progress"),
                ("advanced", "eligible"),
            ],
        )
        self.engine.dispose()
        self.engine = create_engine(f"sqlite+pysqlite:///{self.db}")
        replay = advance_saved_recovery_observation_candidate(
            self.engine,
            original_source_event_id=_ORIGINAL,
            observation_id=ids[-1],
            batch_id=_BATCH,
            historical_rule=_selection(),
        )
        self.assertEqual((replay.status, replay.decision), ("advanced", "frozen"))
        with self.engine.connect() as connection:
            row = connection.execute(select(recovery_progress)).mappings().one()
            self.assertEqual(row["observation_ids"], [str(value) for value in ids])
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(outbox)), 1
            )

    def test_invalid_time_replay_after_restart_cannot_reset_newer_progress(self) -> None:
        def advance(observation_id: UUID):
            return advance_saved_recovery_observation_candidate(
                self.engine,
                original_source_event_id=_ORIGINAL,
                observation_id=observation_id,
                batch_id=_BATCH,
                historical_rule=_selection(),
            )

        first = self.ingest(10, 6.5)
        self.assertEqual(advance(first).decision, "progress")
        bad_raw = {
            "schema_version": "1.0",
            "device_id": str(_DEVICE),
            "message_id": str(uuid4()),
            "sampled_at": "not-a-time",
            "metrics": [{"key": "temperature", "value": 6.5, "unit": "°C"}],
        }
        bad_result = ingest_observation(
            self.engine,
            Reception(
                json.dumps(bad_raw), uuid4(), _BASE + timedelta(seconds=20)
            ),
        )
        self.assertEqual(bad_result.status, "accepted_new")
        assert bad_result.observation_id is not None
        bad = bad_result.observation_id
        self.assertEqual(advance(bad).decision, "reset")
        later = self.ingest(30, 6.5)
        self.assertEqual(advance(later).decision, "progress")
        self.engine.dispose()
        self.engine = create_engine(f"sqlite+pysqlite:///{self.db}")
        self.assertEqual(advance(bad).decision, "late")
        self.assertEqual(advance(later).decision, "duplicate")
        with self.engine.connect() as connection:
            saved = connection.execute(select(recovery_progress)).mappings().one()
            self.assertEqual(saved["observation_ids"], [str(later)])
            self.assertEqual(saved["last_received_observation_id"], later)
        self.assertEqual(advance(self.ingest(40, 6.5)).decision, "progress")
        self.assertEqual(advance(self.ingest(50, 6.5)).decision, "eligible")

    def test_saved_missing_unit_rule_or_tampered_quality_never_proves_recovery(
        self,
    ) -> None:
        first = self.ingest(10, 6.5)
        missing_rule = advance_saved_recovery_observation_candidate(
            self.engine,
            original_source_event_id=_ORIGINAL,
            observation_id=first,
            batch_id=_BATCH,
            historical_rule=None,
        )
        self.assertEqual(missing_rule.status, "waiting_rule")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(recovery_progress)),
                0,
            )

        def advance(observation_id: UUID):
            return advance_saved_recovery_observation_candidate(
                self.engine,
                original_source_event_id=_ORIGINAL,
                observation_id=observation_id,
                batch_id=_BATCH,
                historical_rule=_selection(),
            )

        self.assertEqual(advance(first).decision, "progress")
        bad_unit = self.ingest(20, 6.0, temperature_unit="°F")
        bad_unit_result = advance(bad_unit)
        self.assertEqual(
            (bad_unit_result.status, bad_unit_result.decision), ("advanced", "reset")
        )
        missing = self.ingest(25, 6.0, include_temperature=False)
        self.assertEqual(advance(missing).decision, "reset")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(
                    select(recovery_progress.c.observation_ids)
                ).scalar_one(),
                [],
            )
        tampered = self.ingest(27, 6.0)
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(observations).where(observations.c.id == tampered)
                )
                .mappings()
                .one()
            )
            raw = dict(row["raw_payload"])
            raw["metrics"] = [
                {**metric, "unit": "°F"} if metric["key"] == "temperature" else metric
                for metric in raw["metrics"]
            ]
            connection.execute(
                update(observations)
                .where(observations.c.id == tampered)
                .values(raw_payload=raw)
            )
        self.assertEqual(advance(tampered).status, "held_invalid_evidence")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(
                    select(recovery_progress.c.observation_ids)
                ).scalar_one(),
                [],
            )
        clean = self.ingest(30, 6.5)
        with self.engine.begin() as connection:
            connection.execute(
                update(observations)
                .where(observations.c.id == clean)
                .values(quality=["missing_metric:temperature"])
            )
        self.assertEqual(advance(clean).status, "held_invalid_evidence")
        self.assertEqual(advance(first).decision, "late")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(outbox)), 1
            )

    def test_saved_recovery_rejects_changed_original_and_missing_schema(self) -> None:
        recovered = self.ingest(10, 6.5)
        with self.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == _ORIGINAL)
                .values(body_sha256="sha256:" + "0" * 64)
            )
        result = advance_saved_recovery_observation_candidate(
            self.engine,
            original_source_event_id=_ORIGINAL,
            observation_id=recovered,
            batch_id=_BATCH,
            historical_rule=_selection(),
        )
        self.assertEqual(result.status, "held_invalid_evidence")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(recovery_progress)),
                0,
            )
        empty = create_engine(f"sqlite+pysqlite:///{self.directory.name}/empty.db")
        try:
            missing = advance_saved_recovery_observation_candidate(
                empty,
                original_source_event_id=_ORIGINAL,
                observation_id=recovered,
                batch_id=_BATCH,
                historical_rule=_selection(),
            )
            self.assertEqual(
                (missing.status, missing.reason),
                ("retryable_failure", "storage_unavailable"),
            )
            self.assertFalse(inspect(empty).has_table(observations.name))
        finally:
            empty.dispose()

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

    def test_tick_waits_for_original_and_retries_rule_lookup(self) -> None:
        self.progress(10, 50, 130)
        rules = HistoricalRules(_selection())
        pending = stage_recovery_page_candidate(
            self.engine, historical_rules=rules, now=_BASE, limit=1
        )
        self.assertEqual(pending.results[0][1].status, "waiting_original")
        self.assertEqual(rules.calls, [])
        with self.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == _ORIGINAL)
                .values(state="delivered")
            )
        rules.failure = TimeoutError("B unavailable")
        timed_out = stage_recovery_page_candidate(
            self.engine, historical_rules=rules, now=_BASE, limit=1
        )
        self.assertEqual(
            (timed_out.results[0][1].status, timed_out.results[0][1].reason),
            ("retryable_failure", "rule_unavailable"),
        )
        self.assertEqual(rules.calls, [(_ORIGINAL, _BASE)])
        rules.failure = OperationalError(
            "historical rule SELECT", {}, RuntimeError("down")
        )
        read_failed = stage_recovery_page_candidate(
            self.engine, historical_rules=rules, now=_BASE, limit=1
        )
        self.assertEqual(
            (read_failed.results[0][1].status, read_failed.results[0][1].reason),
            ("retryable_failure", "rule_storage_unavailable"),
        )
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(outbox)), 1
            )
        rules.failure, rules.selected = None, None
        missing = stage_recovery_page_candidate(
            self.engine, historical_rules=rules, now=_BASE, limit=1
        )
        self.assertEqual(missing.results[0][1].status, "waiting_rule")
        rules.selected = _selection()
        staged = stage_recovery_page_candidate(
            self.engine, historical_rules=rules, now=_BASE, limit=1
        )
        again = stage_recovery_page_candidate(
            self.engine, historical_rules=rules, now=_BASE, limit=1
        )
        self.assertEqual(staged.results[0][1].status, "staged")
        self.assertEqual(again.results[0][1].status, "duplicate")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(outbox)), 2
            )

    def test_tick_cursor_does_not_hide_a_waiting_window(self) -> None:
        self.progress(10, 50, 130)
        with self.engine.begin() as connection:
            row = connection.execute(select(recovery_progress)).mappings().one()
            copy = dict(row)
            copy["original_source_event_id"] = _SECOND
            connection.execute(recovery_progress.insert().values(**copy))
        rules = HistoricalRules(_selection())
        first = stage_recovery_page_candidate(
            self.engine, historical_rules=rules, now=_BASE, limit=1
        )
        self.assertEqual(first.next_after, _ORIGINAL)
        self.assertEqual(first.results[0][1].status, "waiting_original")
        second = stage_recovery_page_candidate(
            self.engine,
            historical_rules=rules,
            now=_BASE,
            after=first.next_after,
            limit=1,
        )
        self.assertEqual(second.next_after, None)
        self.assertEqual(second.results[0][0], _SECOND)
        self.assertEqual(second.results[0][1].status, "waiting_original")
        self.assertEqual(rules.calls, [])

    def test_tick_missing_table_is_retryable_without_implicit_schema(self) -> None:
        empty_db = Path(self.directory.name) / "unmigrated.db"
        engine = create_engine(f"sqlite+pysqlite:///{empty_db}")
        try:
            page = stage_recovery_page_candidate(
                engine, historical_rules=HistoricalRules(_selection()), now=_BASE
            )
            self.assertEqual(page.status, "retryable_failure")
            self.assertFalse(inspect(engine).has_table(recovery_progress.name))
        finally:
            engine.dispose()

    def test_staged_recovery_retries_same_source_until_b_reports_resolved(self) -> None:
        self.progress(10, 50, 130)
        with self.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == _ORIGINAL)
                .values(state="delivered")
            )
        staged = stage_recovery_page_candidate(
            self.engine,
            historical_rules=HistoricalRules(_selection()),
            now=_BASE + timedelta(minutes=3),
        ).results[0][1]
        self.assertEqual(staged.status, "staged")
        assert staged.source_event_id is not None
        with self.engine.connect() as connection:
            digest = connection.execute(
                select(outbox.c.body_sha256).where(
                    outbox.c.source_event_id == staged.source_event_id
                )
            ).scalar_one()
        b = RecoveryReceiptPort(staged.source_event_id, digest)
        first = deliver_once_candidate(
            self.engine,
            source_event_id=staged.source_event_id,
            now=_BASE + timedelta(minutes=3),
            port=b,
        )
        self.assertEqual(first, "retry")
        b.original_status = "resolved"
        second = deliver_once_candidate(
            self.engine,
            source_event_id=staged.source_event_id,
            now=_BASE + timedelta(minutes=3, seconds=31),
            port=b,
        )
        self.assertEqual(second, "delivered")
        self.assertEqual(b.sent, 1)
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(outbox).where(
                        outbox.c.source_event_id == staged.source_event_id
                    )
                )
                .mappings()
                .one()
            )
            self.assertEqual(row["state"], "delivered")
            self.assertEqual(
                connection.scalar(select(func.count()).select_from(outbox)), 2
            )


if __name__ == "__main__":
    unittest.main()
