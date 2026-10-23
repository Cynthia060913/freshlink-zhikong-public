"""Real A lock + C high candidate in a disposable PostgreSQL schema only."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import func, select, update

from app.modules.iot.stream_frontier import FrontierUnavailable
from app.modules.telemetry.candidate_preview import build_candidate_preview
from app.modules.telemetry.outbox_candidate import outbox, stage_f03_candidate
from app.modules.telemetry.quality import process_batch
from app.modules.telemetry.recovery_outbox_candidate import (
    stage_gated_recovery_candidate,
)
from app.modules.telemetry.recovery_progress_candidate import (
    metadata as recovery_metadata,
)
from app.modules.telemetry.recovery_progress_candidate import recovery_progress
from app.modules.telemetry.rule_evaluation_candidate import (
    _SOURCE_NAMESPACE,
    LocalBRuleSelectionCandidate,
    select_and_stage_gated_high_candidate,
    stage_gated_high_candidate,
)
from app.modules.telemetry.storage_candidate import (
    evaluation_jobs,
    metadata,
    observations,
)
from tests.api.routes import test_telemetry_inbox as inbox_checks
from tests.api.routes.test_stream_frontier import admit, state, terminal
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


def test_actual_b_historical_rule_stages_c_high_under_real_a_gate(store):
    """PG A gate + actual B F02 service + C high write; no B delivery."""
    from sqlmodel import Session, SQLModel

    from app.modules.alerts.models import (
        Batch,
        BatchRuleSnapshot,
        BatchRuleSnapshotCreate,
        RecoverySpec,
        RuleDict,
        RuleSeverity,
        Warehouse,
    )
    from app.modules.alerts.services.rules import create_snapshot

    b_tables = [Warehouse.__table__, Batch.__table__, BatchRuleSnapshot.__table__]
    SQLModel.metadata.create_all(store.engine, tables=b_tables)
    try:
        observation_id, batch_id, _unused_fixture_body = _observation_and_body(store)
        with Session(store.engine) as session:
            session.add(
                Warehouse(id=store.warehouse_id, code="pg-c-f02", name="F02 PG")
            )
            session.add(
                Batch(
                    id=batch_id,
                    warehouse_id=store.warehouse_id,
                    code="pg-c-batch",
                    product="apple",
                    started_at=NOW - timedelta(days=2),
                )
            )
            session.commit()
            create_snapshot(
                session,
                batch_id,
                BatchRuleSnapshotCreate(
                    version_id=uuid4(),
                    rule_version="pg-f02-v1",
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
        before = state(store)["evidence_revision"]
        selector = LocalBRuleSelectionCandidate(store.engine)
        result = select_and_stage_gated_high_candidate(
            store.engine,
            device_id=store.device_id,
            warehouse_id=store.warehouse_id,
            stream_epoch=store.epoch,
            observation_id=observation_id,
            batch_id=batch_id,
            selector=selector,
            now=NOW,
        )
        assert result.status == "candidate_staged"
        assert state(store)["evidence_revision"] == before + 1
        with store.engine.connect() as connection:
            row = connection.execute(select(outbox)).mappings().one()
            assert row["body"]["kind"] == "temperature_high"
            assert row["body"]["rule_version"] == "pg-f02-v1"
            assert row["body"]["batch_id"] == str(batch_id)
        replay = select_and_stage_gated_high_candidate(
            store.engine,
            device_id=store.device_id,
            warehouse_id=store.warehouse_id,
            stream_epoch=store.epoch,
            observation_id=observation_id,
            batch_id=batch_id,
            selector=selector,
            now=NOW,
        )
        assert replay.status == "not_due"
        assert state(store)["evidence_revision"] == before + 1
    finally:
        SQLModel.metadata.drop_all(store.engine, tables=b_tables)


def test_recovery_freeze_and_a_revision_commit_together_on_real_pg(store):
    """Real A gate/C transaction; B delivered state and historical rule are fixtures."""
    recovery_metadata.create_all(store.engine)
    try:
        receipt = inbox_checks.frame(store)
        assert admit(store, receipt).status == "queued"
        terminal(store, receipt)
        original_id, batch_id, snapshot_id = uuid4(), uuid4(), uuid4()
        anomaly_id = uuid4()
        recovered_ids = [uuid4() for _ in range(3)]

        def sampled(moment: datetime) -> str:
            return moment.isoformat().replace("+00:00", "Z")

        original_body = {
            "schema_version": "1.0",
            "source_event_id": str(original_id),
            "kind": "temperature_high",
            "warehouse_id": str(store.warehouse_id),
            "batch_id": str(batch_id),
            "observation_ids": [str(anomaly_id)],
            "metric_code": "temperature",
            "sampled_at": sampled(NOW),
            "value": 11.0,
            "unit": "°C",
            "severity": "warning",
            "rule_snapshot_id": str(snapshot_id),
            "rule_version": "pg-recovery-v1",
            "algorithm_version": "threshold-rule/1",
            "evidence": {"comparison": ">", "threshold": 8.0},
        }
        historical_rule = {
            "kind": "threshold_high",
            "warehouse_id": str(store.warehouse_id),
            "batch_id": str(batch_id),
            "metric_code": "temperature",
            "snapshot_id": str(snapshot_id),
            "rule_version": "pg-recovery-v1",
            "severity": "warning",
            "unit": "°C",
            "comparison": ">",
            "threshold": 8.0,
            "effective_start_at": sampled(NOW - timedelta(days=1)),
            "effective_end_at": None,
            "recovery": {"comparison": "<=", "threshold": 7.0, "min_samples": 3},
        }
        with store.engine.begin() as connection:
            for observation_id, moment, temperature in [
                (anomaly_id, NOW, 11.0),
                *[
                    (observation_id, NOW + timedelta(seconds=second), 6.0)
                    for observation_id, second in zip(
                        recovered_ids, (10, 50, 130), strict=True
                    )
                ],
            ]:
                connection.execute(
                    observations.insert().values(
                        id=observation_id,
                        device_id=store.device_id,
                        message_id=uuid4(),
                        warehouse_id=store.warehouse_id,
                        content_sha256="0" * 64,
                        raw_text="{}",
                        raw_payload={},
                        sampled_at_utc=moment,
                        first_received_at_utc=moment,
                        normalized={
                            "sampled_at": sampled(moment),
                            "metrics": {"temperature": temperature},
                        },
                        quality=["ok"],
                        risk={"state": "unknown"},
                    )
                )
            connection.execute(
                recovery_progress.insert().values(
                    original_source_event_id=original_id,
                    device_id=store.device_id,
                    warehouse_id=store.warehouse_id,
                    batch_id=batch_id,
                    anomaly_sampled_at_utc=sampled(NOW),
                    comparison="<=",
                    threshold_c=7.0,
                    min_samples=3,
                    max_gap_seconds=90,
                    last_sampled_at_utc=sampled(NOW + timedelta(seconds=130)),
                    last_received_at_utc=sampled(NOW + timedelta(seconds=130)),
                    last_received_observation_id=recovered_ids[-1],
                    last_matching_at_utc=sampled(NOW + timedelta(seconds=130)),
                    observation_ids=[str(value) for value in recovered_ids],
                    eligible=True,
                    version=3,
                )
            )
        assert (
            stage_f03_candidate(
                store.engine,
                source_event_id=original_id,
                observation_id=anomaly_id,
                body=original_body,
                now=NOW,
            )
            == "staged"
        )
        with store.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == original_id)
                .values(state="delivered")
            )
        before = state(store)["evidence_revision"]
        kwargs = {
            "device_id": store.device_id,
            "warehouse_id": store.warehouse_id,
            "stream_epoch": store.epoch,
            "original_source_event_id": original_id,
            "historical_rule": historical_rule,
            "now": NOW + timedelta(minutes=3),
        }
        staged = stage_gated_recovery_candidate(store.engine, **kwargs)
        assert staged.status == "staged" and staged.source_event_id is not None
        assert state(store)["evidence_revision"] == before + 1
        with store.engine.connect() as connection:
            recovery = (
                connection.execute(
                    select(outbox).where(
                        outbox.c.source_event_id == staged.source_event_id
                    )
                )
                .mappings()
                .one()
            )
            assert recovery["frozen_stream_epoch"] == store.epoch
            assert recovery["frozen_evidence_revision"] == before + 1
        assert (
            stage_gated_recovery_candidate(store.engine, **kwargs).status == "duplicate"
        )
        assert state(store)["evidence_revision"] == before + 1
    finally:
        recovery_metadata.drop_all(store.engine)
