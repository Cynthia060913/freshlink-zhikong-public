"""Heartbeat policy tests, not broker/DB/notification acceptance evidence."""

import json
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

from app.modules.iot.heartbeat import (
    ConnectivityState,
    HeartbeatPolicy,
    plan_heartbeat,
    plan_offline,
    receive_heartbeat,
)
from app.modules.iot.ingress import DeviceContext, IngressRejected

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
MESSAGE = UUID("22222222-2222-4222-8222-222222222222")
NEXT = UUID("33333333-3333-4333-8333-333333333333")
WAREHOUSE = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 10, 12, tzinfo=UTC)
TOPIC = f"freshlink/v1/devices/{DEVICE}/heartbeat"


def raw() -> dict:
    return {
        "schema_version": "1.0",
        "device_id": str(DEVICE),
        "message_id": str(MESSAGE),
        "sent_at": NOW.isoformat(),
    }


def received(data=None, **kwargs):
    args = {
        "topic": TOPIC,
        "payload": json.dumps(data if data is not None else raw()).encode(),
        "context": DeviceContext(DEVICE, None),
        "received_at": NOW,
        "retained": False,
    }
    args.update(kwargs)
    return receive_heartbeat(**args)


def online():
    return plan_heartbeat(
        ConnectivityState(DEVICE, WAREHOUSE), received(), previous_fingerprint=None
    ).state


class TestHeartbeatBoundary(unittest.TestCase):
    def reject(self, reason, data=None, **kwargs):
        with self.assertRaises(IngressRejected) as caught:
            received(data, **kwargs)
        self.assertEqual(caught.exception.reason_code, reason)
        self.assertFalse(hasattr(caught.exception, "raw_text"))

    def test_unbound_registered_device_is_allowed(self):
        result = received()
        self.assertEqual(result.device_id, DEVICE)
        self.assertEqual(result.received_at, NOW)
        self.assertEqual(len(result.fingerprint), 64)
        with self.assertRaises(FrozenInstanceError):
            result.sent_at = NOW

    def test_identity_and_trusted_metadata(self):
        for reason, args in (
            (
                "device_unknown",
                {"context": DeviceContext(DEVICE, None, registered=False)},
            ),
            (
                "device_disabled",
                {"context": DeviceContext(DEVICE, None, enabled=False)},
            ),
            ("retained_heartbeat", {"retained": True}),
            ("retained_heartbeat", {"retained": None}),
            ("topic_mismatch", {"topic": TOPIC + "/spoof"}),
        ):
            with self.subTest(reason=reason, args=args):
                self.reject(reason, **args)
        self.reject("identity_mismatch", {**raw(), "device_id": str(NEXT)})
        self.reject("identity_mismatch", {**raw(), "device_id": None})
        self.reject("identity_mismatch", {**raw(), "warehouse_id": str(WAREHOUSE)})
        bound = DeviceContext(DEVICE, WAREHOUSE)
        self.assertEqual(
            received(
                {**raw(), "warehouse_id": str(WAREHOUSE)}, context=bound
            ).device_id,
            DEVICE,
        )
        self.reject(
            "identity_mismatch", {**raw(), "warehouse_id": str(NEXT)}, context=bound
        )

    def test_json_boundary_and_redacted_errors(self):
        for reason, payload in (
            ("payload_too_large", b" " * 32769),
            ("invalid_encoding", b"\xff"),
            ("invalid_json", b"{"),
            ("invalid_json", b'{"x":1,"x":2}'),
            ("invalid_json", b'{"x":NaN}'),
            ("invalid_json", b'{"x":1e400}'),
            ("invalid_payload", b"[]"),
            ("invalid_payload", b'{"x":' + b"[" * 32 + b"0" + b"]" * 32 + b"}"),
        ):
            with self.subTest(reason=reason, payload=payload[:30]):
                self.reject(reason, payload=payload)
        self.reject("invalid_schema", {**raw(), "schema_version": "2.0"})

    def test_malformed_timestamps_and_message_ids(self):
        for value in (
            None,
            17,
            "2026-10-12",
            "2026-10-12T00:00:00",
            "bad",
            "9999-99-99T00:00:00Z",
        ):
            with self.subTest(timestamp=value):
                self.reject("invalid_heartbeat", {**raw(), "sent_at": value})
        for value in (None, 17, "bad"):
            with self.subTest(message=value):
                self.reject("invalid_heartbeat", {**raw(), "message_id": value})

    def test_age_and_future_exact_boundaries(self):
        self.assertEqual(received(received_at=NOW + timedelta(seconds=89)).sent_at, NOW)
        self.reject("stale_heartbeat", received_at=NOW + timedelta(seconds=90))
        self.assertEqual(received(received_at=NOW - timedelta(seconds=30)).sent_at, NOW)
        self.reject(
            "future_heartbeat", received_at=NOW - timedelta(seconds=30, microseconds=1)
        )

    def test_utc_equivalence_and_extension_identity(self):
        local = NOW.astimezone(timezone(timedelta(hours=8)))
        first = received()
        same = received({**raw(), "sent_at": local.isoformat()}, received_at=local)
        self.assertEqual(first, same)
        reordered = dict(reversed(list(raw().items())))
        self.assertEqual(received(reordered).fingerprint, first.fingerprint)
        self.assertNotEqual(
            received({**raw(), "extension": 1}).fingerprint, first.fingerprint
        )
        with self.assertRaises(ValueError):
            received(received_at=NOW.replace(tzinfo=None))

    def test_policy_configuration_validation(self):
        for field, value in (
            ("offline_after_seconds", 0),
            ("maximum_age_seconds", 0),
            ("maximum_future_skew_seconds", -1),
            ("offline_after_seconds", True),
            ("maximum_age_seconds", 1.5),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                HeartbeatPolicy(**{field: value})
        self.assertEqual(
            HeartbeatPolicy(maximum_future_skew_seconds=0).maximum_future_skew_seconds,
            0,
        )


class TestConnectivityPlans(unittest.TestCase):
    def test_never_seen_remains_unknown(self):
        state = ConnectivityState(DEVICE)
        plan = plan_offline(state, now=NOW + timedelta(days=365))
        self.assertIs(plan.state, state)
        self.assertEqual(plan.events, ())

    def test_initial_and_regular_heartbeat(self):
        first = plan_heartbeat(
            ConnectivityState(DEVICE), received(), previous_fingerprint=None
        )
        self.assertEqual(first.state.status, "online")
        self.assertEqual(first.events, ())
        later = received(
            {
                **raw(),
                "message_id": str(NEXT),
                "sent_at": (NOW + timedelta(seconds=30)).isoformat(),
            },
            received_at=NOW + timedelta(seconds=31),
        )
        update = plan_heartbeat(first.state, later, previous_fingerprint=None)
        self.assertEqual(update.state.last_seen_at, NOW + timedelta(seconds=31))
        self.assertEqual(update.state.last_sent_at, NOW + timedelta(seconds=30))
        self.assertEqual(update.events, ())

    def test_duplicate_and_conflict_cannot_renew_last_seen(self):
        state, heartbeat = online(), received()
        for prior, outcome in (
            (heartbeat.fingerprint, "duplicate"),
            ("another fingerprint", "conflict"),
        ):
            with self.subTest(outcome=outcome):
                plan = plan_heartbeat(
                    state,
                    replace(heartbeat, received_at=NOW + timedelta(seconds=80)),
                    previous_fingerprint=prior,
                )
                self.assertIs(plan.state, state)
                self.assertEqual(plan.outcome, outcome)
                self.assertEqual(plan.events, ())

    def test_out_of_order_does_not_revive(self):
        state = online()
        for sent, received_time in (
            (NOW, NOW + timedelta(seconds=10)),
            (NOW - timedelta(seconds=1), NOW),
            (NOW + timedelta(seconds=1), NOW - timedelta(seconds=1)),
        ):
            with self.subTest(sent=sent, received_time=received_time):
                heartbeat = replace(
                    received(), message_id=NEXT, sent_at=sent, received_at=received_time
                )
                plan = plan_heartbeat(state, heartbeat, previous_fingerprint=None)
                self.assertIs(plan.state, state)
                self.assertEqual(plan.outcome, "out_of_order")

    def test_offline_deadline_and_stable_event_id(self):
        state = online()
        self.assertEqual(
            plan_offline(state, now=NOW + timedelta(seconds=89)).events, ()
        )
        plan = plan_offline(state, now=NOW + timedelta(seconds=90))
        self.assertEqual(plan.state.status, "offline")
        self.assertEqual(plan.events[0].occurred_at, NOW + timedelta(seconds=90))
        self.assertEqual(plan.events[0].warehouse_id, WAREHOUSE)
        self.assertEqual(
            plan.events, plan_offline(state, now=NOW + timedelta(hours=1)).events
        )
        self.assertEqual(
            plan_offline(plan.state, now=NOW + timedelta(hours=1)).events, ()
        )
        with self.assertRaises(ValueError):
            plan_offline(state, now=NOW - timedelta(microseconds=1))

    def test_recovery_links_original_without_reusing_id(self):
        offline = plan_offline(online(), now=NOW + timedelta(seconds=90)).state
        heartbeat = replace(
            received(),
            message_id=NEXT,
            sent_at=NOW + timedelta(seconds=100),
            received_at=NOW + timedelta(seconds=101),
        )
        plan = plan_heartbeat(offline, heartbeat, previous_fingerprint=None)
        self.assertEqual(plan.state.status, "online")
        self.assertIsNone(plan.state.offline_source_event_id)
        self.assertEqual(len(plan.events), 1)
        event = plan.events[0]
        self.assertEqual(event.kind, "recovered")
        self.assertEqual(event.related_source_event_id, offline.offline_source_event_id)
        self.assertNotEqual(event.source_event_id, event.related_source_event_id)
        self.assertEqual(
            plan.events,
            plan_heartbeat(offline, heartbeat, previous_fingerprint=None).events,
        )
        self.assertEqual(
            plan_heartbeat(
                plan.state, heartbeat, previous_fingerprint=heartbeat.fingerprint
            ).events,
            (),
        )

    def test_delayed_scanner_preserves_outage_pair(self):
        heartbeat = replace(
            received(),
            message_id=NEXT,
            sent_at=NOW + timedelta(seconds=100),
            received_at=NOW + timedelta(seconds=101),
        )
        plan = plan_heartbeat(online(), heartbeat, previous_fingerprint=None)
        self.assertEqual(
            tuple(event.kind for event in plan.events), ("offline", "recovered")
        )
        self.assertEqual(
            plan.events[1].related_source_event_id, plan.events[0].source_event_id
        )
        second_outage = plan_offline(plan.state, now=NOW + timedelta(seconds=191))
        self.assertNotEqual(
            second_outage.events[0].source_event_id, plan.events[0].source_event_id
        )

    def test_custom_threshold_and_normalized_server_time(self):
        state = online()
        local = (NOW + timedelta(seconds=5)).astimezone(timezone(timedelta(hours=8)))
        plan = plan_offline(
            state, now=local, policy=HeartbeatPolicy(offline_after_seconds=5)
        )
        self.assertEqual(plan.events[0].occurred_at, NOW + timedelta(seconds=5))

    def test_invalid_state_and_wrong_device_fail_closed(self):
        cases = (
            {"status": "bad"},
            {"status": "online"},
            {"last_seen_at": NOW},
            {"offline_source_event_id": MESSAGE},
            {
                "status": "offline",
                "last_seen_at": NOW,
                "last_sent_at": NOW,
                "last_message_id": MESSAGE,
            },
            {
                "status": "online",
                "last_seen_at": NOW.replace(tzinfo=None),
                "last_sent_at": NOW,
                "last_message_id": MESSAGE,
            },
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ConnectivityState(DEVICE, **kwargs)
        with self.assertRaises(ValueError):
            plan_heartbeat(
                online(), replace(received(), device_id=NEXT), previous_fingerprint=None
            )


if __name__ == "__main__":
    unittest.main()
