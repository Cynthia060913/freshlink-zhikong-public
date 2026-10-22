"""A delivery state-machine tests with explicit mocked sessions, not PG proof."""

import json
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from uuid import UUID, uuid4

from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.modules.alerts.models.event import CandidateReceipt, compute_content_digest
from app.modules.iot.event_delivery import (
    EventSnapshot,
    deliver_connectivity_event,
    deliver_to_local_alerts,
)
from app.modules.iot.models import DeviceConnectivityEvent


class ConnectivityDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.event = DeviceConnectivityEvent(
            source_event_id=uuid4(),
            device_id=uuid4(),
            warehouse_id=uuid4(),
            occurred_at=datetime(2026, 10, 15, tzinfo=UTC),
            kind="offline",
            reason="heartbeat_timeout",
            delivery_state="pending",
        )
        self.source = self.event.source_event_id
        self.first = MagicMock(spec=Session)
        self.second = MagicMock(spec=Session)
        for session in (self.first, self.second):
            session.__enter__.return_value = session
        self.first.get.return_value = self.event
        self.second.exec.return_value.first.return_value = deepcopy(self.event)
        self.factory = Mock(side_effect=[self.first, self.second])
        self.bodies = []

    def receive(self, payload):
        self.first.__exit__.assert_called_once()
        self.bodies.append(payload.model_dump(mode="json"))
        return CandidateReceipt(
            status="accepted_new",
            source_event_id=payload.source_event_id,
            content_digest=compute_content_digest(payload),
            event_id=uuid4(),
        )

    def run_delivery(self, receiver=None):
        return deliver_connectivity_event(
            self.factory, receiver or self.receive, self.source
        )

    def test_matching_new_receipt_marks_only_after_target_and_a_commit(self):
        result = self.run_delivery()
        self.assertEqual(result.status, "delivered")
        self.assertIsNotNone(result.target_event_id)
        self.second.commit.assert_called_once()
        saved = self.second.add.call_args.args[0]
        self.assertEqual(saved.delivery_state, "delivered")
        self.assertEqual(saved.reason, "heartbeat_timeout")
        self.assertEqual(self.bodies[0]["sampled_at"], "2026-10-15T00:00:00Z")
        self.assertNotIn("reason", self.bodies[0])

    def test_published_f04_vectors_match_actual_a_to_b_dto_mapping(self):
        fixture = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "contracts/examples/a-device-event-delivery-rc2.json"
            ).read_text(encoding="utf-8")
        )
        for vector in fixture["vectors"]:
            source = vector["a_event"]
            row = DeviceConnectivityEvent(
                source_event_id=UUID(source["source_event_id"]),
                device_id=UUID(source["device_id"]),
                warehouse_id=UUID(source["warehouse_id"]),
                occurred_at=datetime.fromisoformat(source["occurred_at"]),
                kind=source["kind"],
                reason=source["reason"],
                related_source_event_id=UUID(source["related_source_event_id"])
                if source["related_source_event_id"]
                else None,
            )
            actual = EventSnapshot.from_row(row).payload().model_dump(mode="json")
            self.assertEqual(actual, vector["b_payload"])
            self.assertEqual(row.reason, source["reason"])

    def test_target_duplicate_receipt_can_complete_lost_a_checkpoint(self):
        def duplicate(payload):
            receipt = self.receive(payload)
            return receipt.model_copy(
                update={
                    "status": "accepted_duplicate",
                    "stored_event_id": receipt.event_id,
                    "event_id": None,
                }
            )

        self.assertEqual(self.run_delivery(duplicate).status, "delivered")

    def test_recovery_uses_its_own_source_and_original_reference(self):
        original = uuid4()
        self.event.kind = "recovered"
        self.event.related_source_event_id = original
        self.second.exec.return_value.first.return_value = deepcopy(self.event)
        self.assertEqual(self.run_delivery().status, "delivered")
        self.assertEqual(self.bodies[0]["related_source_event_id"], str(original))
        self.assertEqual(self.bodies[0]["source_event_id"], str(self.source))

    def test_missing_event_does_not_call_b(self):
        self.first.get.return_value = None
        receiver = Mock()
        self.assertEqual(self.run_delivery(receiver).status, "not_found")
        receiver.assert_not_called()

    def test_delivered_event_does_not_call_b_again(self):
        self.event.delivery_state = "delivered"
        receiver = Mock()
        self.assertEqual(self.run_delivery(receiver).status, "already_delivered")
        receiver.assert_not_called()

    def test_held_unbound_is_not_rewritten_or_sent(self):
        self.event.delivery_state = "held_unbound"
        receiver = Mock()
        self.assertEqual(self.run_delivery(receiver).status, "held_unbound")
        receiver.assert_not_called()

    def test_null_warehouse_is_not_sent(self):
        self.event.warehouse_id = None
        receiver = Mock()
        self.assertEqual(self.run_delivery(receiver).status, "held_unbound")
        receiver.assert_not_called()

    def test_failed_state_requires_explicit_operator_policy(self):
        self.event.delivery_state = "failed"
        self.assertEqual(self.run_delivery().reason_code, "source_not_pending")
        self.assertEqual(self.bodies, [])

    def test_invalid_source_time_is_blocked_without_send(self):
        self.event.occurred_at = datetime(2026, 10, 15)
        self.assertEqual(self.run_delivery().reason_code, "source_invalid")
        self.assertEqual(self.bodies, [])

    def test_target_retryable_leaves_source_pending(self):
        receiver = Mock(
            return_value=CandidateReceipt(
                status="retryable_failure",
                source_event_id=str(self.source),
                retryable=True,
            )
        )
        self.assertEqual(self.run_delivery(receiver).status, "retryable_failure")
        self.second.commit.assert_not_called()

    def test_target_rejection_is_blocked_not_delivered(self):
        for status in ("invalid", "conflict"):
            self.factory = Mock(side_effect=[self.first, self.second])
            receiver = Mock(
                return_value=CandidateReceipt(
                    status=status, source_event_id=str(self.source)
                )
            )
            self.assertEqual(self.run_delivery(receiver).status, "blocked")
        self.second.commit.assert_not_called()

    def test_malformed_or_mismatched_receipts_never_mark_delivered(self):
        for field, value in (
            ("source_event_id", str(uuid4())),
            ("content_digest", "sha256:" + "0" * 64),
            ("event_id", None),
        ):
            self.factory = Mock(side_effect=[self.first, self.second])
            self.first.__exit__.reset_mock()

            def wrong(payload, key=field, wrong_value=value):
                return self.receive(payload).model_copy(update={key: wrong_value})

            self.assertEqual(self.run_delivery(wrong).reason_code, "receipt_mismatch")
        self.factory = Mock(side_effect=[self.first, self.second])
        self.assertEqual(
            self.run_delivery(Mock(return_value={})).reason_code, "receipt_invalid"
        )
        self.second.commit.assert_not_called()

    def test_source_mutation_during_b_send_cannot_be_marked(self):
        changed = deepcopy(self.event)
        changed.reason = "different historical reason"
        self.second.exec.return_value.first.return_value = changed
        self.assertEqual(self.run_delivery().reason_code, "source_changed")
        self.second.commit.assert_not_called()

    def test_receiver_mutating_body_cannot_acknowledge_original(self):
        def mutate(payload):
            payload.device_id = str(uuid4())
            return self.receive(payload)

        self.assertEqual(self.run_delivery(mutate).reason_code, "receipt_mismatch")
        self.second.commit.assert_not_called()

    def test_source_missing_after_b_send_is_blocked(self):
        self.second.exec.return_value.first.return_value = None
        self.assertEqual(self.run_delivery().reason_code, "source_changed")

    def test_source_becomes_unbound_after_b_send_is_blocked(self):
        row = deepcopy(self.event)
        row.warehouse_id = None
        self.second.exec.return_value.first.return_value = row
        self.assertEqual(self.run_delivery().reason_code, "source_changed")

    def test_concurrent_sender_already_marked_is_successful_no_second_write(self):
        row = deepcopy(self.event)
        row.delivery_state = "delivered"
        self.second.exec.return_value.first.return_value = row
        self.assertEqual(self.run_delivery().status, "already_delivered")
        self.second.commit.assert_not_called()

    def test_source_state_changed_after_send_is_blocked(self):
        row = deepcopy(self.event)
        row.delivery_state = "failed"
        self.second.exec.return_value.first.return_value = row
        self.assertEqual(self.run_delivery().reason_code, "source_not_pending")

    def test_read_or_b_or_checkpoint_storage_fault_is_retryable(self):
        failure = OperationalError("synthetic", {}, RuntimeError("fixture"))
        for stage in ("read", "b", "commit"):
            with self.subTest(stage=stage):
                self.setUp()
                receiver = self.receive
                if stage == "read":
                    self.first.get.side_effect = failure
                elif stage == "b":
                    receiver = Mock(side_effect=failure)
                else:
                    self.second.commit.side_effect = failure
                self.assertEqual(
                    self.run_delivery(receiver).status, "retryable_failure"
                )

    def test_unexpected_receiver_error_is_not_swallowed_as_success(self):
        with self.assertRaises(RuntimeError):
            self.run_delivery(Mock(side_effect=RuntimeError("fixture")))

    def test_local_bridge_uses_b_service_in_its_own_session(self):
        with (
            patch(
                "app.modules.iot.event_delivery.deliver_connectivity_event"
            ) as deliver,
            patch("app.modules.iot.event_delivery.Session") as sessions,
            patch("app.modules.iot.event_delivery.receive_device_event") as receive,
        ):
            deliver_to_local_alerts(Mock(), self.source)
            factory, receiver, identity = deliver.call_args.args
            self.assertEqual(identity, self.source)
            factory()
            payload = Mock()
            receiver(payload)
            receive.assert_called_once_with(
                sessions.return_value.__enter__.return_value, payload
            )
