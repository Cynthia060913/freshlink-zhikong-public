"""Real A lock + C high candidate in a disposable PostgreSQL schema only."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import delete, func, select, update

from app.modules.iot.ingress import DeviceContext, receive_telemetry
from app.modules.iot.stream_frontier import (
    FrontierUnavailable,
    begin_evidence_write,
    record_terminal,
)
from app.modules.iot.stream_frontier_models import entries
from app.modules.telemetry.candidate_preview import build_candidate_preview
from app.modules.telemetry.f03_body_candidate import f03_digest_candidate
from app.modules.telemetry.multi_batch_candidate import (
    stage_gated_multi_batch_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox, stage_f03_candidate
from app.modules.telemetry.quality import process_batch
from app.modules.telemetry.recovery_freshness_candidate import (
    RecoveryFreshnessCandidate,
    check_recovery_freshness_candidate,
)
from app.modules.telemetry.recovery_outbox_candidate import (
    stage_gated_recovery_candidate,
)
from app.modules.telemetry.recovery_progress_candidate import (
    metadata as recovery_metadata,
)
from app.modules.telemetry.recovery_progress_candidate import recovery_progress
from app.modules.telemetry.recovery_refreeze_candidate import (
    RecoveryRefreezeCandidate,
    refresh_pending_recovery_frontier_on_connection_candidate,
)
from app.modules.telemetry.recovery_tick_candidate import (
    LocalBHistoricalRecoveryRuleCandidate,
)
from app.modules.telemetry.rule_evaluation_candidate import (
    _SOURCE_NAMESPACE,
    LocalBRuleSelectionCandidate,
    select_and_stage_gated_high_candidate,
    stage_gated_high_candidate,
)
from app.modules.telemetry.storage_candidate import (
    evaluation_jobs,
    ingest_observation_on_connection_candidate,
    metadata,
    observations,
    receptions,
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


def test_two_batch_one_high_one_normal_share_one_real_pg_gate(store):
    observation_id, first, _body = _observation_and_body(store)
    second = uuid4()

    def selected(batch_id, threshold):
        return {
            "batch_id": str(batch_id),
            "warehouse_id": str(store.warehouse_id),
            "snapshot_id": str(uuid4()),
            "rule_version": f"pg-{threshold}-v1",
            "metric_code": "temperature",
            "kind": "threshold_high",
            "comparison": ">",
            "severity": "warning",
            "threshold": threshold,
            "unit": "°C",
            "effective_start_at": "2026-10-17T00:00:00Z",
            "effective_end_at": None,
        }

    before = state(store)["evidence_revision"]
    kwargs = {
        "device_id": store.device_id,
        "warehouse_id": store.warehouse_id,
        "stream_epoch": store.epoch,
        "observation_id": observation_id,
        "batch_ids": (first, second),
        "selected_rules": [selected(first, 8), selected(second, 20)],
        "now": NOW,
    }
    result = stage_gated_multi_batch_candidate(store.engine, **kwargs)
    assert result.status == "candidate_staged"
    assert len(result.source_event_ids) == 1
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(outbox)) == 1
        risk = connection.scalar(
            select(observations.c.risk).where(observations.c.id == observation_id)
        )
        assert {item["batch_id"]: item["state"] for item in risk["batches"]} == {
            str(first): "candidate",
            str(second): "normal",
        }
    assert state(store)["evidence_revision"] == before + 1
    assert stage_gated_multi_batch_candidate(store.engine, **kwargs).status == "not_due"
    assert state(store)["evidence_revision"] == before + 1


def test_two_batch_second_write_failure_rolls_back_real_pg_frontier(store):
    from sqlalchemy.exc import OperationalError

    from app.modules.telemetry import multi_batch_candidate as module

    observation_id, first, _body = _observation_and_body(store)
    second = uuid4()
    rules = [
        {
            "batch_id": str(batch),
            "warehouse_id": str(store.warehouse_id),
            "snapshot_id": str(uuid4()),
            "rule_version": "pg-both-high-v1",
            "metric_code": "temperature",
            "kind": "threshold_high",
            "comparison": ">",
            "severity": "warning",
            "threshold": threshold,
            "unit": "°C",
            "effective_start_at": "2026-10-17T00:00:00Z",
            "effective_end_at": None,
        }
        for batch, threshold in ((first, 8), (second, 9))
    ]
    kwargs = {
        "device_id": store.device_id,
        "warehouse_id": store.warehouse_id,
        "stream_epoch": store.epoch,
        "observation_id": observation_id,
        "batch_ids": (first, second),
        "selected_rules": rules,
        "now": NOW,
    }
    before = state(store)["evidence_revision"]
    actual = module.stage_f03_on_connection_candidate
    calls = 0

    def fail_second(*args, **options):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OperationalError("synthetic", {}, RuntimeError("private"))
        return actual(*args, **options)

    with patch.object(
        module, "stage_f03_on_connection_candidate", side_effect=fail_second
    ):
        with pytest.raises(OperationalError):
            stage_gated_multi_batch_candidate(store.engine, **kwargs)
    assert state(store)["evidence_revision"] == before
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(outbox)) == 0
        assert (
            connection.scalar(
                select(evaluation_jobs.c.state).where(
                    evaluation_jobs.c.observation_id == observation_id
                )
            )
            == "pending"
        )
    assert (
        stage_gated_multi_batch_candidate(store.engine, **kwargs).status
        == "candidate_staged"
    )
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(outbox)) == 2


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
            # SQLModel has no ORM relationship between these rows; flush the
            # parent explicitly so PostgreSQL sees it before the child FK.
            session.flush()
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
    """Actual B historical F02 + A gate/C recovery transaction in real PG."""
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
    recovery_metadata.create_all(store.engine)
    try:
        receipt = inbox_checks.frame(store)
        assert admit(store, receipt).status == "queued"
        terminal(store, receipt)
        original_id, batch_id = uuid4(), uuid4()
        with Session(store.engine) as session:
            session.add(
                Warehouse(id=store.warehouse_id, code="pg-c-recovery", name="Recovery")
            )
            session.flush()
            session.add(
                Batch(
                    id=batch_id,
                    warehouse_id=store.warehouse_id,
                    code="pg-c-recovery-batch",
                    product="apple",
                    started_at=NOW - timedelta(days=2),
                )
            )
            session.commit()
            snapshot = create_snapshot(
                session,
                batch_id,
                BatchRuleSnapshotCreate(
                    version_id=uuid4(),
                    rule_version="pg-recovery-v1",
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
            snapshot_id = snapshot.id
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
                message_id = uuid4()
                raw = {
                    "schema_version": "1.0",
                    "device_id": str(store.device_id),
                    "message_id": str(message_id),
                    "sampled_at": sampled(moment),
                    "metrics": [
                        {"key": "temperature", "value": temperature, "unit": "°C"},
                        {"key": "humidity", "value": 75, "unit": "%RH"},
                    ],
                }
                processed = process_batch(
                    [
                        {
                            **raw,
                            "warehouse_id": str(store.warehouse_id),
                            "received_at": sampled(moment),
                        }
                    ]
                )[0]
                connection.execute(
                    observations.insert().values(
                        id=observation_id,
                        device_id=store.device_id,
                        message_id=message_id,
                        warehouse_id=store.warehouse_id,
                        content_sha256="0" * 64,
                        raw_text=json.dumps(raw, ensure_ascii=False),
                        raw_payload=raw,
                        sampled_at_utc=moment,
                        first_received_at_utc=moment,
                        normalized=processed["normalized"],
                        quality=processed["quality"],
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
        # Delivery is a controlled fixture; this test does not call B F03 HTTP.
        with store.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == original_id)
                .values(state="delivered")
            )
        historical_rule = LocalBHistoricalRecoveryRuleCandidate(
            store.engine
        ).for_original(original_id, NOW)
        assert historical_rule is not None
        assert historical_rule["snapshot_id"] == str(snapshot_id)
        assert historical_rule["kind"] == "threshold_high"
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
        ready_args = {
            "recovery_source_event_id": staged.source_event_id,
            "device_id": store.device_id,
            "warehouse_id": store.warehouse_id,
            "stream_epoch": store.epoch,
        }
        ready = check_recovery_freshness_candidate(store.engine, **ready_args)
        assert ready.status == "ready_at_check"
        # A stored source can become inconsistent with its preserved raw
        # sample without changing the immutable F03 body or frozen frontier.
        # Both the high observation and the recovery window must fail closed.
        for observation_id in (anomaly_id, recovered_ids[1]):
            with store.engine.begin() as connection:
                saved_raw = connection.scalar(
                    select(observations.c.raw_payload).where(
                        observations.c.id == observation_id
                    )
                )
                altered_raw = {
                    **saved_raw,
                    "metrics": [
                        {**saved_raw["metrics"][0], "value": 99},
                        *saved_raw["metrics"][1:],
                    ],
                }
                connection.execute(
                    update(observations)
                    .where(observations.c.id == observation_id)
                    .values(raw_payload=altered_raw)
                )
            assert check_recovery_freshness_candidate(store.engine, **ready_args) == (
                RecoveryFreshnessCandidate("held", "invalid_stored_evidence")
            )
            with store.engine.begin() as connection:
                connection.execute(
                    update(observations)
                    .where(observations.c.id == observation_id)
                    .values(raw_payload=saved_raw)
                )
        assert check_recovery_freshness_candidate(store.engine, **ready_args) == (
            RecoveryFreshnessCandidate("ready_at_check")
        )
        # A well-formed but altered body must not pass the pre-send gate merely
        # because its IDs and temperature still match the frozen evidence.
        for source_event_id in (staged.source_event_id, original_id):
            with store.engine.begin() as connection:
                saved_body = connection.execute(
                    select(outbox.c.body).where(
                        outbox.c.source_event_id == source_event_id
                    )
                ).scalar_one()
                altered_body = {
                    **saved_body,
                    "severity": (
                        "warning" if saved_body["severity"] != "warning" else "info"
                    ),
                }
                connection.execute(
                    update(outbox)
                    .where(outbox.c.source_event_id == source_event_id)
                    .values(body=altered_body)
                )
            assert check_recovery_freshness_candidate(store.engine, **ready_args) == (
                RecoveryFreshnessCandidate("held", "invalid_stored_evidence")
            )
            with store.engine.begin() as connection:
                connection.execute(
                    update(outbox)
                    .where(outbox.c.source_event_id == source_event_id)
                    .values(body=saved_body)
                )
        assert (
            check_recovery_freshness_candidate(store.engine, **ready_args).status
            == "ready_at_check"
        )
        # A matching digest alone does not make a different original kind a
        # valid high-temperature anomaly for this recovery window.
        with store.engine.begin() as connection:
            original_row = (
                connection.execute(
                    select(outbox).where(outbox.c.source_event_id == original_id)
                )
                .mappings()
                .one()
            )
            changed_original = {**original_row["body"], "kind": "temperature_low"}
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == original_id)
                .values(
                    body=changed_original,
                    body_sha256=f03_digest_candidate(changed_original).removeprefix(
                        "sha256:"
                    ),
                )
            )
        assert check_recovery_freshness_candidate(store.engine, **ready_args) == (
            RecoveryFreshnessCandidate("held", "invalid_original_link")
        )
        with store.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == original_id)
                .values(
                    body=original_row["body"],
                    body_sha256=original_row["body_sha256"],
                )
            )
        assert (
            check_recovery_freshness_candidate(store.engine, **ready_args).status
            == "ready_at_check"
        )
        # A late invalid receipt has no new observation, but must still hold
        # recovery. This direct insert is test evidence, not a trusted intake.
        bad_receipt = uuid4()
        with store.engine.begin() as connection:
            connection.execute(
                receptions.insert().values(
                    id=bad_receipt,
                    observation_id=None,
                    device_id=store.device_id,
                    warehouse_id=store.warehouse_id,
                    received_at_utc=sampled(NOW + timedelta(minutes=4)),
                    raw_text="synthetic-invalid",
                    receipt_sha256="a" * 64,
                    outcome="invalid",
                    reason="invalid_unit",
                )
            )
        assert check_recovery_freshness_candidate(store.engine, **ready_args) == (
            RecoveryFreshnessCandidate("held", "unresolved_bad_reception")
        )
        with store.engine.begin() as connection:
            connection.execute(delete(receptions).where(receptions.c.id == bad_receipt))
            begin_evidence_write(
                connection,
                device_id=store.device_id,
                warehouse_id=store.warehouse_id,
                epoch=store.epoch,
            )
        assert check_recovery_freshness_candidate(store.engine, **ready_args) == (
            RecoveryFreshnessCandidate("held", "stale_frozen_frontier")
        )
        for observation_id in (anomaly_id, recovered_ids[1]):
            with store.engine.begin() as connection:
                original_text = connection.scalar(
                    select(observations.c.raw_text).where(
                        observations.c.id == observation_id
                    )
                )
                connection.execute(
                    update(observations)
                    .where(observations.c.id == observation_id)
                    .values(raw_text="invalid synthetic JSON")
                )
            with store.engine.begin() as connection:
                assert refresh_pending_recovery_frontier_on_connection_candidate(
                    connection, **ready_args
                ) == RecoveryRefreezeCandidate("held", "invalid_stored_evidence")
            with store.engine.begin() as connection:
                connection.execute(
                    update(observations)
                    .where(observations.c.id == observation_id)
                    .values(raw_text=original_text)
                )
        with store.engine.begin() as connection:
            assert refresh_pending_recovery_frontier_on_connection_candidate(
                connection, **ready_args
            ) == RecoveryRefreezeCandidate("refrozen")
        assert check_recovery_freshness_candidate(store.engine, **ready_args) == (
            RecoveryFreshnessCandidate("ready_at_check")
        )

        def admit_saved_sample(
            temperature: float, seconds: int, *, check_pending: bool = False
        ) -> None:
            moment = NOW + timedelta(seconds=seconds)
            raw = {
                "schema_version": "1.0",
                "device_id": str(store.device_id),
                "message_id": str(uuid4()),
                "sampled_at": sampled(moment),
                "metrics": [
                    {"key": "temperature", "value": temperature, "unit": "°C"},
                    {"key": "humidity", "value": 75, "unit": "%RH"},
                ],
            }
            envelope = receive_telemetry(
                topic=f"freshlink/v1/devices/{store.device_id}/telemetry",
                payload=json.dumps(raw, ensure_ascii=False).encode(),
                context=DeviceContext(store.device_id, store.warehouse_id),
                received_at=moment,
            )
            assert admit(store, envelope).status == "queued"
            if check_pending:
                with store.engine.begin() as connection:
                    assert refresh_pending_recovery_frontier_on_connection_candidate(
                        connection, **ready_args
                    ) == RecoveryRefreezeCandidate("held", "frontier_incomplete")
            with store.engine.begin() as connection:
                begin_evidence_write(
                    connection,
                    device_id=store.device_id,
                    warehouse_id=store.warehouse_id,
                    epoch=store.epoch,
                )
                result = ingest_observation_on_connection_candidate(
                    connection, envelope
                )
                assert result.status == "accepted_new"
                entry = (
                    connection.execute(
                        select(entries).where(
                            entries.c.reception_id == envelope.reception_id
                        )
                    )
                    .mappings()
                    .one()
                )
                record_terminal(
                    connection,
                    reception_id=envelope.reception_id,
                    fingerprint=entry["fingerprint"],
                    epoch=store.epoch,
                    outcome="accepted_new",
                    observation_id=result.observation_id,
                )

        with store.engine.connect() as connection:
            frozen_before = (
                connection.execute(
                    select(outbox).where(
                        outbox.c.source_event_id == staged.source_event_id
                    )
                )
                .mappings()
                .one()
            )
        admit_saved_sample(6.0, 140, check_pending=True)
        assert check_recovery_freshness_candidate(store.engine, **ready_args) == (
            RecoveryFreshnessCandidate("held", "stale_frozen_frontier")
        )
        with store.engine.begin() as connection:
            assert refresh_pending_recovery_frontier_on_connection_candidate(
                connection, **ready_args
            ) == RecoveryRefreezeCandidate("refrozen")
        assert check_recovery_freshness_candidate(store.engine, **ready_args) == (
            RecoveryFreshnessCandidate("ready_at_check")
        )
        with store.engine.connect() as connection:
            frozen_after = (
                connection.execute(
                    select(outbox).where(
                        outbox.c.source_event_id == staged.source_event_id
                    )
                )
                .mappings()
                .one()
            )
        for name in ("body", "body_sha256", "source_event_id", "attempt_count"):
            assert frozen_after[name] == frozen_before[name]
        assert (
            frozen_after["frozen_admitted_seq"] > frozen_before["frozen_admitted_seq"]
        )
        with store.engine.begin() as connection:
            assert refresh_pending_recovery_frontier_on_connection_candidate(
                connection, **{**ready_args, "stream_epoch": uuid4()}
            ) == RecoveryRefreezeCandidate("held", "frontier_unavailable")
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == staged.source_event_id)
                .values(state="leased", lease_token=uuid4())
            )
        with store.engine.begin() as connection:
            assert refresh_pending_recovery_frontier_on_connection_candidate(
                connection, **ready_args
            ) == RecoveryRefreezeCandidate("held", "recovery_not_unclaimed")
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == staged.source_event_id)
                .values(state="pending", lease_token=None, attempt_count=1)
            )
        with store.engine.begin() as connection:
            assert refresh_pending_recovery_frontier_on_connection_candidate(
                connection, **ready_args
            ) == RecoveryRefreezeCandidate("held", "recovery_not_unclaimed")
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == staged.source_event_id)
                .values(attempt_count=0)
            )
        admit_saved_sample(11.0, 150)
        with store.engine.begin() as connection:
            assert refresh_pending_recovery_frontier_on_connection_candidate(
                connection, **ready_args
            ) == RecoveryRefreezeCandidate("held", "new_evidence_not_recovered")
        assert check_recovery_freshness_candidate(store.engine, **ready_args) == (
            RecoveryFreshnessCandidate("held", "stale_frozen_frontier")
        )
    finally:
        recovery_metadata.drop_all(store.engine)
        SQLModel.metadata.drop_all(store.engine, tables=b_tables)
