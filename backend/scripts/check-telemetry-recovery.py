"""A-owned disposable PG/ASGI proof of C recovery against actual B services.

No scheduler, real notification, network HTTP server or formal C migration is
installed. Candidate C DDL is allowed only after the strict disposable DB gate.
"""

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.main import app
from app.modules.alerts.services.rules import select_rules_for_observation
from app.modules.iot.proof_schema_guard import require_b01_candidate_schema
from app.modules.iot.registry import receive_registered_telemetry
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session
from app.modules.telemetry.b_http_candidate import (
    BHttpCandidatePort,
    HttpCandidateReply,
)
from app.modules.telemetry.delivery_candidate import deliver_once_candidate
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.recovery_observation_candidate import (
    advance_saved_recovery_observation_candidate,
)
from app.modules.telemetry.recovery_progress_candidate import load_recovery_progress
from app.modules.telemetry.recovery_progress_candidate import (
    metadata as progress_metadata,
)
from app.modules.telemetry.recovery_tick_candidate import stage_recovery_page_candidate
from app.modules.telemetry.rule_evaluation_candidate import evaluate_one_batch_candidate
from app.modules.telemetry.storage_candidate import ingest_observation, metadata

C_REVISION = "e6bb8ef25974cdc6c709811c6424302caa68cb99"


class ActualBRulePort:
    """Session-scoped B F02 reads; never reuse a session across C transactions."""

    def __init__(self, warehouse_id: UUID, batch_id: UUID) -> None:
        self.warehouse_id, self.batch_id = warehouse_id, batch_id

    def select_rules_for_observation(self, **kwargs: Any) -> dict[str, Any]:
        with Session(engine) as session:
            return select_rules_for_observation(session, **kwargs).model_dump(
                mode="json"
            )

    def for_original(
        self, _source_id: UUID, sampled_at: datetime
    ) -> dict[str, Any] | None:
        selected = self.select_rules_for_observation(
            warehouse_id=self.warehouse_id,
            batch_ids=(self.batch_id,),
            metric_code="temperature",
            sampled_at=sampled_at,
        )
        assert selected["status"] == "configured" and len(selected["rules"]) == 1
        return selected["rules"][0]


class ActualBAsgiTransport:
    """Actual authenticated B handlers and PG writes, not a fake receipt port."""

    def __init__(self, api: TestClient, headers: dict[str, str]) -> None:
        self.api, self.headers = api, headers
        self.post_count = 0

    def request(
        self, method: str, path: str, body: bytes | None = None
    ) -> HttpCandidateReply:
        if method == "POST":
            self.post_count += 1
        response = self.api.request(
            method,
            path,
            content=body,
            headers={**self.headers, "Content-Type": "application/json"},
        )
        return HttpCandidateReply(response.status_code, response.content)


def run() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    database = urlparse(str(settings.DATABASE_URL))
    if (
        database.hostname != "test-db"
        or database.path != "/freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
        or os.environ.get("TELEMETRY_C_REVISION") != C_REVISION
    ):
        raise RuntimeError("Refusing non-disposable database or unpinned C source")
    target = Path("/results/pg-b-c-recovery.json")
    if target.exists():
        raise RuntimeError("Refusing evidence overwrite")
    require_b01_candidate_schema(engine)
    metadata.create_all(engine)  # C candidate only; not production migration proof.
    progress_metadata.create_all(engine)
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    with TestClient(app) as api:
        login = api.post(
            "/api/v1/login/access-token",
            data={
                "username": settings.FIRST_SUPERUSER,
                "password": settings.FIRST_SUPERUSER_PASSWORD,
            },
        )
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        warehouse = api.post(
            "/api/v1/warehouses/",
            headers=headers,
            json={
                "code": "recovery-proof",
                "name": "Disposable recovery proof",
            },
        )
        assert warehouse.status_code == 201
        warehouse_id = UUID(warehouse.json()["id"])
        batch = api.post(
            f"/api/v1/warehouses/{warehouse_id}/batches",
            headers=headers,
            json={
                "code": "recovery-apple",
                "product": "Simulated apple",
                "started_at": (base - timedelta(days=1)).isoformat(),
            },
        )
        assert batch.status_code == 201
        batch_id = UUID(batch.json()["id"])
        registered = api.post(
            "/api/v1/devices/",
            headers=headers,
            json={
                "code": "recovery-sensor",
                "name": "Recovery test sensor",
                "kind": "temperature_sensor",
            },
        )
        assert registered.status_code == 201
        device_id = UUID(registered.json()["id"])
        binding = api.post(
            f"/api/v1/devices/{device_id}/binding",
            headers=headers,
            json={"warehouse_id": str(warehouse_id)},
        )
        assert binding.status_code == 200

        def create_rule(
            version: str, start: datetime, recovery: float, supersedes: list[str]
        ) -> dict:
            result = api.post(
                f"/api/v1/batches/{batch_id}/rule-snapshots",
                headers=headers,
                json={
                    "version_id": str(uuid4()),
                    "rule_version": version,
                    "effective_start_at": start.isoformat(),
                    "supersedes_snapshot_ids": supersedes,
                    "rules": [
                        {
                            "metric_code": "temperature",
                            "candidate_kind": "temperature_high",
                            "severity": "warning",
                            "threshold_high": 8.0,
                            "unit": "°C",
                            "recovery": {
                                "comparison": "<=",
                                "threshold": recovery,
                                "min_samples": 3,
                            },
                        }
                    ],
                },
            )
            assert result.status_code == 201
            return result.json()

        first_rule = create_rule("recovery-v1", base - timedelta(hours=1), 7.0, [])
        create_rule("recovery-v2", base + timedelta(seconds=5), 4.0, [first_rule["id"]])
        rules = ActualBRulePort(warehouse_id, batch_id)

        def ingest(second: int, value: float, unit: str = "°C") -> UUID:
            sampled = base + timedelta(seconds=second)
            raw = json.dumps(
                {
                    "schema_version": "1.0",
                    "device_id": str(device_id),
                    "message_id": str(uuid4()),
                    "sampled_at": sampled.isoformat(),
                    "metrics": [
                        {"key": "temperature", "value": value, "unit": unit},
                        {"key": "humidity", "value": 70, "unit": "%RH"},
                    ],
                },
                ensure_ascii=False,
            )
            with Session(engine) as session:
                envelope = receive_registered_telemetry(
                    session,
                    authenticated_device_id=device_id,
                    credential_enabled=True,
                    warehouse_exists=warehouse_lookup_for_session(session),
                    topic=f"freshlink/v1/devices/{device_id}/telemetry",
                    payload=raw.encode(),
                    received_at=sampled + timedelta(seconds=1),
                )
            accepted = ingest_observation(engine, envelope)
            assert (
                accepted.status == "accepted_new"
                and accepted.observation_id is not None
            )
            return accepted.observation_id

        hot = ingest(0, 11.0)
        evaluated = evaluate_one_batch_candidate(
            engine, observation_id=hot, batch_id=batch_id, selector=rules, now=base
        )
        assert (
            evaluated.status == "candidate_staged"
            and evaluated.source_event_id is not None
        )
        source = evaluated.source_event_id
        transport = ActualBAsgiTransport(api, headers)
        port = BHttpCandidatePort(transport)
        assert api.get(f"/api/v1/candidates/by-source/{source}").status_code == 401
        assert (
            deliver_once_candidate(engine, source_event_id=source, now=base, port=port)
            == "delivered"
        )
        assert port.lookup(source).alert_status == "open"
        historical = rules.for_original(source, base)
        assert historical["rule_version"] == "recovery-v1"
        assert (
            rules.for_original(source, base + timedelta(seconds=10))["rule_version"]
            == "recovery-v2"
        )

        decisions = []
        observation_ids = []
        for second, unit, expected in (
            (10, "°C", "progress"),
            (20, "°F", "reset"),
            (30, "°C", "progress"),
            (40, "°C", "progress"),
            (50, "°C", "eligible"),
        ):
            observation_id = ingest(second, 6.0, unit)
            observation_ids.append(observation_id)
            advanced = advance_saved_recovery_observation_candidate(
                engine,
                original_source_event_id=source,
                observation_id=observation_id,
                batch_id=batch_id,
                historical_rule=historical,
            )
            assert (advanced.status, advanced.decision) == ("advanced", expected)
            decisions.append(advanced.decision)
            if expected != "eligible":
                assert port.lookup(source).alert_status == "open"

        # Reconnect the pool and reload durable membership before staging.
        engine.dispose()
        saved = load_recovery_progress(engine, source)
        assert saved.eligible and saved.observation_ids == tuple(observation_ids[-3:])
        replay = advance_saved_recovery_observation_candidate(
            engine,
            original_source_event_id=source,
            observation_id=observation_ids[-1],
            batch_id=batch_id,
            historical_rule=historical,
        )
        assert (replay.status, replay.decision) == ("advanced", "frozen")
        staged = stage_recovery_page_candidate(
            engine, historical_rules=rules, now=base + timedelta(seconds=60)
        )
        assert staged.status == "ok" and len(staged.results) == 1
        recovery = staged.results[0][1]
        assert recovery.status == "staged" and recovery.source_event_id is not None
        assert (
            deliver_once_candidate(
                engine,
                source_event_id=recovery.source_event_id,
                now=base + timedelta(seconds=60),
                port=port,
            )
            == "delivered"
        )
        assert port.lookup(source).alert_status == "resolved"
        again = stage_recovery_page_candidate(
            engine, historical_rules=rules, now=base + timedelta(seconds=61)
        )
        assert again.results[0][1].status == "duplicate"
        assert again.results[0][1].source_event_id == recovery.source_event_id
        assert (
            deliver_once_candidate(
                engine,
                source_event_id=recovery.source_event_id,
                now=base + timedelta(seconds=61),
                port=port,
            )
            == "not_due"
        )
        assert transport.post_count == 2
        with engine.connect() as connection:
            states = connection.execute(
                select(outbox.c.source_event_id, outbox.c.state).where(
                    outbox.c.source_event_id.in_((source, recovery.source_event_id))
                )
            ).all()
        assert len(states) == 2 and all(state == "delivered" for _, state in states)
        report = {
            "phase": "pg_actual_b_c_saved_recovery_passed",
            "a_revision": os.environ["TELEMETRY_A_REVISION"],
            "a_dirty": os.environ["TELEMETRY_A_DIRTY"] == "1",
            "c_revision": C_REVISION,
            "b_revision": "8561ce1ec0ea4e996bccbdc5f46d473bb170fc09",
            "source_event_id": str(source),
            "recovery_source_event_id": str(recovery.source_event_id),
            "decisions": decisions,
            "frozen_observation_ids": [str(v) for v in saved.observation_ids],
            "actual_b_post_count": transport.post_count,
            "original_status": "resolved",
            "transport": "authenticated actual ASGI handlers, not TCP HTTP",
            "not_checked": [
                "formal C migration",
                "automatic scheduling",
                "concurrent evidence mutation",
                "DB container restart",
                "real notifications",
                "browser",
                "complete E1",
            ],
        }
        with target.open("x", encoding="utf-8") as evidence:
            json.dump(report, evidence, ensure_ascii=False, indent=2)
        logging.info(
            json.dumps(
                {
                    "phase": report["phase"],
                    "decisions": decisions,
                    "posts": transport.post_count,
                }
            )
        )


if __name__ == "__main__":
    run()
