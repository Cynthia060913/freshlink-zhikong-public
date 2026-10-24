"""Actual private PG scope/loop proof, not broker/HTTP/full E1."""

import os
import signal
import sys
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.modules.iot.stream_frontier import FrontierUnavailable
from app.modules.iot.stream_frontier_models import streams
from app.modules.iot.telemetry_service import verify_scope
from tests.api.routes import test_stream_frontier as checks
from tests.modules.test_telemetry_service import settings_for

pytestmark = pytest.mark.skipif(
    os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") != "1",
    reason="owned PostgreSQL required",
)


@pytest.fixture
def store():
    yield from checks.store.__wrapped__()


def test_verified_epoch_is_read_only_and_bad_epoch_is_not_reseeded(store):
    before = checks.state(store)
    verify_scope(store.engine, settings_for(store.device_id, store.epoch))
    with pytest.raises(FrontierUnavailable):
        verify_scope(store.engine, settings_for(store.device_id, uuid4()))
    assert checks.state(store) == before


def test_unverified_origin_is_refused_without_repair(store):
    with store.engine.begin() as conn:
        conn.execute(update(streams).values(origin_verified=False))
    with pytest.raises(FrontierUnavailable):
        verify_scope(store.engine, settings_for(store.device_id, store.epoch))
    assert checks.state(store)["origin_verified"] is False


def test_evaluate_cli_drains_real_b_ruled_job_and_preserves_signal_handlers(
    monkeypatch, tmp_path
):
    from app.modules.iot import telemetry_service as service
    from tests.modules import test_gated_high_business as business

    case = business.GatedHighBusinessTests()
    case.setUp()
    try:
        case.configure(kind="threshold_high")
        observation = case.saved()
        config = tmp_path / "scope.json"
        config.write_text(
            settings_for(case.store.device_id, case.store.epoch).model_dump_json(),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            service, "create_engine", lambda *_a, **_kw: case.store.engine
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "telemetry-service",
                "--role",
                "evaluate",
                "--config",
                str(config),
                "--max-seconds",
                "0.01",
            ],
        )
        previous = {
            key: signal.getsignal(key) for key in (signal.SIGINT, signal.SIGTERM)
        }
        service.main()
        assert case.pending(observation) == "candidate_staged"
        assert len(case.rows()) == 1
        assert {key: signal.getsignal(key) for key in previous} == previous
    finally:
        case.doCleanups()
