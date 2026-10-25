"""Real PG device gate for C7da recovery page; historical rules are fixtures.

Reuses the existing deterministic C recovery scenario, replacing only its
direct single-item caller with the new page port. No A lock/stamp is mocked,
no network is sent, and fixture observations/B delivered state are not E1.
"""

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.recovery_tick_candidate import (
    stage_gated_recovery_page_candidate,
)
from tests.api.routes.test_stream_frontier import state
from tests.modules import test_telemetry_gated_high_postgres_candidate as checks


@pytest.fixture
def recovery_store():
    with contextmanager(checks.store.__wrapped__)() as store:
        yield store


def test_current_page_uses_real_pg_gate_and_missing_epoch_writes_nothing(
    recovery_store,
):
    store = recovery_store
    calls = []

    def through_page(engine, **kwargs):
        class FixtureHistoricalRules:
            def for_original(self, original_id, sampled_at):
                assert original_id == kwargs["original_source_event_id"]
                assert sampled_at == checks.NOW
                calls.append(original_id)
                return kwargs["historical_rule"]

        rules = FixtureHistoricalRules()
        before = state(store)["evidence_revision"]
        prior_calls = len(calls)
        missing = stage_gated_recovery_page_candidate(
            engine, historical_rules=rules, frontier_epochs={}, now=kwargs["now"]
        )
        # Before first staging there is no epoch, no rule lookup and no write.
        # On replay the existing candidate is returned without refreezing.
        result = missing.results[0][1]
        assert result.status in {"held_invalid_evidence", "duplicate"}
        assert len(calls) == prior_calls
        assert state(store)["evidence_revision"] == before
        with engine.connect() as connection:
            count_before = connection.scalar(select(func.count()).select_from(outbox))
        assert count_before in {1, 2}
        page = stage_gated_recovery_page_candidate(
            engine,
            historical_rules=rules,
            frontier_epochs={store.device_id: store.epoch},
            now=kwargs["now"],
            limit=1,
        )
        assert page.status == "ok" and len(page.results) == 1
        assert page.next_after is None
        assert page.results[0][0] == kwargs["original_source_event_id"]
        return page.results[0][1]

    with patch.object(
        checks, "stage_gated_recovery_candidate", side_effect=through_page
    ):
        # Original test verifies staged candidate + revision + frozen epoch,
        # then replays and asserts unchanged revision/exactly one recovery.
        checks.test_recovery_freeze_and_a_revision_commit_together_on_real_pg(store)
    # A replay may reread the original historical rule. Idempotency is the
    # unchanged evidence revision and single recovery row verified above,
    # not a promise that the read-only rule port is called only once.
    assert len(calls) == 2
