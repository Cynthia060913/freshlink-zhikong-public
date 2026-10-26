"""Isolated lease/CAS checks; real A stream and saved evidence run in PG tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select

from app.modules.iot.stream_frontier import FrontierStamp
from app.modules.telemetry.outbox_candidate import finish_candidate, outbox
from app.modules.telemetry.recovery_claim_candidate import (
    claim_fresh_recovery_on_connection_candidate,
)
from app.modules.telemetry.recovery_freshness_candidate import (
    RecoveryFreshnessCandidate,
)
from app.modules.telemetry.storage_candidate import metadata

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
EPOCH = UUID("33333333-3333-4333-8333-333333333333")
SOURCE = UUID("44444444-4444-4444-8444-444444444444")


class RecoveryClaimCandidateCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(
            f"sqlite+pysqlite:///{Path(directory.name) / 'c.db'}"
        )
        self.addCleanup(self.engine.dispose)
        metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.execute(
                outbox.insert().values(
                    source_event_id=SOURCE,
                    related_source_event_id=uuid4(),
                    frozen_device_id=DEVICE,
                    frozen_warehouse_id=WAREHOUSE,
                    frozen_stream_epoch=EPOCH,
                    frozen_admitted_seq=1,
                    frozen_settled_through=1,
                    frozen_evidence_revision=1,
                    observation_id=uuid4(),
                    body_sha256="a" * 64,
                    body={"kind": "temperature_recovered"},
                    state="pending",
                    attempt_count=0,
                    next_attempt_at_utc=NOW,
                )
            )

    def claim(
        self,
        *,
        now: datetime = NOW,
        stamp: FrontierStamp | None = None,
        verification: RecoveryFreshnessCandidate = RecoveryFreshnessCandidate(
            "ready_at_check"
        ),
    ):
        current = stamp or FrontierStamp(DEVICE, WAREHOUSE, EPOCH, 1, 1, 1)
        with (
            patch(
                "app.modules.telemetry.recovery_claim_candidate.read_complete_frontier",
                return_value=current,
            ),
            patch(
                "app.modules.telemetry.recovery_claim_candidate._verify_saved_recovery_on_connection",
                return_value=verification,
            ),
            self.engine.begin() as connection,
        ):
            result = claim_fresh_recovery_on_connection_candidate(
                connection,
                recovery_source_event_id=SOURCE,
                device_id=DEVICE,
                warehouse_id=WAREHOUSE,
                stream_epoch=EPOCH,
                now=now,
            )
        return result

    def test_due_lease_retry_and_old_token_fence(self) -> None:
        first = self.claim()
        self.assertEqual(first.status, "claimed")
        assert first.claim is not None
        self.assertEqual(first.claim.attempt_count, 1)
        self.assertEqual(self.claim().status, "held")
        self.assertTrue(
            finish_candidate(
                self.engine,
                claim=first.claim,
                disposition="retry",
                now=NOW,
                error_code="b_timeout_unresolved",
                retry_after_seconds=60,
            )
        )
        self.assertEqual(self.claim(now=NOW + timedelta(seconds=59)).status, "held")
        second = self.claim(now=NOW + timedelta(seconds=60))
        self.assertEqual(second.status, "claimed")
        assert second.claim is not None
        self.assertEqual(second.claim.attempt_count, 2)
        self.assertNotEqual(first.claim.lease_token, second.claim.lease_token)
        self.assertFalse(
            finish_candidate(
                self.engine,
                claim=first.claim,
                disposition="delivered",
                now=NOW + timedelta(seconds=60),
            )
        )

    def test_bad_evidence_and_changed_revision_never_consume_attempt(self) -> None:
        self.assertEqual(
            self.claim(
                verification=RecoveryFreshnessCandidate(
                    "held", "invalid_stored_evidence"
                )
            ).reason,
            "invalid_stored_evidence",
        )
        changed = FrontierStamp(DEVICE, WAREHOUSE, EPOCH, 1, 1, 2)
        self.assertEqual(
            self.claim(stamp=changed).status,
            "held",
        )
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(outbox).where(outbox.c.source_event_id == SOURCE)
                )
                .mappings()
                .one()
            )
        self.assertEqual((row["state"], row["attempt_count"]), ("pending", 0))


if __name__ == "__main__":
    unittest.main()
