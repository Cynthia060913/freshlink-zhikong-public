"""Opt-in PostgreSQL transaction check for C's isolated candidate tables.

Only a disposable ``test-db/freshlink_test/FreshLink-test`` may run this test.
It creates a unique schema in that test database; production does not call
``create_all`` and the formal Alembic migration remains a separate task.
"""

import csv
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import create_engine, delete, event, func, select, update
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateSchema, DropSchema

from app.modules.iot.stream_frontier import FrontierStamp
from app.modules.telemetry.history_candidate import (
    HistoryFiltersCandidate,
    list_history_candidate,
)
from app.modules.telemetry.outbox_candidate import (
    outbox,
    outbox_conflicts,
    stage_candidate,
    stage_candidate_on_connection_candidate,
    stage_f03_candidate,
)
from app.modules.telemetry.recovery_outbox_candidate import (
    freeze_recovery_frontier_on_connection_candidate,
    stage_eligible_recovery_on_connection_candidate,
)
from app.modules.telemetry.recovery_progress_candidate import (
    advance_persisted_recovery_window,
    advance_persisted_recovery_window_on_connection_candidate,
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
from app.modules.telemetry.rule_evaluation_candidate import (
    evaluate_one_batch_candidate,
    stage_evaluated_high_on_connection_candidate,
)
from app.modules.telemetry.snapshot_candidate import (
    create_snapshot_candidate,
    read_snapshot_page_candidate,
    render_snapshot_csv_candidate,
)
from app.modules.telemetry.storage_candidate import (
    evaluation_jobs,
    ingest_observation,
    ingest_observation_on_connection_candidate,
    metadata,
    observations,
    quality_tags,
    receptions,
)

_ENABLED = os.getenv("FRESHLINK_C_TEST_POSTGRES") == "1"
_DEVICE_ID = UUID("11111111-1111-4111-8111-111111111111")
_WAREHOUSE_ID = UUID("22222222-2222-4222-8222-222222222222")
_SAMPLED_AT = "2026-10-15T00:00:00Z"
_RECEIVED_AT = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Reception:
    raw_text: str
    reception_id: UUID
    received_at: datetime
    authenticated_device_id: UUID = _DEVICE_ID
    warehouse_id: UUID = _WAREHOUSE_ID

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


def _reception(
    message_id: UUID, *, temperature: int = 11, seconds: int = 0
) -> _Reception:
    raw = {
        "schema_version": "1.0",
        "device_id": str(_DEVICE_ID),
        "message_id": str(message_id),
        "sampled_at": _SAMPLED_AT,
        "metrics": [
            {"key": "temperature", "value": temperature, "unit": "°C"},
            {"key": "humidity", "value": 70, "unit": "%RH"},
        ],
    }
    return _Reception(
        raw_text=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
        reception_id=uuid4(),
        received_at=_RECEIVED_AT + timedelta(seconds=seconds),
    )


@unittest.skipUnless(_ENABLED, "requires FRESHLINK_C_TEST_POSTGRES=1")
class PostgresStorageCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        # Refuse to connect until the exact disposable project/database/host
        # guard passes. A fresh schema per test prevents HTTP data from
        # changing the transaction test's expected row counts.
        from app.core.config import settings

        url = make_url(str(settings.DATABASE_URL))
        if (
            url.get_backend_name() != "postgresql"
            or url.host != "test-db"
            or url.database != "freshlink_test"
            or settings.PROJECT_NAME != "FreshLink-test"
        ):
            raise AssertionError("C PG test requires disposable test-db only")
        self.url = url.set(drivername="postgresql+psycopg")
        self.schema = "c_test_" + uuid4().hex
        self.base_engine = create_engine(self.url, pool_pre_ping=True)
        self.addCleanup(self.base_engine.dispose)
        with self.base_engine.begin() as connection:
            connection.execute(CreateSchema(self.schema))
        self.engine = self.base_engine.execution_options(
            schema_translate_map={None: self.schema}
        )
        self.addCleanup(self._drop_test_schema)
        # Test schema only. Production must use the reviewed Alembic chain.
        metadata.create_all(self.engine)

    def _drop_test_schema(self) -> None:
        metadata.drop_all(self.engine)
        with self.base_engine.begin() as connection:
            # No CASCADE: refuse removal if unexpected objects appeared.
            connection.execute(DropSchema(self.schema))

    def _count(self, table) -> int:
        with self.engine.connect() as connection:
            return int(connection.scalar(select(func.count()).select_from(table)))

    def test_frozen_recovery_fields_share_pg_commit_and_survive_new_connection(
        self,
    ) -> None:
        """Real C PG rows/rollback; A frontier decision is explicitly mocked."""
        original_id, recovery_id = uuid4(), uuid4()
        first_observation, recovery_observation = uuid4(), uuid4()
        epoch = uuid4()
        with self.engine.begin() as connection:
            for observation_id in (first_observation, recovery_observation):
                connection.execute(
                    observations.insert().values(
                        id=observation_id,
                        device_id=_DEVICE_ID,
                        message_id=uuid4(),
                        warehouse_id=_WAREHOUSE_ID,
                        content_sha256="0" * 64,
                        raw_text="{}",
                        raw_payload={},
                        sampled_at_utc=_RECEIVED_AT,
                        first_received_at_utc=_RECEIVED_AT,
                        normalized={},
                        quality=["ok"],
                        risk={"status": "unknown"},
                    )
                )
            connection.execute(
                outbox.insert(),
                [
                    {
                        "source_event_id": original_id,
                        "related_source_event_id": None,
                        "observation_id": first_observation,
                        "body_sha256": "0" * 64,
                        "body": {
                            "kind": "temperature_high",
                            "source_event_id": str(original_id),
                            "warehouse_id": str(_WAREHOUSE_ID),
                            "observation_ids": [str(first_observation)],
                        },
                        "state": "delivered",
                        "attempt_count": 1,
                        "next_attempt_at_utc": _RECEIVED_AT,
                    },
                    {
                        "source_event_id": recovery_id,
                        "related_source_event_id": original_id,
                        "observation_id": recovery_observation,
                        "body_sha256": "1" * 64,
                        "body": {
                            "kind": "temperature_recovered",
                            "source_event_id": str(recovery_id),
                            "related_source_event_id": str(original_id),
                            "warehouse_id": str(_WAREHOUSE_ID),
                            "observation_ids": [str(recovery_observation)],
                        },
                        "state": "pending",
                        "attempt_count": 0,
                        "next_attempt_at_utc": _RECEIVED_AT,
                    },
                ],
            )
        stamp = FrontierStamp(_DEVICE_ID, _WAREHOUSE_ID, epoch, 2, 2, 7)
        frozen = select(outbox.c.frozen_evidence_revision).where(
            outbox.c.source_event_id == recovery_id
        )
        with patch(
            "app.modules.telemetry.recovery_outbox_candidate.stamp_is_current",
            return_value=True,
        ):
            with self.engine.connect() as writer:
                transaction = writer.begin()
                self.assertEqual(
                    freeze_recovery_frontier_on_connection_candidate(
                        writer,
                        recovery_source_event_id=recovery_id,
                        original_source_event_id=original_id,
                        stamp=stamp,
                    ),
                    "frozen",
                )
                with self.engine.connect() as reader:
                    self.assertIsNone(reader.scalar(frozen))
                transaction.rollback()
            with self.engine.connect() as reader:
                self.assertIsNone(reader.scalar(frozen))
            with self.engine.begin() as writer:
                self.assertEqual(
                    freeze_recovery_frontier_on_connection_candidate(
                        writer,
                        recovery_source_event_id=recovery_id,
                        original_source_event_id=original_id,
                        stamp=stamp,
                    ),
                    "frozen",
                )
        reopened = create_engine(self.url).execution_options(
            schema_translate_map={None: self.schema}
        )
        try:
            with reopened.connect() as reader:
                row = (
                    reader.execute(
                        select(outbox).where(outbox.c.source_event_id == recovery_id)
                    )
                    .mappings()
                    .one()
                )
                self.assertEqual(row["frozen_stream_epoch"], epoch)
                self.assertEqual(row["frozen_evidence_revision"], 7)
                self.assertEqual(row["related_source_event_id"], original_id)
        finally:
            reopened.dispose()

    def test_ingest_on_caller_connection_is_invisible_until_pg_commit(self) -> None:
        """A's later device gate can own the same real PG transaction."""
        first = _reception(uuid4(), temperature=11)
        with self.engine.connect() as writer:
            transaction = writer.begin()
            staged = ingest_observation_on_connection_candidate(writer, first)
            self.assertEqual(staged.status, "accepted_new")
            self.assertEqual(self._count(observations), 0)
            self.assertEqual(self._count(receptions), 0)
            transaction.rollback()
        self.assertEqual(self._count(observations), 0)
        self.assertEqual(self._count(evaluation_jobs), 0)
        with self.engine.connect() as writer:
            transaction = writer.begin()
            committed = ingest_observation_on_connection_candidate(writer, first)
            self.assertEqual(committed.status, "accepted_new")
            self.assertEqual(self._count(observations), 0)
            transaction.commit()
        self.assertEqual(self._count(observations), 1)
        self.assertEqual(self._count(receptions), 1)
        self.assertEqual(self._count(evaluation_jobs), 1)
        self.assertEqual(ingest_observation(self.engine, first), committed)

    def test_high_candidate_and_risk_share_caller_pg_commit(self) -> None:
        """Strict two-connection check; A gate itself is a separate integration."""
        batch_id = uuid4()
        ingested = ingest_observation(self.engine, _reception(uuid4(), temperature=13))
        self.assertEqual(ingested.status, "accepted_new")
        assert ingested.observation_id is not None

        class Selector:
            def select_rules_for_observation(self, **_kwargs) -> dict:
                return {
                    "status": "configured",
                    "reason_code": None,
                    "rules": [
                        {
                            "batch_id": str(batch_id),
                            "warehouse_id": str(_WAREHOUSE_ID),
                            "snapshot_id": str(uuid4()),
                            "rule_version": "temp-v1",
                            "metric_code": "temperature",
                            "unit": "°C",
                            "kind": "threshold_high",
                            "severity": "warning",
                            "comparison": ">",
                            "threshold": 8.0,
                            "effective_start_at": "2026-10-14T00:00:00Z",
                            "effective_end_at": None,
                        }
                    ],
                }

        old = evaluate_one_batch_candidate(
            self.engine,
            observation_id=ingested.observation_id,
            batch_id=batch_id,
            selector=Selector(),
            now=_RECEIVED_AT,
        )
        self.assertEqual(old.status, "candidate_staged")
        with self.engine.begin() as connection:
            body = connection.scalar(select(outbox.c.body))
            connection.execute(delete(outbox))
            connection.execute(update(evaluation_jobs).values(state="pending"))
            connection.execute(update(observations).values(risk={"state": "unknown"}))
        assert isinstance(body, dict)
        with self.engine.connect() as writer:
            transaction = writer.begin()
            staged = stage_evaluated_high_on_connection_candidate(
                writer,
                observation_id=ingested.observation_id,
                batch_id=batch_id,
                body=body,
                now=_RECEIVED_AT,
            )
            self.assertEqual(staged.status, "candidate_staged")
            self.assertEqual(self._count(outbox), 0)
            self.assertEqual(self._count(evaluation_jobs), 1)
            transaction.rollback()
        self.assertEqual(self._count(outbox), 0)
        with self.engine.connect() as writer:
            transaction = writer.begin()
            committed = stage_evaluated_high_on_connection_candidate(
                writer,
                observation_id=ingested.observation_id,
                batch_id=batch_id,
                body=body,
                now=_RECEIVED_AT,
            )
            self.assertEqual(committed.status, "candidate_staged")
            self.assertEqual(self._count(outbox), 0)
            transaction.commit()
        self.assertEqual(self._count(outbox), 1)
        with self.engine.connect() as connection:
            state = connection.scalar(select(evaluation_jobs.c.state))
            risk = connection.scalar(select(observations.c.risk))
        self.assertEqual(state, "candidate_staged")
        self.assertEqual(risk["state"], "candidate")

    def test_recovery_progress_uses_caller_pg_transaction(self) -> None:
        self.addCleanup(progress_metadata.drop_all, self.engine)
        progress_metadata.create_all(self.engine)
        initial = RecoveryWindowCandidate(
            original_source_event_id=uuid4(),
            device_id=_DEVICE_ID,
            warehouse_id=_WAREHOUSE_ID,
            batch_id=uuid4(),
            anomaly_sampled_at=_RECEIVED_AT,
        )
        rule = RecoveryRuleCandidate("<=", 7.0, 3, 90)
        sample = RecoverySampleCandidate(
            observation_id=uuid4(),
            device_id=_DEVICE_ID,
            warehouse_id=_WAREHOUSE_ID,
            batch_id=initial.batch_id,
            sampled_at=_RECEIVED_AT + timedelta(seconds=10),
            temperature_c=6.0,
            quality=("ok",),
            duplicate="new",
        )
        with self.engine.connect() as writer:
            transaction = writer.begin()
            staged, decision = (
                advance_persisted_recovery_window_on_connection_candidate(
                    writer, initial, rule, sample
                )
            )
            self.assertEqual(decision, "progress")
            self.assertEqual(self._count(recovery_progress), 0)
            transaction.rollback()
        self.assertEqual(self._count(recovery_progress), 0)
        with self.engine.begin() as writer:
            committed, decision = (
                advance_persisted_recovery_window_on_connection_candidate(
                    writer, initial, rule, sample
                )
            )
            self.assertEqual(decision, "progress")
        self.assertEqual(self._count(recovery_progress), 1)
        self.assertEqual(committed, staged)

    def test_recovery_staging_shares_pg_caller_commit_and_rollback(self) -> None:
        """A future admission lock can own the same PG transaction as C staging.

        This does not prove that the admission lock or continuous frontier is
        implemented: the test supplies an already eligible persisted window.
        """
        self.addCleanup(progress_metadata.drop_all, self.engine)
        progress_metadata.create_all(self.engine)
        original_id = uuid4()
        batch_id = uuid4()
        snapshot_id = uuid4()
        anomaly = ingest_observation(self.engine, _reception(uuid4(), temperature=11))
        self.assertEqual(anomaly.status, "accepted_new")
        assert anomaly.observation_id is not None
        body = {
            "schema_version": "1.0",
            "source_event_id": str(original_id),
            "kind": "temperature_high",
            "warehouse_id": str(_WAREHOUSE_ID),
            "batch_id": str(batch_id),
            "observation_ids": [str(anomaly.observation_id)],
            "metric_code": "temperature",
            "sampled_at": _SAMPLED_AT,
            "value": 11.0,
            "unit": "°C",
            "severity": "warning",
            "rule_snapshot_id": str(snapshot_id),
            "rule_version": "temp-v1",
            "algorithm_version": "threshold-rule/1",
            "evidence": {"comparison": ">", "threshold": 8.0},
        }
        self.assertEqual(
            stage_f03_candidate(
                self.engine,
                source_event_id=original_id,
                observation_id=anomaly.observation_id,
                body=body,
                now=_RECEIVED_AT,
            ),
            "staged",
        )
        historical_rule = {
            "kind": "temperature_high",
            "warehouse_id": str(_WAREHOUSE_ID),
            "batch_id": str(batch_id),
            "metric_code": "temperature",
            "snapshot_id": str(snapshot_id),
            "rule_version": "temp-v1",
            "severity": "warning",
            "unit": "°C",
            "comparison": ">",
            "threshold": 8.0,
            "effective_start_at": "2026-10-14T00:00:00Z",
            "effective_end_at": None,
            "recovery": {"comparison": "<=", "threshold": 7.0, "min_samples": 3},
        }
        initial = RecoveryWindowCandidate(
            original_source_event_id=original_id,
            device_id=_DEVICE_ID,
            warehouse_id=_WAREHOUSE_ID,
            batch_id=batch_id,
            anomaly_sampled_at=_RECEIVED_AT,
        )
        rule = RecoveryRuleCandidate("<=", 7.0, 3, 90)
        for second in (10, 50, 130):
            raw = _reception(uuid4(), temperature=6, seconds=second).raw_payload
            sampled_at = _RECEIVED_AT + timedelta(seconds=second)
            raw["sampled_at"] = sampled_at.isoformat().replace("+00:00", "Z")
            receipt = _Reception(
                raw_text=json.dumps(raw, ensure_ascii=False),
                reception_id=uuid4(),
                received_at=sampled_at,
            )
            saved = ingest_observation(self.engine, receipt)
            self.assertEqual(saved.status, "accepted_new")
            assert saved.observation_id is not None
            window, _ = advance_persisted_recovery_window(
                self.engine,
                initial,
                rule,
                RecoverySampleCandidate(
                    observation_id=saved.observation_id,
                    device_id=_DEVICE_ID,
                    warehouse_id=_WAREHOUSE_ID,
                    batch_id=batch_id,
                    sampled_at=sampled_at,
                    temperature_c=6.0,
                    quality=("ok",),
                    duplicate="new",
                ),
            )
        self.assertTrue(window.eligible)

        def stage(connection):
            return stage_eligible_recovery_on_connection_candidate(
                connection,
                original_source_event_id=original_id,
                historical_rule=historical_rule,
                now=_RECEIVED_AT + timedelta(minutes=3),
            )

        with self.engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    update(outbox)
                    .where(outbox.c.source_event_id == original_id)
                    .values(state="delivered")
                )
                staged = stage(connection)
                self.assertEqual(staged.status, "staged")
                self.assertEqual(stage(connection).status, "duplicate")
                self.assertEqual(
                    connection.scalar(select(func.count()).select_from(outbox)), 2
                )
            finally:
                transaction.rollback()
        self.base_engine.dispose()
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(
                    select(outbox.c.state).where(
                        outbox.c.source_event_id == original_id
                    )
                ),
                "pending",
            )
        self.assertEqual(self._count(outbox), 1)
        with self.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == original_id)
                .values(state="delivered")
            )
            staged = stage(connection)
            self.assertEqual(staged.status, "staged")
        assert staged.source_event_id is not None
        self.base_engine.dispose()
        self.assertEqual(self._count(outbox), 2)
        with self.engine.begin() as connection:
            self.assertEqual(stage(connection).status, "duplicate")

    def test_caller_transaction_keeps_pg_savepoint_replay_and_conflict_atomic(
        self,
    ) -> None:
        receipt = _reception(uuid4())
        ingested = ingest_observation(self.engine, receipt)
        self.assertEqual(ingested.status, "accepted_new")
        assert ingested.observation_id is not None
        source_id = uuid4()
        body = {
            "source_event_id": str(source_id),
            "observation_ids": [str(ingested.observation_id)],
            "kind": "temperature_high",
            "evidence": {"temperature_c": 11},
        }
        self.assertEqual(
            stage_candidate(
                self.engine,
                source_event_id=source_id,
                observation_id=ingested.observation_id,
                body=body,
                now=_RECEIVED_AT,
            ),
            "staged",
        )
        changed = {**body, "evidence": {"temperature_c": 12}}
        with self.engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    update(outbox)
                    .where(outbox.c.source_event_id == source_id)
                    .values(state="pending")
                )
                for payload, expected in ((body, "duplicate"), (changed, "conflict")):
                    self.assertEqual(
                        stage_candidate_on_connection_candidate(
                            connection,
                            source_event_id=source_id,
                            observation_id=ingested.observation_id,
                            body=payload,
                            now=_RECEIVED_AT,
                        ),
                        expected,
                    )
                self.assertEqual(
                    connection.scalar(
                        select(func.count()).select_from(outbox_conflicts)
                    ),
                    1,
                )
            finally:
                transaction.rollback()
        self.base_engine.dispose()
        self.assertEqual(self._count(outbox_conflicts), 0)
        self.assertEqual(
            stage_candidate(
                self.engine,
                source_event_id=source_id,
                observation_id=ingested.observation_id,
                body=changed,
                now=_RECEIVED_AT,
            ),
            "conflict",
        )
        self.assertEqual(self._count(outbox_conflicts), 1)

    def test_interleaved_rule_versions_leave_one_durable_pg_candidate(self) -> None:
        """Two PG workers race on one observation without two business events."""
        receipt = _reception(uuid4(), temperature=13)
        ingested = ingest_observation(self.engine, receipt)
        self.assertEqual(ingested.status, "accepted_new")
        assert ingested.observation_id is not None
        observation_id = ingested.observation_id
        batch_id = UUID("33333333-3333-4333-8333-333333333333")
        warehouse_id = _WAREHOUSE_ID
        barrier = Barrier(2)

        class Selector:
            def __init__(self, version: str, threshold: float) -> None:
                self.version = version
                self.threshold = threshold

            def select_rules_for_observation(self, **_kwargs) -> dict:
                barrier.wait(timeout=10)
                return {
                    "status": "configured",
                    "reason_code": None,
                    "rules": [
                        {
                            "batch_id": str(batch_id),
                            "warehouse_id": str(warehouse_id),
                            "snapshot_id": str(uuid4()),
                            "rule_version": self.version,
                            "metric_code": "temperature",
                            "unit": "°C",
                            "kind": "threshold_high",
                            "severity": "warning",
                            "comparison": ">",
                            "threshold": self.threshold,
                            "effective_start_at": "2026-10-14T00:00:00Z",
                            "effective_end_at": None,
                        }
                    ],
                }

        def evaluate(version: str, threshold: float):
            return evaluate_one_batch_candidate(
                self.engine,
                observation_id=observation_id,
                batch_id=batch_id,
                selector=Selector(version, threshold),
                now=_RECEIVED_AT,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(evaluate, "temp-v1", 8.0)
            second = pool.submit(evaluate, "temp-v2", 9.0)
            results = (first.result(timeout=20), second.result(timeout=20))
        self.assertEqual(
            {result.status for result in results},
            {"candidate_staged", "held_unknown"},
        )
        self.assertEqual(results[0].source_event_id, results[1].source_event_id)
        self.base_engine.dispose()
        with self.engine.connect() as connection:
            frozen = connection.execute(select(outbox)).mappings().one()
            stored = (
                connection.execute(
                    select(observations).where(observations.c.id == observation_id)
                )
                .mappings()
                .one()
            )
        self.assertEqual(self._count(outbox), 1)
        self.assertEqual(self._count(outbox_conflicts), 1)
        self.assertEqual(frozen["source_event_id"], results[0].source_event_id)
        self.assertEqual(stored["raw_text"], receipt.raw_text)
        self.assertEqual(stored["risk"]["state"], "candidate")

    def test_authenticated_http_snapshot_csv_on_disposable_postgres(self) -> None:
        """Real PG UUID auth, fixed CSV membership and scoped errors.

        This is still a candidate schema and a module-only router, never the
        shared application migration or root HTTP mount.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlmodel import Session

        from app.api.deps import get_db
        from app.core.security import create_access_token
        from app.models import User
        from app.modules.telemetry.http_candidate import router

        User.__table__.create(self.engine)
        try:
            reader = User(
                email=f"c-pg-reader-{uuid4().hex}@example.test", hashed_password="test"
            )
            other = User(
                email=f"c-pg-other-{uuid4().hex}@example.test", hashed_password="test"
            )
            disabled = User(
                email=f"c-pg-disabled-{uuid4().hex}@example.test",
                hashed_password="test",
                is_active=False,
            )
            with Session(self.engine) as session:
                session.add_all([reader, other, disabled])
                session.commit()
                for user in (reader, other, disabled):
                    session.refresh(user)

            def test_db():
                with Session(self.engine) as session:
                    yield session

            def headers(user):
                token = create_access_token(str(user.id), timedelta(minutes=10))
                return {"Authorization": f"Bearer {token}"}

            app = FastAPI()
            app.include_router(router, prefix="/api/v1")
            app.dependency_overrides[get_db] = test_db
            receipt = _reception(uuid4(), seconds=30)
            ingested = ingest_observation(self.engine, receipt)
            self.assertEqual(ingested.status, "accepted_new")
            assert ingested.observation_id is not None
            path = "/api/v1/telemetry/observations"
            params = {"warehouse_id": str(_WAREHOUSE_ID)}
            with TestClient(app) as client:
                self.assertEqual(client.get(path + "/").status_code, 401)
                self.assertEqual(
                    client.get(path + "/", headers=headers(disabled)).status_code,
                    400,
                )
                page = client.get(path + "/", params=params, headers=headers(reader))
                self.assertEqual(page.status_code, 200, page.text)
                data = page.json()
                self.assertGreaterEqual(data["total"], 1)
                self.assertIn(
                    str(ingested.observation_id),
                    [item["observation_id"] for item in data["items"]],
                )
                export = client.get(
                    path + "/export.csv",
                    params={**params, "snapshot_id": data["snapshot_id"]},
                    headers=headers(reader),
                )
                self.assertEqual(export.status_code, 200, export.text)
                exported = list(
                    csv.DictReader(StringIO(export.content.decode("utf-8-sig")))
                )
                self.assertEqual(
                    [row["observation_id"] for row in exported],
                    [item["observation_id"] for item in data["items"]],
                )
                detail = client.get(
                    path + f"/{ingested.observation_id}", headers=headers(reader)
                )
                self.assertEqual(detail.status_code, 200, detail.text)
                self.assertEqual(detail.json()["raw_text"], receipt.raw_text)
                self.assertEqual(
                    client.get(
                        path + "/export.csv",
                        params={**params, "snapshot_id": data["snapshot_id"]},
                        headers=headers(other),
                    ).status_code,
                    422,
                )

                def fail_snapshot(_conn, _cursor, statement, _params, _context, _many):
                    if "telemetry_snapshot_candidate" in statement:
                        raise OperationalError(
                            statement, {}, RuntimeError("synthetic PG read fault")
                        )

                event.listen(self.base_engine, "before_cursor_execute", fail_snapshot)
                try:
                    unavailable = client.get(path + "/", headers=headers(reader))
                finally:
                    event.remove(
                        self.base_engine, "before_cursor_execute", fail_snapshot
                    )
                self.assertEqual(unavailable.status_code, 503, unavailable.text)
                self.assertEqual(unavailable.headers["Retry-After"], "30")
        finally:
            User.__table__.drop(self.engine)

    def test_raw_idempotency_rollback_concurrency_and_new_connection(self) -> None:
        message_id = uuid4()
        first = _reception(message_id)
        created = ingest_observation(self.engine, first)
        self.assertEqual(created.status, "accepted_new")
        self.assertEqual(ingest_observation(self.engine, first), created)

        redelivery = ingest_observation(self.engine, _reception(message_id, seconds=1))
        self.assertEqual(redelivery.status, "accepted_duplicate")
        self.assertEqual(redelivery.observation_id, created.observation_id)
        changed = ingest_observation(
            self.engine, _reception(message_id, temperature=12, seconds=2)
        )
        self.assertEqual(changed.status, "conflict")
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(observations).where(
                        observations.c.id == created.observation_id
                    )
                )
                .mappings()
                .one()
            )
        self.assertEqual(row["raw_text"], first.raw_text)
        self.assertEqual(row["normalized"]["metrics"]["temperature"], 11)
        self.assertEqual(row["risk"]["state"], "unknown")
        self.assertEqual(row["quality"], ["ok"])
        self.assertEqual(self._count(quality_tags), 1)
        self.assertEqual(
            [
                row["observation_id"]
                for row in list_history_candidate(
                    self.engine, HistoryFiltersCandidate(quality_in=("ok",))
                ).items
            ],
            [str(created.observation_id)],
        )
        self.assertEqual((self._count(observations), self._count(receptions)), (1, 3))

        rollback_receipt = _reception(uuid4())
        injected = False

        def fail_job_insert(_conn, _cursor, statement, _params, _context, _many):
            nonlocal injected
            if (
                not injected
                and "INSERT INTO" in statement
                and "telemetry_evaluation_candidate" in statement
            ):
                injected = True
                raise OperationalError(statement, {}, RuntimeError("synthetic failure"))

        event.listen(self.base_engine, "before_cursor_execute", fail_job_insert)
        try:
            failed = ingest_observation(self.engine, rollback_receipt)
        finally:
            event.remove(self.base_engine, "before_cursor_execute", fail_job_insert)
        self.assertTrue(injected)
        self.assertEqual(failed.status, "retryable_failure")
        self.assertEqual(failed.reason, "storage_unavailable")
        self.assertEqual(
            (
                self._count(observations),
                self._count(receptions),
                self._count(evaluation_jobs),
            ),
            (1, 3, 1),
        )
        self.assertEqual(
            ingest_observation(self.engine, rollback_receipt).status, "accepted_new"
        )

        racing_id = uuid4()
        racers = (_reception(racing_id, seconds=3), _reception(racing_id, seconds=4))
        gate = Barrier(2)

        def receive(receipt: _Reception):
            gate.wait(timeout=10)
            return ingest_observation(self.engine, receipt)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(receive, receipt) for receipt in racers]
            results = [future.result(timeout=20) for future in futures]
        for index, result in enumerate(results):
            if result.status == "retryable_failure":
                results[index] = ingest_observation(self.engine, racers[index])
        self.assertCountEqual(
            [result.status for result in results],
            ["accepted_new", "accepted_duplicate"],
        )
        self.assertEqual(results[0].observation_id, results[1].observation_id)

        # A fresh engine/connection checks committed rows, not a cached ORM
        # session. This is not a PostgreSQL process/container restart test.
        fresh = create_engine(self.url, pool_pre_ping=True)
        fresh_view: Engine = fresh.execution_options(
            schema_translate_map={None: self.schema}
        )
        try:
            with fresh_view.connect() as connection:
                counts = tuple(
                    int(connection.scalar(select(func.count()).select_from(table)))
                    for table in (
                        observations,
                        quality_tags,
                        receptions,
                        evaluation_jobs,
                    )
                )
                stored = connection.execute(
                    select(observations.c.raw_text).where(
                        observations.c.id == created.observation_id
                    )
                ).scalar_one()
            self.assertEqual(counts, (3, 3, 6, 3))
            self.assertEqual(stored, first.raw_text)
        finally:
            fresh.dispose()

    def test_uncommitted_older_record_does_not_join_existing_snapshot(self) -> None:
        """Two real PG transactions expose a late commit with an older timestamp."""
        baseline = ingest_observation(self.engine, _reception(uuid4(), seconds=10))
        self.assertEqual(baseline.status, "accepted_new")
        actor_id = uuid4()
        filters = HistoryFiltersCandidate(device_id=_DEVICE_ID, quality_in=("ok",))
        now = datetime(2026, 10, 16, tzinfo=UTC)
        late_id = uuid4()
        raw = _reception(uuid4(), temperature=7).raw_payload
        raw["sampled_at"] = "2026-10-14T00:00:00Z"
        writer = self.engine.connect()
        transaction = writer.begin()
        try:
            # Direct test fixture insert remains uncommitted while another
            # connection freezes its one committed SELECT result set.
            writer.execute(
                observations.insert().values(
                    id=late_id,
                    device_id=_DEVICE_ID,
                    message_id=UUID(raw["message_id"]),
                    warehouse_id=_WAREHOUSE_ID,
                    content_sha256="0" * 64,
                    raw_text=json.dumps(raw, ensure_ascii=False),
                    raw_payload=raw,
                    sampled_at_utc=datetime(2026, 10, 14, tzinfo=UTC),
                    first_received_at_utc=datetime(2026, 10, 14, tzinfo=UTC),
                    normalized={"metrics": {"temperature": 7, "humidity": 70}},
                    quality=["ok"],
                    risk={"state": "unknown", "reason": "no_threshold_rule"},
                )
            )
            writer.execute(
                quality_tags.insert().values(observation_id=late_id, tag="ok")
            )
            old = create_snapshot_candidate(
                self.engine,
                actor_id=actor_id,
                filters=filters,
                policy_version="active-user-read-v1",
                now=now,
            )
            transaction.commit()
        finally:
            if transaction.is_active:
                transaction.rollback()
            writer.close()

        def ids(snapshot_id: UUID) -> list[str]:
            page = read_snapshot_page_candidate(
                self.engine,
                snapshot_id=snapshot_id,
                actor_id=actor_id,
                filters=filters,
                policy_version="active-user-read-v1",
                now=now,
                limit=100,
            )
            self.assertIsNone(page.next_after_ordinal)
            result = [item["observation_id"] for item in page.items]
            csv_bytes = render_snapshot_csv_candidate(
                self.engine,
                snapshot_id=snapshot_id,
                actor_id=actor_id,
                filters=filters,
                policy_version="active-user-read-v1",
                now=now,
            )
            csv_ids = [
                row["observation_id"]
                for row in csv.DictReader(StringIO(csv_bytes.decode("utf-8-sig")))
            ]
            self.assertEqual(csv_ids, result)
            self.assertEqual(len(result), page.total)
            return result

        self.assertNotIn(str(late_id), ids(old))
        new = create_snapshot_candidate(
            self.engine,
            actor_id=actor_id,
            filters=filters,
            policy_version="active-user-read-v1",
            now=now,
        )
        self.assertIn(str(late_id), ids(new))


if __name__ == "__main__":
    unittest.main()
