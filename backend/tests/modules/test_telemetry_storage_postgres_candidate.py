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
from uuid import UUID, uuid4

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateSchema, DropSchema

from app.modules.telemetry.history_candidate import (
    HistoryFiltersCandidate,
    list_history_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox, outbox_conflicts
from app.modules.telemetry.rule_evaluation_candidate import evaluate_one_batch_candidate
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
                            "kind": "temperature_high",
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
