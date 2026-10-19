"""Isolated C outbox worker checks; the fake port is not a B service test."""

import json
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine

from app.modules.telemetry.b_http_candidate import (
    BHttpCandidatePort,
    HttpCandidateReply,
)
from app.modules.telemetry.delivery_candidate import (
    RemoteCandidateReceipt,
    deliver_due_batch_candidate,
    deliver_once_candidate,
)
from app.modules.telemetry.outbox_candidate import (
    claim_candidate,
    due_source_ids_candidate,
    finish_candidate,
    outbox,
    stage_candidate,
)
from app.modules.telemetry.storage_candidate import ingest_observation, metadata

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Reception:
    raw_text: str
    reception_id: UUID
    received_at: datetime = NOW
    authenticated_device_id: UUID = DEVICE
    warehouse_id: UUID = WAREHOUSE

    @property
    def raw_payload(self) -> dict[str, Any]:
        return json.loads(self.raw_text)


@dataclass
class FakePort:
    lookup_results: list[RemoteCandidateReceipt | None | Exception] = field(
        default_factory=list
    )
    send_results: list[RemoteCandidateReceipt | Exception] = field(default_factory=list)
    lookup_ids: list[UUID] = field(default_factory=list)
    sent_bodies: list[dict[str, Any]] = field(default_factory=list)

    def lookup(self, source_event_id: UUID) -> RemoteCandidateReceipt | None:
        self.lookup_ids.append(source_event_id)
        result = self.lookup_results.pop(0) if self.lookup_results else None
        if isinstance(result, Exception):
            raise result
        return result

    def send(self, body: dict[str, Any]) -> RemoteCandidateReceipt:
        self.sent_bodies.append(body)
        result = self.send_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class CandidateDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "delivery.db"
        self.engine: Engine = create_engine(f"sqlite:///{self.path}")
        metadata.create_all(self.engine)
        raw = {
            "schema_version": "1.0",
            "device_id": str(DEVICE),
            "message_id": str(uuid4()),
            "sampled_at": "2026-10-18T00:00:00Z",
            "metrics": [{"key": "temperature", "value": 13, "unit": "°C"}],
        }
        observation = ingest_observation(
            self.engine, Reception(json.dumps(raw, ensure_ascii=False), uuid4())
        )
        self.assertEqual(observation.status, "accepted_new")
        assert observation.observation_id is not None
        self.source_id = uuid4()
        self.body = {
            "source_event_id": str(self.source_id),
            "observation_ids": [str(observation.observation_id)],
            "kind": "temperature_high",
            "evidence": {"temperature_c": 13, "quality": ["ok"]},
        }
        self.assertEqual(
            stage_candidate(
                self.engine,
                source_event_id=self.source_id,
                observation_id=observation.observation_id,
                body=self.body,
                now=NOW,
            ),
            "staged",
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def _row(self):
        with self.engine.connect() as connection:
            return (
                connection.execute(
                    select(outbox).where(outbox.c.source_event_id == self.source_id)
                )
                .mappings()
                .one()
            )

    def _receipt(self, status="accepted_new", *, digest: str | None = None):
        return RemoteCandidateReceipt(
            source_event_id=self.source_id,
            status=status,
            body_sha256=self._row()["body_sha256"] if digest is None else digest,
        )

    def _run(self, port: FakePort, *, now: datetime = NOW, max_attempts: int = 10):
        return deliver_once_candidate(
            self.engine,
            source_event_id=self.source_id,
            now=now,
            port=port,
            max_attempts=max_attempts,
        )

    def test_first_acceptance_freezes_body_and_finishes_once(self) -> None:
        port = FakePort(send_results=[self._receipt()])
        self.assertEqual(self._run(port), "delivered")
        self.assertEqual(port.lookup_ids, [self.source_id])
        self.assertEqual(port.sent_bodies, [self.body])
        self.assertEqual(self._row()["state"], "delivered")
        self.assertEqual(self._run(port, now=NOW + timedelta(hours=1)), "not_due")
        self.assertEqual(len(port.sent_bodies), 1)

    def test_locally_incompatible_frozen_body_is_rejected_once(self) -> None:
        class MissingOnlyTransport:
            methods: list[str] = []

            def request(self, method, _path, _body=None):
                self.methods.append(method)
                if method != "GET":
                    raise AssertionError("invalid frozen body must not reach B")
                return HttpCandidateReply(404, b"{}")

        transport = MissingOnlyTransport()
        self.assertEqual(
            deliver_once_candidate(
                self.engine,
                source_event_id=self.source_id,
                now=NOW,
                port=BHttpCandidatePort(transport),
            ),
            "rejected",
        )
        self.assertEqual(transport.methods, ["GET"])
        self.assertEqual(self._row()["last_error_code"], "invalid_local_candidate")
        self.assertEqual(self._row()["state"], "rejected")
        self.assertEqual(
            deliver_once_candidate(
                self.engine,
                source_event_id=self.source_id,
                now=NOW + timedelta(hours=1),
                port=BHttpCandidatePort(transport),
            ),
            "not_due",
        )

    def test_timeout_after_remote_commit_is_resolved_by_source(self) -> None:
        port = FakePort(
            lookup_results=[None, self._receipt("accepted_duplicate")],
            send_results=[TimeoutError("simulated lost response")],
        )
        self.assertEqual(self._run(port), "delivered")
        self.assertEqual(port.lookup_ids, [self.source_id, self.source_id])
        self.assertEqual(self._row()["attempt_count"], 1)
        self.assertEqual(self._row()["state"], "delivered")

    def test_unresolved_timeout_retries_frozen_source_and_body_after_reopen(
        self,
    ) -> None:
        first = FakePort(send_results=[TimeoutError("simulated")])
        self.assertEqual(self._run(first), "retry")
        self.assertEqual(self._row()["last_error_code"], "b_timeout_unresolved")
        self.assertEqual(self._run(first, now=NOW + timedelta(seconds=29)), "not_due")
        self.engine.dispose()
        self.engine = create_engine(f"sqlite:///{self.path}")
        second = FakePort(send_results=[self._receipt("accepted_duplicate")])
        self.assertEqual(
            self._run(second, now=NOW + timedelta(seconds=30)), "delivered"
        )
        self.assertEqual(first.sent_bodies, second.sent_bodies)
        self.assertEqual(self._row()["attempt_count"], 2)

    def test_prior_remote_acceptance_avoids_another_send(self) -> None:
        port = FakePort(lookup_results=[self._receipt("accepted_duplicate")])
        self.assertEqual(self._run(port), "delivered")
        self.assertEqual(port.sent_bodies, [])

    def test_lookup_failure_retries_without_sending_and_budget_stops(self) -> None:
        port = FakePort(lookup_results=[ConnectionError("simulated lookup outage")])
        self.assertEqual(self._run(port, max_attempts=2), "retry")
        self.assertEqual(port.sent_bodies, [])
        self.assertEqual(self._row()["last_error_code"], "b_lookup_unavailable")
        again = FakePort(lookup_results=[ConnectionError("simulated lookup outage")])
        self.assertEqual(
            self._run(again, now=NOW + timedelta(seconds=30), max_attempts=2),
            "rejected",
        )
        self.assertEqual(self._row()["last_error_code"], "retry_budget_exhausted")
        self.assertEqual(self._row()["state"], "rejected")

    def test_authorization_failure_keeps_bounded_failure_evidence(self) -> None:
        port = FakePort(lookup_results=[PermissionError("synthetic auth failure")])
        self.assertEqual(self._run(port, max_attempts=2), "retry")
        self.assertEqual(port.sent_bodies, [])
        self.assertEqual(self._row()["last_error_code"], "b_auth_unavailable")
        second = FakePort(
            lookup_results=[None],
            send_results=[PermissionError("synthetic send auth failure")],
        )
        self.assertEqual(
            self._run(second, now=NOW + timedelta(seconds=30), max_attempts=2),
            "rejected",
        )
        self.assertEqual(self._row()["last_error_code"], "retry_budget_exhausted")

    def test_rejection_and_unverifiable_receipt_never_mark_delivered(self) -> None:
        wrong = FakePort(send_results=[self._receipt(digest="0" * 64)])
        self.assertEqual(self._run(wrong), "retry")
        self.assertEqual(self._row()["last_error_code"], "b_body_unverifiable")
        conflict = FakePort(send_results=[self._receipt("conflict")])
        self.assertEqual(
            self._run(conflict, now=NOW + timedelta(seconds=30)), "rejected"
        )
        self.assertEqual(self._row()["last_error_code"], "b_content_conflict")
        self.assertEqual(self._row()["state"], "rejected")

    def test_retryable_reply_then_invalid_reply_preserves_failure(self) -> None:
        transient = FakePort(send_results=[self._receipt("retryable_failure")])
        self.assertEqual(self._run(transient), "retry")
        self.assertEqual(self._row()["last_error_code"], "b_retryable_failure")
        invalid = FakePort(send_results=[self._receipt("invalid")])
        self.assertEqual(
            self._run(invalid, now=NOW + timedelta(seconds=30)), "rejected"
        )
        self.assertEqual(self._row()["last_error_code"], "b_invalid_candidate")
        self.assertEqual(self._row()["state"], "rejected")

    def test_wrong_source_receipt_is_not_accepted(self) -> None:
        wrong = RemoteCandidateReceipt(
            source_event_id=uuid4(),
            status="accepted_new",
            body_sha256=self._row()["body_sha256"],
        )
        self.assertEqual(self._run(FakePort(send_results=[wrong])), "retry")
        self.assertEqual(self._row()["last_error_code"], "b_source_unverifiable")

    def test_bad_budget_is_rejected_before_claim(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_attempts"):
            self._run(FakePort(), max_attempts=0)
        self.assertEqual(self._row()["attempt_count"], 0)

    def test_bounded_due_discovery_and_batch_do_not_resend_delivered(self) -> None:
        second_id = uuid4()
        second_body = {**self.body, "source_event_id": str(second_id)}
        self.assertEqual(
            stage_candidate(
                self.engine,
                source_event_id=second_id,
                observation_id=UUID(self.body["observation_ids"][0]),
                body=second_body,
                now=NOW + timedelta(seconds=1),
            ),
            "staged",
        )
        self.assertEqual(
            due_source_ids_candidate(self.engine, now=NOW), [self.source_id]
        )
        self.assertEqual(
            deliver_due_batch_candidate(
                self.engine,
                now=NOW,
                port=FakePort(send_results=[self._receipt()]),
                limit=1,
            ),
            [(self.source_id, "delivered")],
        )
        self.assertEqual(
            due_source_ids_candidate(self.engine, now=NOW + timedelta(seconds=1)),
            [second_id],
        )
        with self.engine.connect() as connection:
            digest = connection.execute(
                select(outbox.c.body_sha256).where(
                    outbox.c.source_event_id == second_id
                )
            ).scalar_one()
        self.assertEqual(
            deliver_due_batch_candidate(
                self.engine,
                now=NOW + timedelta(seconds=1),
                port=FakePort(
                    send_results=[
                        RemoteCandidateReceipt(second_id, "accepted_new", digest)
                    ]
                ),
                limit=1,
            ),
            [(second_id, "delivered")],
        )
        self.assertEqual(
            due_source_ids_candidate(self.engine, now=NOW + timedelta(days=1)), []
        )

    def test_expired_lease_is_discovered_and_old_owner_cannot_finish(self) -> None:
        claim = claim_candidate(self.engine, source_event_id=self.source_id, now=NOW)
        assert claim is not None
        self.assertEqual(
            due_source_ids_candidate(self.engine, now=NOW + timedelta(seconds=29)), []
        )
        self.assertEqual(
            due_source_ids_candidate(self.engine, now=NOW + timedelta(seconds=30)),
            [self.source_id],
        )
        self.assertEqual(
            deliver_due_batch_candidate(
                self.engine,
                now=NOW + timedelta(seconds=30),
                port=FakePort(lookup_results=[self._receipt("accepted_duplicate")]),
            ),
            [(self.source_id, "delivered")],
        )
        self.assertFalse(
            finish_candidate(self.engine, claim=claim, disposition="delivered", now=NOW)
        )

    def test_due_page_rejects_unbounded_or_naive_requests(self) -> None:
        for limit in (0, 1001, True):
            with self.assertRaisesRegex(ValueError, "limit"):
                due_source_ids_candidate(self.engine, now=NOW, limit=limit)
        with self.assertRaisesRegex(ValueError, "aware"):
            due_source_ids_candidate(self.engine, now=NOW.replace(tzinfo=None))
        with self.assertRaisesRegex(ValueError, "max_attempts"):
            deliver_due_batch_candidate(
                self.engine, now=NOW, port=FakePort(), max_attempts=0
            )
        self.assertEqual(self._row()["attempt_count"], 0)

    def test_recovery_waits_for_original_delivery_without_using_attempts(self) -> None:
        recovery_id = uuid4()
        recovery = {
            **self.body,
            "source_event_id": str(recovery_id),
            "kind": "temperature_recovered",
            "related_source_event_id": str(self.source_id),
        }
        self.assertEqual(
            stage_candidate(
                self.engine,
                source_event_id=recovery_id,
                observation_id=UUID(self.body["observation_ids"][0]),
                body=recovery,
                now=NOW,
            ),
            "staged",
        )
        self.assertEqual(
            due_source_ids_candidate(self.engine, now=NOW), [self.source_id]
        )
        preflight = FakePort()
        self.assertEqual(
            deliver_once_candidate(
                self.engine, source_event_id=recovery_id, now=NOW, port=preflight
            ),
            "not_due",
        )
        self.assertEqual(preflight.lookup_ids, [])
        self.assertEqual(preflight.sent_bodies, [])
        self.assertEqual(
            self._run(FakePort(send_results=[self._receipt()])), "delivered"
        )
        self.assertEqual(due_source_ids_candidate(self.engine, now=NOW), [recovery_id])
        with self.engine.connect() as connection:
            digest = connection.execute(
                select(outbox.c.body_sha256).where(
                    outbox.c.source_event_id == recovery_id
                )
            ).scalar_one()
        self.assertEqual(
            deliver_once_candidate(
                self.engine,
                source_event_id=recovery_id,
                now=NOW,
                port=FakePort(
                    lookup_results=[
                        None,
                        RemoteCandidateReceipt(
                            self.source_id,
                            "accepted_duplicate",
                            alert_status="resolved",
                        ),
                    ],
                    send_results=[
                        RemoteCandidateReceipt(recovery_id, "accepted_new", digest)
                    ],
                ),
            ),
            "delivered",
        )

    def test_recovery_waits_for_b_resolved_readback_after_acceptance(self) -> None:
        recovery_id = uuid4()
        recovery = {
            **self.body,
            "source_event_id": str(recovery_id),
            "kind": "temperature_recovered",
            "related_source_event_id": str(self.source_id),
        }
        stage_candidate(
            self.engine,
            source_event_id=recovery_id,
            observation_id=UUID(self.body["observation_ids"][0]),
            body=recovery,
            now=NOW,
        )
        self.assertEqual(
            self._run(FakePort(send_results=[self._receipt()])), "delivered"
        )
        with self.engine.connect() as connection:
            digest = connection.execute(
                select(outbox.c.body_sha256).where(
                    outbox.c.source_event_id == recovery_id
                )
            ).scalar_one()
        accepted = RemoteCandidateReceipt(recovery_id, "accepted_new", digest)
        original_open = RemoteCandidateReceipt(
            self.source_id, "accepted_duplicate", alert_status="open"
        )
        first = FakePort(lookup_results=[None, original_open], send_results=[accepted])
        self.assertEqual(
            deliver_once_candidate(
                self.engine, source_event_id=recovery_id, now=NOW, port=first
            ),
            "retry",
        )
        self.assertEqual(first.lookup_ids, [recovery_id, self.source_id])
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(outbox).where(outbox.c.source_event_id == recovery_id)
                )
                .mappings()
                .one()
            )
        self.assertEqual(row["last_error_code"], "b_recovery_unverifiable")
        self.assertEqual(row["state"], "pending")
        original_resolved = RemoteCandidateReceipt(
            self.source_id, "accepted_duplicate", alert_status="resolved"
        )
        second = FakePort(
            lookup_results=[
                RemoteCandidateReceipt(recovery_id, "accepted_duplicate", digest),
                original_resolved,
            ]
        )
        self.assertEqual(
            deliver_once_candidate(
                self.engine,
                source_event_id=recovery_id,
                now=NOW + timedelta(seconds=31),
                port=second,
            ),
            "delivered",
        )
        self.assertEqual(second.sent_bodies, [])
        self.assertEqual(second.lookup_ids, [recovery_id, self.source_id])

    def test_recovery_stays_pending_when_original_is_rejected(self) -> None:
        recovery_id = uuid4()
        recovery = {
            **self.body,
            "source_event_id": str(recovery_id),
            "kind": "temperature_recovered",
            "related_source_event_id": str(self.source_id),
        }
        stage_candidate(
            self.engine,
            source_event_id=recovery_id,
            observation_id=UUID(self.body["observation_ids"][0]),
            body=recovery,
            now=NOW,
        )
        self.assertEqual(
            self._run(FakePort(send_results=[self._receipt("invalid")])),
            "rejected",
        )
        self.assertEqual(due_source_ids_candidate(self.engine, now=NOW), [])
        self.assertEqual(
            deliver_once_candidate(
                self.engine,
                source_event_id=recovery_id,
                now=NOW,
                port=FakePort(),
            ),
            "not_due",
        )
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(outbox).where(outbox.c.source_event_id == recovery_id)
                )
                .mappings()
                .one()
            )
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["attempt_count"], 0)


if __name__ == "__main__":
    unittest.main()
