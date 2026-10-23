"""Real A lock + C high candidate in a disposable PostgreSQL schema only."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import func, select

from app.modules.iot.stream_frontier import FrontierUnavailable
from app.modules.telemetry.candidate_preview import build_candidate_preview
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.quality import process_batch
from app.modules.telemetry.rule_evaluation_candidate import (
    _SOURCE_NAMESPACE,
    stage_gated_high_candidate,
)
from app.modules.telemetry.storage_candidate import (
    evaluation_jobs,
    metadata,
    observations,
)
from tests.api.routes.test_stream_frontier import state
from tests.api.routes.test_stream_frontier import store as frontier_store

NOW = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)


@pytest.fixture
def store():
    # A's fixture refuses anything except its owned disposable PG/hosted CI.
    with contextmanager(frontier_store.__wrapped__)() as private:
        metadata.create_all(private.engine)
        try:
            yield private
        finally:
            metadata.drop_all(private.engine)


def _observation_and_body(store):
    observation_id, batch_id = uuid4(), uuid4()
    raw = {
        "schema_version": "1.0",
        "device_id": str(store.device_id),
        "message_id": str(uuid4()),
        "sampled_at": "2026-10-18T00:00:00Z",
        "metrics": [
            {"key": "temperature", "value": 13, "unit": "°C"},
            {"key": "humidity", "value": 75, "unit": "%RH"},
        ],
    }
    view = {
        **raw,
        "warehouse_id": str(store.warehouse_id),
        "received_at": "2026-10-18T00:00:00Z",
    }
    processed = process_batch([view], 8.0)[0]
    source_id = uuid5(
        _SOURCE_NAMESPACE, f"{observation_id}:{batch_id}:temperature_high"
    )
    body = build_candidate_preview(
        processed,
        observation_id=str(observation_id),
        source_event_id=str(source_id),
        rule={
            "warehouse_id": str(store.warehouse_id),
            "batch_id": str(batch_id),
            "rule_snapshot_id": str(uuid4()),
            "rule_version": "pg-fixture-v1",
            "metric_code": "temperature",
            "unit": "°C",
            "comparison": ">",
            "severity": "warning",
            "threshold": 8.0,
            "effective_from": "2026-10-17T00:00:00Z",
            "effective_to": None,
        },
    )
    assert body is not None
    with store.engine.begin() as connection:
        connection.execute(
            observations.insert().values(
                id=observation_id,
                device_id=store.device_id,
                message_id=UUID(raw["message_id"]),
                warehouse_id=store.warehouse_id,
                content_sha256="0" * 64,
                raw_text=json.dumps(raw, ensure_ascii=False),
                raw_payload=raw,
                sampled_at_utc=NOW,
                first_received_at_utc=NOW,
                normalized=processed["normalized"],
                quality=processed["quality"],
                risk={"state": "unknown"},
            )
        )
        connection.execute(
            evaluation_jobs.insert().values(
                observation_id=observation_id, state="pending"
            )
        )
    return observation_id, batch_id, body


def test_high_candidate_and_a_revision_commit_together_and_replay_is_noop(store):
    """Real PG lock/rows; B historical rule and association are fixtures."""
    observation_id, batch_id, body = _observation_and_body(store)
    before = state(store)["evidence_revision"]
    kwargs = {
        "device_id": store.device_id,
        "warehouse_id": store.warehouse_id,
        "stream_epoch": store.epoch,
        "observation_id": observation_id,
        "batch_id": batch_id,
        "body": body,
        "now": NOW,
    }
    result = stage_gated_high_candidate(store.engine, **kwargs)
    assert result.status == "candidate_staged"
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(outbox)) == 1
        assert (
            connection.scalar(
                select(evaluation_jobs.c.state).where(
                    evaluation_jobs.c.observation_id == observation_id
                )
            )
            == "candidate_staged"
        )
        assert (
            connection.scalar(
                select(observations.c.risk).where(observations.c.id == observation_id)
            )["state"]
            == "candidate"
        )
    assert state(store)["evidence_revision"] == before + 1
    assert stage_gated_high_candidate(store.engine, **kwargs).status == "not_due"
    assert state(store)["evidence_revision"] == before + 1
    with pytest.raises(FrontierUnavailable):
        stage_gated_high_candidate(store.engine, **{**kwargs, "stream_epoch": uuid4()})
    assert state(store)["evidence_revision"] == before + 1
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(outbox)) == 1
