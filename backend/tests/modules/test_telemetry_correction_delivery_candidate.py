"""Correction-specific lease and uncertain-receipt regression on isolated C DB."""

import json
import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import create_engine, select, update

from app.modules.telemetry.b_http_candidate import (
    BHttpLinkedCorrectionPort,
    HttpCandidateReply,
)
from app.modules.telemetry.correction_delivery_candidate import (
    claim_correction_wire_candidate,
    deliver_correction_once_candidate,
    finish_correction_wire_candidate,
)
from app.modules.telemetry.correction_wire_candidate import (
    project_held_correction_wire_candidate,
    validate_correction_wire_body_candidate,
)
from app.modules.telemetry.delivery_candidate import (
    InvalidLocalCandidate,
    RemoteCandidateReceipt,
)
from app.modules.telemetry.f03_body_candidate import f03_digest_candidate
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.quality import process_batch
from app.modules.telemetry.storage_candidate import metadata, observations
from tests.modules.test_telemetry_correction_wire_candidate import (
    CORRECTION_ID,
    DEVICE_ID,
    MESSAGE_ID,
    OBSERVATION_ID,
    ORIGINAL_ID,
    WAREHOUSE_ID,
    _digest,
    _internal,
    _original,
    _raw_observation,
)

NOW = datetime(2026, 10, 25, 8, 3, tzinfo=UTC)


class _BPort:
    def __init__(self, digest: str, *, lost_reply: bool = False) -> None:
        self.digest = digest.removeprefix("sha256:")
        self.lost_reply = lost_reply
        self.lookup_count = 0
        self.send_count = 0

    def lookup(self, source_event_id: UUID) -> RemoteCandidateReceipt | None:
        self.lookup_count += 1
        if self.lookup_count == 1:
            return None
        return RemoteCandidateReceipt(
            source_event_id, "accepted_duplicate", body_sha256=self.digest
        )

    def send(self, body: dict) -> RemoteCandidateReceipt:
        self.send_count += 1
        if self.lost_reply:
            raise TimeoutError("synthetic lost response after B accepted")
        return RemoteCandidateReceipt(
            UUID(body["source_event_id"]), "accepted_new", body_sha256=self.digest
        )


class CorrectionDeliveryCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        metadata.create_all(self.engine)
        raw = _raw_observation()
        quality = process_batch([{**raw, "received_at": NOW.isoformat()}])[0]
        original, internal = _original(), _internal()
        with self.engine.begin() as connection:
            connection.execute(
                observations.insert().values(
                    id=UUID(OBSERVATION_ID),
                    device_id=UUID(DEVICE_ID),
                    message_id=UUID(MESSAGE_ID),
                    warehouse_id=UUID(WAREHOUSE_ID),
                    content_sha256="b" * 64,
                    raw_text=json.dumps(raw),
                    raw_payload=raw,
                    sampled_at_utc=datetime(2026, 10, 25, 8, tzinfo=UTC),
                    first_received_at_utc=NOW,
                    normalized=quality["normalized"],
                    quality=quality["quality"],
                    risk=quality["risk"],
                )
            )
            connection.execute(
                outbox.insert().values(
                    source_event_id=UUID(ORIGINAL_ID),
                    related_source_event_id=None,
                    observation_id=UUID(OBSERVATION_ID),
                    body_sha256=f03_digest_candidate(original).removeprefix("sha256:"),
                    body=original,
                    state="delivered",
                    attempt_count=1,
                    next_attempt_at_utc=NOW,
                )
            )
            connection.execute(
                outbox.insert().values(
                    source_event_id=UUID(CORRECTION_ID),
                    related_source_event_id=UUID(ORIGINAL_ID),
                    observation_id=UUID(OBSERVATION_ID),
                    body_sha256=_digest(internal),
                    body=internal,
                    state="held_contract",
                    attempt_count=0,
                    next_attempt_at_utc=NOW,
                )
            )
        self.wire = project_held_correction_wire_candidate(
            internal_body=internal,
            internal_body_sha256=_digest(internal),
            original_body=original,
            original_body_sha256=f03_digest_candidate(original).removeprefix("sha256:"),
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def row(self) -> dict:
        with self.engine.connect() as connection:
            return dict(
                connection.execute(
                    select(outbox).where(
                        outbox.c.source_event_id == UUID(CORRECTION_ID)
                    )
                )
                .mappings()
                .one()
            )

    def test_claim_retry_expired_lease_fences_old_worker_and_preserves_internal(self):
        self.assertIsNone(
            claim_correction_wire_candidate(
                self.engine, source_event_id=UUID(CORRECTION_ID), now=NOW
            )
        )
        blocked_port = _BPort(self.wire.content_digest)
        self.assertEqual(
            deliver_correction_once_candidate(
                self.engine,
                source_event_id=UUID(CORRECTION_ID),
                now=NOW,
                port=blocked_port,
            ),
            "not_due",
        )
        self.assertEqual((blocked_port.lookup_count, blocked_port.send_count), (0, 0))
        self.assertEqual(
            (self.row()["state"], self.row()["attempt_count"]), ("held_contract", 0)
        )
        first = claim_correction_wire_candidate(
            self.engine,
            source_event_id=UUID(CORRECTION_ID),
            now=NOW,
            contract_confirmed=True,
        )
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.wire_body, self.wire.body)
        self.assertEqual(first.wire_digest, self.wire.content_digest)
        self.assertEqual(
            (self.row()["state"], self.row()["attempt_count"]), ("leased", 1)
        )
        self.assertIsNone(
            claim_correction_wire_candidate(
                self.engine,
                source_event_id=UUID(CORRECTION_ID),
                now=NOW,
                contract_confirmed=True,
            )
        )
        second = claim_correction_wire_candidate(
            self.engine,
            source_event_id=UUID(CORRECTION_ID),
            now=NOW + timedelta(seconds=31),
            contract_confirmed=True,
        )
        self.assertIsNotNone(second)
        assert second is not None
        self.assertNotEqual(first.lease_token, second.lease_token)
        self.assertFalse(
            finish_correction_wire_candidate(
                self.engine, claim=first, disposition="delivered", now=NOW
            )
        )
        self.assertTrue(
            finish_correction_wire_candidate(
                self.engine,
                claim=second,
                disposition="retry",
                now=NOW + timedelta(seconds=31),
                error_code="b_timeout_unresolved",
                retry_after_seconds=30,
            )
        )
        pending = self.row()
        self.assertEqual((pending["state"], pending["attempt_count"]), ("pending", 2))
        self.assertEqual(pending["body"], _internal())
        self.assertEqual(pending["body_sha256"], _digest(_internal()))
        self.assertIsNone(
            claim_correction_wire_candidate(
                self.engine,
                source_event_id=UUID(CORRECTION_ID),
                now=NOW + timedelta(seconds=60),
                contract_confirmed=True,
            )
        )
        third = claim_correction_wire_candidate(
            self.engine,
            source_event_id=UUID(CORRECTION_ID),
            now=NOW + timedelta(seconds=61),
            contract_confirmed=True,
        )
        self.assertEqual(third.attempt_count, 3)

    def test_missing_original_or_changed_raw_never_claims_or_sends(self):
        with self.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == UUID(ORIGINAL_ID))
                .values(state="pending")
            )
        self.assertIsNone(
            claim_correction_wire_candidate(
                self.engine,
                source_event_id=UUID(CORRECTION_ID),
                now=NOW,
                contract_confirmed=True,
            )
        )
        self.assertEqual(
            (self.row()["state"], self.row()["attempt_count"]), ("held_contract", 0)
        )
        with self.engine.begin() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.source_event_id == UUID(ORIGINAL_ID))
                .values(state="delivered")
            )
            connection.execute(
                update(observations)
                .where(observations.c.id == UUID(OBSERVATION_ID))
                .values(raw_text="{}")
            )
        with self.assertRaisesRegex(ValueError, "raw_text and raw_payload"):
            claim_correction_wire_candidate(
                self.engine,
                source_event_id=UUID(CORRECTION_ID),
                now=NOW,
                contract_confirmed=True,
            )
        self.assertEqual(
            (self.row()["state"], self.row()["attempt_count"]), ("held_contract", 0)
        )

    def test_lost_post_reply_reads_same_wire_digest_and_finishes(self):
        port = _BPort(self.wire.content_digest, lost_reply=True)
        self.assertEqual(
            deliver_correction_once_candidate(
                self.engine,
                source_event_id=UUID(CORRECTION_ID),
                now=NOW,
                port=port,
                contract_confirmed=True,
            ),
            "delivered",
        )
        self.assertEqual((port.lookup_count, port.send_count), (2, 1))
        row = self.row()
        self.assertEqual((row["state"], row["attempt_count"]), ("delivered", 1))
        self.assertEqual(row["body"], _internal())
        self.assertEqual(row["body_sha256"], _digest(_internal()))

    def test_bad_digest_cannot_be_recorded_as_delivered(self):
        port = _BPort("sha256:" + "0" * 64)
        self.assertEqual(
            deliver_correction_once_candidate(
                self.engine,
                source_event_id=UUID(CORRECTION_ID),
                now=NOW,
                port=port,
                contract_confirmed=True,
            ),
            "retry",
        )
        row = self.row()
        self.assertEqual((row["state"], row["attempt_count"]), ("pending", 1))
        self.assertEqual(row["last_error_code"], "b_wire_digest_unverifiable")

    def test_wrong_source_receipt_cannot_be_recorded_as_delivered(self):
        class WrongSourcePort(_BPort):
            def send(self, body: dict) -> RemoteCandidateReceipt:
                self.send_count += 1
                return RemoteCandidateReceipt(
                    UUID(ORIGINAL_ID), "accepted_new", body_sha256=self.digest
                )

        port = WrongSourcePort(self.wire.content_digest)
        self.assertEqual(
            deliver_correction_once_candidate(
                self.engine,
                source_event_id=UUID(CORRECTION_ID),
                now=NOW,
                port=port,
                contract_confirmed=True,
            ),
            "retry",
        )
        self.assertEqual(self.row()["last_error_code"], "b_source_unverifiable")

    def test_http_adapter_rejects_internal_body_and_sends_only_valid_wire(self):
        class Transport:
            def __init__(self, digest: str) -> None:
                self.digest = digest
                self.calls: list[tuple[str, str, bytes | None]] = []

            def request(
                self, method: str, path: str, body: bytes | None = None
            ) -> HttpCandidateReply:
                self.calls.append((method, path, body))
                return HttpCandidateReply(
                    201,
                    json.dumps(
                        {
                            "status": "accepted_new",
                            "source_event_id": CORRECTION_ID,
                            "content_digest": self.digest,
                        }
                    ).encode(),
                )

        transport = Transport(self.wire.content_digest)
        port = BHttpLinkedCorrectionPort(transport)
        with self.assertRaises(InvalidLocalCandidate):
            port.send(_internal())
        self.assertEqual(transport.calls, [])
        self.assertEqual(
            validate_correction_wire_body_candidate(self.wire.body),
            self.wire.content_digest,
        )
        receipt = port.send(self.wire.body)
        self.assertEqual(receipt.source_event_id, UUID(CORRECTION_ID))
        self.assertEqual(
            receipt.body_sha256, self.wire.content_digest.removeprefix("sha256:")
        )
        self.assertEqual(len(transport.calls), 1)
        sent = json.loads(transport.calls[0][2])
        self.assertEqual(sent, self.wire.body)


if __name__ == "__main__":
    unittest.main()
