"""Isolated C/B rule-selection adapter checks; no shared app or real B database."""

import csv
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel

from app.modules.telemetry.history_candidate import HistoryFiltersCandidate
from app.modules.telemetry.outbox_candidate import (
    outbox,
    outbox_conflicts,
    stage_f03_candidate,
)
from app.modules.telemetry.recovery_rule_candidate import bind_original_recovery_rule
from app.modules.telemetry.rule_evaluation_candidate import (
    evaluate_one_batch_candidate,
    mark_evaluation_on_connection_candidate,
    stage_evaluated_high_on_connection_candidate,
    stage_gated_high_candidate,
)
from app.modules.telemetry.snapshot_candidate import (
    create_snapshot_candidate,
    read_snapshot_page_candidate,
    render_snapshot_csv_candidate,
)
from app.modules.telemetry.storage_candidate import (
    evaluation_jobs,
    ingest_observation,
    metadata,
    observations,
)

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
BATCH = UUID("33333333-3333-4333-8333-333333333333")
SNAPSHOT = UUID("88888888-8888-4888-8888-888888888888")
NOW = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Reception:
    raw_text: str
    reception_id: UUID
    authenticated_device_id: UUID = DEVICE
    warehouse_id: UUID = WAREHOUSE
    received_at: datetime = NOW

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


class Selector:
    def __init__(self, reply: dict | Exception):
        self.reply = reply
        self.calls: list[dict] = []

    def select_rules_for_observation(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _rule(**changes: object) -> dict:
    entry = {
        "batch_id": str(BATCH),
        "warehouse_id": str(WAREHOUSE),
        "rule_version": "temp-v1",
        "snapshot_id": str(SNAPSHOT),
        "metric_code": "temperature",
        "unit": "°C",
        "kind": "threshold_high",
        "severity": "warning",
        "comparison": ">",
        "threshold": 8.0,
        "recovery": {"consecutive_samples": 3},
        "effective_start_at": "2026-10-14T00:00:00Z",
        "effective_end_at": None,
        "source": "warehouse-manager-ui",
    }
    entry.update(changes)
    return {"status": "configured", "rules": [entry], "reason_code": None}


class RuleEvaluationCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "candidate.db"
        self.engine = create_engine(f"sqlite:///{self.path}")
        metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def _ingest(self, temperature: float, *, humidity: bool = True) -> tuple[UUID, str]:
        metrics = [{"key": "temperature", "value": temperature, "unit": "°C"}]
        if humidity:
            metrics.append({"key": "humidity", "value": 75, "unit": "%RH"})
        raw = json.dumps(
            {
                "schema_version": "1.0",
                "device_id": str(DEVICE),
                "message_id": str(uuid4()),
                "sampled_at": "2026-10-15T00:00:00Z",
                "metrics": metrics,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result = ingest_observation(self.engine, Reception(raw, uuid4()))
        self.assertEqual(result.status, "accepted_new")
        assert result.observation_id is not None
        return result.observation_id, raw

    def _read(self, table) -> list[dict]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(table)).mappings()]

    def _preselected_high(self) -> tuple[UUID, dict]:
        observation_id, _ = self._ingest(13)
        result = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=Selector(_rule()),
            now=NOW,
        )
        self.assertEqual(result.status, "candidate_staged")
        body = self._read(outbox)[0]["body"]
        with self.engine.begin() as connection:
            connection.execute(delete(outbox))
            connection.execute(update(evaluation_jobs).values(state="pending"))
            connection.execute(update(observations).values(risk={"state": "unknown"}))
        return observation_id, body

    def test_actual_b_f02_rule_kind_projects_to_c_f03_and_recovery(self) -> None:
        """Exercise B's real SQLite service DTO, not a handwritten F02 reply."""
        if sys.version_info < (3, 14):  # noqa: UP036 - local 3.13 can run C-only tests
            self.skipTest("B service uses Python 3.14 syntax")
        from app.modules.alerts.models import (
            Batch,
            BatchCreate,
            BatchRuleSnapshot,
            BatchRuleSnapshotCreate,
            RecoverySpec,
            RuleDict,
            RuleSeverity,
            Warehouse,
            WarehouseCreate,
        )
        from app.modules.alerts.services.batches import create_batch
        from app.modules.alerts.services.rules import (
            create_snapshot,
            select_rules_for_observation,
        )
        from app.modules.alerts.services.warehouses import create_warehouse

        SQLModel.metadata.create_all(
            self.engine,
            tables=[
                Warehouse.__table__,
                Batch.__table__,
                BatchRuleSnapshot.__table__,
            ],
        )
        with Session(self.engine) as session:
            warehouse_id = create_warehouse(
                session, WarehouseCreate(code="wh-c-f02", name="F02 test")
            ).id
            batch_id = create_batch(
                session,
                warehouse_id,
                BatchCreate(code="batch-c-f02", product="apple", started_at=NOW),
            ).id
            create_snapshot(
                session,
                batch_id,
                BatchRuleSnapshotCreate(
                    version_id=uuid4(),
                    rule_version="b-f02-v1",
                    effective_start_at=NOW - timedelta(days=1),
                    rules=[
                        RuleDict(
                            metric_code="temperature",
                            candidate_kind="threshold_high",
                            severity=RuleSeverity.WARNING,
                            threshold_high=8.0,
                            unit="°C",
                            recovery=RecoverySpec(
                                comparison="<=", threshold=7.0, min_samples=3
                            ),
                        )
                    ],
                ),
            )
        # C's device/warehouse identity must match B's trusted association.
        raw = json.dumps(
            {
                "schema_version": "1.0",
                "device_id": str(DEVICE),
                "message_id": str(uuid4()),
                "sampled_at": "2026-10-15T00:00:00Z",
                "metrics": [
                    {"key": "temperature", "value": 13.0, "unit": "°C"},
                    {"key": "humidity", "value": 75, "unit": "%RH"},
                ],
            },
            ensure_ascii=False,
        )
        created = ingest_observation(
            self.engine,
            Reception(raw, uuid4(), warehouse_id=warehouse_id),
        )
        self.assertEqual(created.status, "accepted_new")
        assert created.observation_id is not None

        class RealBSelector:
            def __init__(self) -> None:
                self.reply: dict | None = None

            def select_rules_for_observation(self, **kwargs) -> dict:
                with Session(self_engine) as session:
                    self.reply = select_rules_for_observation(
                        session, **kwargs
                    ).model_dump(mode="json")
                return self.reply

        self_engine = self.engine
        selector = RealBSelector()
        evaluated = evaluate_one_batch_candidate(
            self.engine,
            observation_id=created.observation_id,
            batch_id=batch_id,
            selector=selector,
            now=NOW,
        )
        self.assertEqual(evaluated.status, "candidate_staged")
        assert selector.reply is not None
        self.assertEqual(selector.reply["rules"][0]["kind"], "threshold_high")
        body = self._read(outbox)[0]["body"]
        self.assertEqual(body["kind"], "temperature_high")
        recovered = bind_original_recovery_rule(body, selector.reply["rules"][0])
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual((recovered.comparison, recovered.min_samples), ("<=", 3))

    def test_gated_high_commits_once_and_rolls_back_noop_replay(self) -> None:
        """Mock A gate on SQLite; this does not prove the real PG row lock."""
        observation_id, body = self._preselected_high()
        epoch = uuid4()

        def simulated_gate(connection, **_kwargs):
            # SQLite's deferred BEGIN needs a write before the outbox SAVEPOINT.
            connection.execute(
                update(evaluation_jobs)
                .where(evaluation_jobs.c.observation_id == observation_id)
                .values(state=evaluation_jobs.c.state)
            )
            return 1

        with patch(
            "app.modules.telemetry.rule_evaluation_candidate.begin_evidence_write",
            side_effect=simulated_gate,
        ) as gate:
            result = stage_gated_high_candidate(
                self.engine,
                device_id=DEVICE,
                warehouse_id=WAREHOUSE,
                stream_epoch=epoch,
                observation_id=observation_id,
                batch_id=BATCH,
                body=body,
                now=NOW,
            )
            self.assertEqual(result.status, "candidate_staged")
            self.assertEqual(
                gate.call_args.kwargs,
                {"device_id": DEVICE, "warehouse_id": WAREHOUSE, "epoch": epoch},
            )
            self.assertEqual(
                self._read(evaluation_jobs)[0]["state"], "candidate_staged"
            )
            self.assertEqual(self._read(observations)[0]["risk"]["state"], "candidate")
            self.assertEqual(len(self._read(outbox)), 1)
            replay = stage_gated_high_candidate(
                self.engine,
                device_id=DEVICE,
                warehouse_id=WAREHOUSE,
                stream_epoch=epoch,
                observation_id=observation_id,
                batch_id=BATCH,
                body=body,
                now=NOW,
            )
            self.assertEqual(replay.status, "not_due")
            self.assertEqual(len(self._read(outbox)), 1)

    def test_gated_high_rejects_wrong_owner_and_rolls_back(self) -> None:
        observation_id, body = self._preselected_high()
        with patch(
            "app.modules.telemetry.rule_evaluation_candidate.begin_evidence_write",
            return_value=1,
        ):
            with self.assertRaisesRegex(ValueError, "does not belong"):
                stage_gated_high_candidate(
                    self.engine,
                    device_id=uuid4(),
                    warehouse_id=WAREHOUSE,
                    stream_epoch=uuid4(),
                    observation_id=observation_id,
                    batch_id=BATCH,
                    body=body,
                    now=NOW,
                )
        self.assertEqual(self._read(outbox), [])
        self.assertEqual(self._read(evaluation_jobs)[0]["state"], "pending")
        self.assertEqual(self._read(observations)[0]["risk"]["state"], "unknown")

    def test_gated_high_conflict_commits_hold_without_changing_risk(self) -> None:
        observation_id, body = self._preselected_high()
        source_id = UUID(body["source_event_id"])
        self.assertEqual(
            stage_f03_candidate(
                self.engine,
                source_event_id=source_id,
                observation_id=observation_id,
                body=body,
                now=NOW,
            ),
            "staged",
        )
        changed = {**body, "severity": "critical"}
        epoch = uuid4()

        def simulated_gate(connection, **_kwargs):
            connection.execute(
                update(evaluation_jobs)
                .where(evaluation_jobs.c.observation_id == observation_id)
                .values(state=evaluation_jobs.c.state)
            )
            return 1

        with (
            patch(
                "app.modules.telemetry.rule_evaluation_candidate.begin_evidence_write",
                side_effect=simulated_gate,
            ),
            patch(
                "app.modules.telemetry.rule_evaluation_candidate.mark_uncertain"
            ) as hold,
        ):
            result = stage_gated_high_candidate(
                self.engine,
                device_id=DEVICE,
                warehouse_id=WAREHOUSE,
                stream_epoch=epoch,
                observation_id=observation_id,
                batch_id=BATCH,
                body=changed,
                now=NOW,
            )
            self.assertEqual(result.status, "held_unknown")
            hold.assert_called_once()
            self.assertEqual(
                hold.call_args.kwargs, {"device_id": DEVICE, "epoch": epoch}
            )
        self.assertEqual(self._read(evaluation_jobs)[0]["state"], "pending")
        self.assertEqual(self._read(observations)[0]["risk"]["state"], "unknown")
        self.assertEqual(len(self._read(outbox_conflicts)), 1)

    def test_gated_high_gate_failure_never_writes_c_rows(self) -> None:
        observation_id, body = self._preselected_high()
        with patch(
            "app.modules.telemetry.rule_evaluation_candidate.begin_evidence_write",
            side_effect=RuntimeError("frontier unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "frontier unavailable"):
                stage_gated_high_candidate(
                    self.engine,
                    device_id=DEVICE,
                    warehouse_id=WAREHOUSE,
                    stream_epoch=uuid4(),
                    observation_id=observation_id,
                    batch_id=BATCH,
                    body=body,
                    now=NOW,
                )
        self.assertEqual(self._read(outbox), [])
        self.assertEqual(self._read(evaluation_jobs)[0]["state"], "pending")
        self.assertEqual(self._read(observations)[0]["risk"]["state"], "unknown")

    def test_evaluation_write_shares_caller_transaction(self) -> None:
        observation_id, _ = self._ingest(11)
        candidate_risk = {"state": "candidate", "reason": "threshold_exceeded"}
        with self.engine.connect() as connection:
            with self.assertRaisesRegex(ValueError, "open caller transaction"):
                mark_evaluation_on_connection_candidate(
                    connection,
                    observation_id=observation_id,
                    state="candidate_staged",
                    risk=candidate_risk,
                )
            transaction = connection.begin()
            self.assertTrue(
                mark_evaluation_on_connection_candidate(
                    connection,
                    observation_id=observation_id,
                    state="candidate_staged",
                    risk=candidate_risk,
                )
            )
            transaction.rollback()
        self.assertEqual(self._read(evaluation_jobs)[0]["state"], "pending")
        self.assertEqual(self._read(observations)[0]["risk"]["state"], "unknown")
        with self.engine.begin() as connection:
            with self.assertRaisesRegex(ValueError, "no rule"):
                mark_evaluation_on_connection_candidate(
                    connection,
                    observation_id=observation_id,
                    state="no_rule",
                    risk={"state": "normal"},
                )
            self.assertTrue(
                mark_evaluation_on_connection_candidate(
                    connection,
                    observation_id=observation_id,
                    state="candidate_staged",
                    risk=candidate_risk,
                )
            )
        self.assertEqual(self._read(evaluation_jobs)[0]["state"], "candidate_staged")
        self.assertEqual(self._read(observations)[0]["risk"], candidate_risk)

    def test_preselected_high_freeze_and_risk_share_outer_transaction(self) -> None:
        observation_id, raw = self._ingest(13)
        old = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=Selector(_rule()),
            now=NOW,
        )
        self.assertEqual(old.status, "candidate_staged")
        body = self._read(outbox)[0]["body"]
        with self.engine.begin() as connection:
            connection.execute(delete(outbox))
            connection.execute(update(evaluation_jobs).values(state="pending"))
            connection.execute(update(observations).values(risk={"state": "unknown"}))
        with self.engine.connect() as connection:
            with self.assertRaisesRegex(ValueError, "open caller transaction"):
                stage_evaluated_high_on_connection_candidate(
                    connection,
                    observation_id=observation_id,
                    batch_id=BATCH,
                    body=body,
                    now=NOW,
                )
            transaction = connection.begin()
            # SQLite legacy transaction mode needs a write before SAVEPOINT;
            # the real PG caller writes A's evidence revision first.
            connection.execute(
                update(evaluation_jobs)
                .where(evaluation_jobs.c.observation_id == observation_id)
                .values(state="pending")
            )
            staged = stage_evaluated_high_on_connection_candidate(
                connection,
                observation_id=observation_id,
                batch_id=BATCH,
                body=body,
                now=NOW,
            )
            self.assertEqual(staged.status, "candidate_staged")
            transaction.rollback()
        self.assertEqual(self._read(outbox), [])
        self.assertEqual(self._read(evaluation_jobs)[0]["state"], "pending")
        self.assertEqual(self._read(observations)[0]["risk"]["state"], "unknown")
        changed_body = dict(body)
        changed_body["value"] = 7.0
        with self.engine.begin() as connection:
            with self.assertRaisesRegex(ValueError, "differs from stored"):
                stage_evaluated_high_on_connection_candidate(
                    connection,
                    observation_id=observation_id,
                    batch_id=BATCH,
                    body=changed_body,
                    now=NOW,
                )
            changed_evidence = deepcopy(body)
            changed_evidence["evidence"]["raw_value"] = 12
            with self.assertRaisesRegex(ValueError, "differs from raw evidence"):
                stage_evaluated_high_on_connection_candidate(
                    connection,
                    observation_id=observation_id,
                    batch_id=BATCH,
                    body=changed_evidence,
                    now=NOW,
                )
            staged = stage_evaluated_high_on_connection_candidate(
                connection,
                observation_id=observation_id,
                batch_id=BATCH,
                body=body,
                now=NOW,
            )
            self.assertEqual(staged.status, "candidate_staged")
        self.assertEqual(self._read(observations)[0]["raw_text"], raw)
        self.assertEqual(self._read(observations)[0]["risk"]["state"], "candidate")
        self.assertEqual(self._read(evaluation_jobs)[0]["state"], "candidate_staged")
        self.assertEqual(self._read(outbox)[0]["source_event_id"], old.source_event_id)
        with self.engine.begin() as connection:
            connection.execute(update(evaluation_jobs).values(state="pending"))
            connection.execute(update(observations).values(risk={"state": "unknown"}))
            retried = stage_evaluated_high_on_connection_candidate(
                connection,
                observation_id=observation_id,
                batch_id=BATCH,
                body=body,
                now=NOW,
            )
            self.assertEqual(retried.status, "candidate_staged")
        self.assertEqual(len(self._read(outbox)), 1)
        with self.engine.begin() as connection:
            connection.execute(update(evaluation_jobs).values(state="pending"))
            connection.execute(update(observations).values(risk={"state": "unknown"}))
            different_rule = dict(body)
            different_rule["rule_version"] = "temp-v2"
            conflicted = stage_evaluated_high_on_connection_candidate(
                connection,
                observation_id=observation_id,
                batch_id=BATCH,
                body=different_rule,
                now=NOW,
            )
            self.assertEqual(conflicted.status, "held_unknown")
        self.assertEqual(self._read(evaluation_jobs)[0]["state"], "pending")
        self.assertEqual(self._read(observations)[0]["risk"]["state"], "unknown")
        self.assertEqual(len(self._read(outbox)), 1)
        self.assertEqual(len(self._read(outbox_conflicts)), 1)

    def test_hot_missing_humidity_stages_once_without_losing_raw_or_quality(
        self,
    ) -> None:
        observation_id, raw = self._ingest(13, humidity=False)
        actor_id = uuid4()
        filters = HistoryFiltersCandidate(warehouse_id=WAREHOUSE)
        old_snapshot = create_snapshot_candidate(
            self.engine,
            actor_id=actor_id,
            filters=filters,
            policy_version="active-user-read-v1",
            now=NOW,
        )
        selector = Selector(_rule())
        result = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=selector,
            now=NOW,
        )
        self.assertEqual(result.status, "candidate_staged")
        self.assertEqual(selector.calls[0]["batch_ids"], (BATCH,))
        self.assertEqual(selector.calls[0]["warehouse_id"], WAREHOUSE)
        self.assertEqual(selector.calls[0]["sampled_at"], NOW)
        row = self._read(observations)[0]
        self.assertEqual(row["raw_text"], raw)
        self.assertEqual(row["quality"], ["missing_metric:humidity"])
        self.assertEqual(row["risk"]["state"], "candidate")
        sent = self._read(outbox)[0]
        self.assertEqual(sent["source_event_id"], result.source_event_id)
        self.assertEqual(sent["body"]["evidence"]["quality"], row["quality"])
        self.assertEqual(sent["body"]["evidence"]["raw_value"], 13)
        self.assertEqual(sent["body"]["rule_snapshot_id"], str(SNAPSHOT))
        self.assertEqual(self._read(evaluation_jobs)[0]["state"], "candidate_staged")
        new_snapshot = create_snapshot_candidate(
            self.engine,
            actor_id=actor_id,
            filters=filters,
            policy_version="active-user-read-v1",
            now=NOW,
        )

        def projected_version(snapshot_id: UUID) -> tuple[str | None, str]:
            page = read_snapshot_page_candidate(
                self.engine,
                snapshot_id=snapshot_id,
                actor_id=actor_id,
                filters=filters,
                policy_version="active-user-read-v1",
                now=NOW,
            )
            exported = list(
                csv.DictReader(
                    StringIO(
                        render_snapshot_csv_candidate(
                            self.engine,
                            snapshot_id=snapshot_id,
                            actor_id=actor_id,
                            filters=filters,
                            policy_version="active-user-read-v1",
                            now=NOW,
                        ).decode("utf-8-sig")
                    )
                )
            )
            self.assertEqual(page.total, 1)
            self.assertEqual(len(exported), 1)
            self.assertEqual(exported[0]["observation_id"], str(observation_id))
            return page.items[0]["rule_version"], exported[0]["rule_version"]

        self.assertEqual(projected_version(old_snapshot), (None, ""))
        self.assertEqual(projected_version(new_snapshot), ("temp-v1", "temp-v1"))
        again = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=selector,
            now=NOW,
        )
        self.assertEqual(again.status, "not_due")
        self.assertEqual(len(self._read(outbox)), 1)

    def test_no_rule_keeps_unknown_and_normal_needs_complete_quality(self) -> None:
        no_rule_id, _ = self._ingest(13)
        no_rule = Selector({"status": "no_rule", "rules": [], "reason_code": None})
        result = evaluate_one_batch_candidate(
            self.engine,
            observation_id=no_rule_id,
            batch_id=BATCH,
            selector=no_rule,
            now=NOW,
        )
        self.assertEqual(result.status, "no_rule")
        self.assertEqual(self._read(observations)[0]["risk"]["state"], "unknown")

        normal_id, _ = self._ingest(7)
        self.assertEqual(
            evaluate_one_batch_candidate(
                self.engine,
                observation_id=normal_id,
                batch_id=BATCH,
                selector=Selector(_rule()),
                now=NOW,
            ).status,
            "within_rule",
        )
        incomplete_id, _ = self._ingest(7, humidity=False)
        held = evaluate_one_batch_candidate(
            self.engine,
            observation_id=incomplete_id,
            batch_id=BATCH,
            selector=Selector(_rule()),
            now=NOW,
        )
        self.assertEqual(held.status, "held_unknown")
        rows = {row["id"]: row for row in self._read(observations)}
        self.assertEqual(rows[normal_id]["risk"]["state"], "normal")
        self.assertEqual(rows[incomplete_id]["risk"]["state"], "unknown")
        self.assertEqual(self._read(outbox), [])

    def test_transient_selection_failure_retries_and_same_event_survives_job_retry(
        self,
    ) -> None:
        observation_id, _ = self._ingest(13)
        failure = Selector(
            OperationalError("SELECT rules", {}, RuntimeError("synthetic"))
        )
        first = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=failure,
            now=NOW,
        )
        self.assertEqual(first.status, "retryable_failure")
        self.assertEqual(self._read(evaluation_jobs)[0]["state"], "pending")
        self.assertEqual(self._read(outbox), [])
        for error, expected_reason in (
            (TimeoutError("B timed out"), "rule_unavailable"),
            (ConnectionError("B disconnected"), "rule_unavailable"),
            (PermissionError("B auth unavailable"), "rule_auth_unavailable"),
        ):
            failed = evaluate_one_batch_candidate(
                self.engine,
                observation_id=observation_id,
                batch_id=BATCH,
                selector=Selector(error),
                now=NOW,
            )
            self.assertEqual(
                (failed.status, failed.reason),
                ("retryable_failure", expected_reason),
            )
            self.assertEqual(self._read(evaluation_jobs)[0]["state"], "pending")
            self.assertEqual(self._read(observations)[0]["risk"]["state"], "unknown")
            self.assertEqual(self._read(outbox), [])
        selector = Selector(_rule())
        staged = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=selector,
            now=NOW,
        )
        self.assertEqual(staged.status, "candidate_staged")
        body = self._read(outbox)[0]["body"]
        # Simulate a crash after durable staging but before job completion.
        with self.engine.begin() as connection:
            connection.execute(
                update(evaluation_jobs)
                .where(evaluation_jobs.c.observation_id == observation_id)
                .values(state="pending")
            )
        selector.reply = _rule(
            snapshot_id=str(uuid4()), rule_version="retroactive-v2", threshold=9.0
        )
        retried = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=selector,
            now=NOW,
        )
        self.assertEqual(retried.source_event_id, staged.source_event_id)
        self.assertEqual(len(selector.calls), 1)
        self.assertEqual(self._read(outbox)[0]["body"], body)
        self.assertEqual(len(self._read(outbox)), 1)

    def test_invalid_or_mismatched_rule_cannot_mark_high_as_normal(self) -> None:
        observation_id, raw = self._ingest(13)
        for selection in (
            {"status": "invalid", "rules": [], "reason_code": "unknown_batch"},
            _rule(warehouse_id=str(uuid4())),
            _rule(unit="°F"),
            _rule(severity="urgent"),
            _rule(effective_end_at="2026-10-15T00:00:00Z"),
        ):
            result = evaluate_one_batch_candidate(
                self.engine,
                observation_id=observation_id,
                batch_id=BATCH,
                selector=Selector(selection),
                now=NOW,
            )
            self.assertEqual(result.status, "held_unknown", selection)
        row = self._read(observations)[0]
        self.assertEqual(row["raw_text"], raw)
        self.assertEqual(row["risk"]["state"], "unknown")
        self.assertEqual(self._read(outbox), [])

    def test_invalid_time_or_temperature_unit_never_reaches_rule_selector(self) -> None:
        selector = Selector(_rule())
        for bad_field in (
            {"sampled_at": "not-a-time"},
            {"metrics": [{"key": "temperature", "value": 13, "unit": "°F"}]},
        ):
            raw = json.dumps(
                {
                    "schema_version": "1.0",
                    "device_id": str(DEVICE),
                    "message_id": str(uuid4()),
                    "sampled_at": "2026-10-15T00:00:00Z",
                    "metrics": [{"key": "temperature", "value": 13, "unit": "°C"}],
                    **bad_field,
                },
                ensure_ascii=False,
            )
            result = ingest_observation(self.engine, Reception(raw, uuid4()))
            self.assertEqual(result.status, "accepted_new")
            assert result.observation_id is not None
            checked = evaluate_one_batch_candidate(
                self.engine,
                observation_id=result.observation_id,
                batch_id=BATCH,
                selector=selector,
                now=NOW,
            )
            self.assertEqual(checked.status, "not_due")
        self.assertEqual(selector.calls, [])
        self.assertEqual(self._read(outbox), [])
        for row in self._read(observations):
            self.assertEqual(row["risk"]["state"], "unknown")

    def test_interleaved_rule_versions_cannot_stage_two_events_for_one_batch(
        self,
    ) -> None:
        observation_id, _ = self._ingest(13)
        engine = self.engine
        inner_result = None

        class InterleavedSelector:
            def select_rules_for_observation(self, **_kwargs) -> dict:
                nonlocal inner_result
                inner_result = evaluate_one_batch_candidate(
                    engine,
                    observation_id=observation_id,
                    batch_id=BATCH,
                    selector=Selector(_rule()),
                    now=NOW,
                )
                return _rule(
                    snapshot_id=str(uuid4()),
                    rule_version="retroactive-v2",
                    threshold=9.0,
                )

        outer = evaluate_one_batch_candidate(
            self.engine,
            observation_id=observation_id,
            batch_id=BATCH,
            selector=InterleavedSelector(),
            now=NOW,
        )
        self.assertIsNotNone(inner_result)
        self.assertEqual(inner_result.status, "candidate_staged")
        self.assertEqual(outer.status, "held_unknown")
        self.assertEqual(outer.reason, "candidate_body_conflict")
        self.assertEqual(outer.source_event_id, inner_result.source_event_id)
        self.assertEqual(len(self._read(outbox)), 1)
        self.assertEqual(len(self._read(outbox_conflicts)), 1)
        self.assertEqual(
            self._read(observations)[0]["risk"]["rule_snapshot_id"], str(SNAPSHOT)
        )
