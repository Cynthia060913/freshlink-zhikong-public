"""Real PG + JWT/root HTTP adapter; no remote network or notifications."""

import os
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlmodel import Session

from app.api.deps import get_db
from app.core.security import create_access_token
from app.main import app
from app.modules.alerts.models.event import AlertEvent, AlertStatusTransition
from app.modules.iot.high_delivery_worker import HighDeliveryWorker
from app.modules.iot.stream_frontier_models import streams
from app.modules.telemetry.b_http_candidate import (
    BHttpCandidatePort,
    HttpCandidateReply,
)
from app.modules.telemetry.outbox_candidate import claim_candidate, outbox
from tests.api.routes import test_stream_frontier as checks
from tests.modules import test_gated_high_business as business

pytestmark = pytest.mark.skipif(
    os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") != "1", reason="owned PostgreSQL only"
)


@pytest.fixture
def setup():
    case = business.GatedHighBusinessTests()
    case.setUp()
    try:
        for table in (AlertEvent.__table__, AlertStatusTransition.__table__):
            table.create(case.store.engine)
            case.addCleanup(table.drop, case.store.engine)
        case.configure(kind="threshold_high")
        result = case.worker.evaluate(case.saved(), now=checks.T0)
        assert result.status == "candidate_staged"

        def database():
            with Session(case.store.engine) as session:
                yield session

        prior = app.dependency_overrides.get(get_db)
        app.dependency_overrides[get_db] = database
        try:
            with TestClient(app) as client:
                yield case, client
        finally:
            if prior is None:
                app.dependency_overrides.pop(get_db, None)
            else:
                app.dependency_overrides[get_db] = prior
    finally:
        case.doCleanups()


class RootTransport:
    def __init__(self, case, client, *, authorized=True, lost_reply=False):
        self.case, self.client = case, client
        self.lost_reply, self.posts = lost_reply, 0
        self.headers = {}
        if authorized:
            self.headers["Authorization"] = "Bearer " + create_access_token(
                case.store.actor_id, timedelta(minutes=5)
            )

    def request(self, method, path, body=None):
        with self.case.store.engine.begin() as conn:
            conn.execute(select(streams).with_for_update(nowait=True)).all()
        response = self.client.request(
            method,
            path,
            content=body,
            headers={**self.headers, "Content-Type": "application/json"},
        )
        if method == "POST":
            self.posts += 1
            if self.lost_reply and response.status_code == 201:
                raise TimeoutError("synthetic lost committed HTTP reply")
        return HttpCandidateReply(response.status_code, response.content)


def test_real_root_jwt_b_commit_readback_and_restart_dedup(setup):
    case, client = setup
    transport = RootTransport(case, client, lost_reply=True)
    port = BHttpCandidatePort(transport)
    worker = HighDeliveryWorker(
        case.store.engine, port, device_ids=[case.store.device_id]
    )
    assert worker.run_once(now=checks.T0)[0][1] == "delivered"
    assert transport.posts == 1
    original = case.rows()[0]
    assert original["state"] == "delivered"
    assert port.lookup(original["source_event_id"]).alert_status == "open"
    restarted = HighDeliveryWorker(
        case.store.engine, port, device_ids=[case.store.device_id]
    )
    assert restarted.run_once(now=checks.T0 + timedelta(seconds=60)) == []
    assert transport.posts == 1


def test_missing_auth_is_durable_retry_and_does_not_insert_b_event(setup):
    case, client = setup
    transport = RootTransport(case, client, authorized=False)
    worker = HighDeliveryWorker(
        case.store.engine,
        BHttpCandidatePort(transport),
        device_ids=[case.store.device_id],
    )
    assert worker.run_once(now=checks.T0)[0][1] == "retry"
    row = case.rows()[0]
    assert row["state"] == "pending" and row["attempt_count"] == 1
    assert row["last_error_code"] == "b_auth_unavailable" and transport.posts == 0
    with case.store.engine.connect() as conn:
        assert conn.execute(select(AlertEvent.id)).first() is None


def test_expired_claim_after_b_commit_is_reconciled_without_second_post(setup):
    case, client = setup
    transport = RootTransport(case, client)
    port = BHttpCandidatePort(transport)
    source = case.rows()[0]["source_event_id"]
    interrupted = claim_candidate(
        case.store.engine, source_event_id=source, now=checks.T0
    )
    assert port.send(interrupted.body).status == "accepted_new"
    # Simulate process loss after B commit, before C's finish checkpoint.
    assert case.rows()[0]["state"] == "leased"
    restarted = HighDeliveryWorker(
        case.store.engine, port, device_ids=[case.store.device_id]
    )
    assert (
        restarted.run_once(now=checks.T0 + timedelta(seconds=31))[0][1] == "delivered"
    )
    assert case.rows()[0]["attempt_count"] == 2
    assert transport.posts == 1


def test_actual_bounded_worker_loop_drains_the_committed_high(setup):
    case, client = setup
    transport = RootTransport(case, client)
    worker = HighDeliveryWorker(
        case.store.engine,
        BHttpCandidatePort(transport),
        device_ids=[case.store.device_id],
    )
    worker.run(max_seconds=0.02)
    assert case.rows()[0]["state"] == "delivered"
    assert transport.posts == 1


def test_runtime_claim_uses_fresh_time_after_discovery(setup):
    case, client = setup
    observed_leases = []

    class InspectLease(RootTransport):
        def request(self, method, path, body=None):
            observed_leases.append(case.rows()[0]["lease_until_utc"])
            return super().request(method, path, body)

    worker = HighDeliveryWorker(
        case.store.engine,
        BHttpCandidatePort(InspectLease(case, client)),
        device_ids=[case.store.device_id],
    )
    with patch("app.modules.iot.high_delivery_worker.datetime") as clock:
        clock.now.side_effect = [checks.T0, checks.T0 + timedelta(seconds=31)]
        assert worker.run_once()[0][1] == "delivered"
    assert observed_leases and all(
        item == checks.T0 + timedelta(seconds=61) for item in observed_leases
    )


def test_recovery_rows_and_other_device_scope_are_never_claimed(setup):
    case, client = setup
    port = BHttpCandidatePort(RootTransport(case, client))
    other = HighDeliveryWorker(case.store.engine, port, device_ids=[uuid4()])
    assert other.run_once(now=checks.T0) == []
    assert case.rows()[0]["attempt_count"] == 0
    row = case.rows()[0]
    with case.store.engine.begin() as conn:
        conn.execute(
            update(outbox).values(body={**row["body"], "kind": "temperature_recovered"})
        )
    worker = HighDeliveryWorker(
        case.store.engine, port, device_ids=[case.store.device_id]
    )
    assert worker.run_once(now=checks.T0) == []
    assert case.rows()[0]["attempt_count"] == 0


@pytest.mark.parametrize("scope", [[], [uuid4()] * 2, [uuid4() for _ in range(15)]])
def test_invalid_scope_is_refused(scope):
    with pytest.raises(ValueError):
        HighDeliveryWorker(None, None, device_ids=scope)
