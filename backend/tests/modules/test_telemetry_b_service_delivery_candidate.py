"""C outbox to B's actual HTTP route/service, using isolated SQLite files.

The HTTP adapter is in-process TestClient, not a deployed network or real
notification. A lost POST reply is simulated only after B has committed.
"""

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, func, select

from app.modules.telemetry.b_http_candidate import (
    BHttpCandidatePort,
    HttpCandidateReply,
)
from app.modules.telemetry.delivery_candidate import deliver_once_candidate
from app.modules.telemetry.f03_body_candidate import normalize_f03_body_candidate
from app.modules.telemetry.outbox_candidate import outbox, stage_f03_candidate
from app.modules.telemetry.storage_candidate import ingest_observation, metadata

NOW = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Reception:
    raw_text: str
    reception_id: UUID
    authenticated_device_id: UUID
    warehouse_id: UUID
    received_at: datetime = NOW

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


class CandidateToRealBServiceTests(unittest.TestCase):
    def test_lost_post_reply_reconciles_one_persisted_alert(self) -> None:
        if sys.version_info < (3, 14):  # noqa: UP036 - B service uses 3.14 syntax
            self.skipTest("B service needs project Python 3.14")

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlmodel import Session, SQLModel
        from sqlmodel import select as sqlmodel_select

        from app.api import deps as api_deps
        from app.modules.alerts.models import AlertEvent, Batch, Warehouse
        from app.modules.alerts.routes.events import router as events_router

        with tempfile.TemporaryDirectory() as directory:
            c_engine = create_engine(f"sqlite:///{Path(directory) / 'c.db'}")
            b_engine = create_engine(f"sqlite:///{Path(directory) / 'b.db'}")
            try:
                metadata.create_all(c_engine)
                SQLModel.metadata.create_all(b_engine)
                warehouse_id, batch_id, device_id = uuid4(), uuid4(), uuid4()
                with Session(b_engine) as session:
                    session.add(
                        Warehouse(id=warehouse_id, code="c-b-e1", name="E1 isolated")
                    )
                    session.flush()
                    session.add(
                        Batch(
                            id=batch_id,
                            warehouse_id=warehouse_id,
                            code="apples-e1",
                            product="apple",
                            started_at=NOW - timedelta(days=1),
                        )
                    )
                    session.commit()

                raw = {
                    "schema_version": "1.0",
                    "device_id": str(device_id),
                    "message_id": str(uuid4()),
                    "sampled_at": "2026-10-18T00:00:00Z",
                    "metrics": [
                        {"key": "temperature", "value": 13.0, "unit": "°C"},
                        {"key": "humidity", "value": 75, "unit": "%RH"},
                    ],
                }
                saved = ingest_observation(
                    c_engine,
                    _Reception(
                        json.dumps(raw, ensure_ascii=False),
                        uuid4(),
                        device_id,
                        warehouse_id,
                    ),
                )
                self.assertEqual(saved.status, "accepted_new")
                assert saved.observation_id is not None
                source_id, snapshot_id = uuid4(), uuid4()
                body = normalize_f03_body_candidate(
                    {
                        "schema_version": "1.0",
                        "source_event_id": str(source_id),
                        "kind": "temperature_high",
                        "warehouse_id": str(warehouse_id),
                        "batch_id": str(batch_id),
                        "observation_ids": [str(saved.observation_id)],
                        "metric_code": "temperature",
                        "sampled_at": raw["sampled_at"],
                        "value": 13.0,
                        "unit": "°C",
                        "severity": "warning",
                        "rule_snapshot_id": str(snapshot_id),
                        "rule_version": "e1-fixture-v1",
                        "algorithm_version": "threshold-rule/1",
                        "evidence": {
                            "comparison": ">",
                            "threshold": 8.0,
                            "quality": ["ok"],
                        },
                    }
                )
                self.assertEqual(
                    stage_f03_candidate(
                        c_engine,
                        source_event_id=source_id,
                        observation_id=saved.observation_id,
                        body=body,
                        now=NOW,
                    ),
                    "staged",
                )

                app = FastAPI()
                app.include_router(events_router, prefix="/api/v1")

                def _session():
                    with Session(b_engine) as session:
                        yield session

                class _User:
                    id = uuid4()

                app.dependency_overrides[api_deps.get_db] = _session
                app.dependency_overrides[api_deps.get_current_user] = lambda: _User()
                client = TestClient(app)

                class _LostPostReplyTransport:
                    methods: list[str] = []

                    def request(self, method, path, payload=None):
                        self.methods.append(method)
                        response = client.request(
                            method,
                            path,
                            content=payload,
                            headers={"Content-Type": "application/json"},
                        )
                        if method == "POST":
                            raise TimeoutError("synthetic lost response after B commit")
                        return HttpCandidateReply(
                            response.status_code, response.content
                        )

                transport = _LostPostReplyTransport()
                port = BHttpCandidatePort(transport)
                self.assertEqual(
                    deliver_once_candidate(
                        c_engine, source_event_id=source_id, now=NOW, port=port
                    ),
                    "delivered",
                )
                self.assertEqual(transport.methods, ["GET", "POST", "GET"])
                with Session(b_engine) as session:
                    self.assertEqual(
                        session.scalar(select(func.count()).select_from(AlertEvent)), 1
                    )
                    alert = session.exec(
                        sqlmodel_select(AlertEvent).where(
                            AlertEvent.source_event_id == str(source_id)
                        )
                    ).one()
                    self.assertEqual(alert.kind, "temperature_high")
                    self.assertEqual(alert.batch_id, batch_id)
                with c_engine.connect() as connection:
                    row = connection.execute(select(outbox)).mappings().one()
                    self.assertEqual(row["state"], "delivered")
                    self.assertEqual(row["source_event_id"], source_id)
                self.assertEqual(
                    deliver_once_candidate(
                        c_engine,
                        source_event_id=source_id,
                        now=NOW + timedelta(hours=1),
                        port=port,
                    ),
                    "not_due",
                )
                self.assertEqual(transport.methods, ["GET", "POST", "GET"])
            finally:
                c_engine.dispose()
                b_engine.dispose()
