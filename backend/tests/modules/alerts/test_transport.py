"""Unit tests for the shared notification contract.

Pins down the minimum fields required by INTERFACES /
02-B-Agent指令.md (notification result contract).
Pure-Python, no DB, no FastAPI app instance. Should run with either
``python -m unittest`` or ``pytest`` (no fixtures).
"""

from __future__ import annotations

import unittest
import uuid
from dataclasses import FrozenInstanceError

from app.modules.alerts.notifications import (
    DeliveryOutcome,
    NotificationEnvelope,
    NotificationKind,
    NotificationResult,
    NotificationTransport,
)


class EnvelopeSourceEventIdCase(unittest.TestCase):
    def test_source_event_id_round_trips(self) -> None:
        eid = uuid.uuid4()
        envelope = NotificationEnvelope(
            source_event_id=eid,
            kind=NotificationKind.SMTP_EMAIL,
            subject="s",
            body_text="b",
            recipients=("alice@example.com",),
        )
        self.assertEqual(envelope.source_event_id, eid)


class NotificationResultFrozenCase(unittest.TestCase):
    def test_result_is_immutable(self) -> None:
        result = NotificationResult(
            attempt_id=uuid.uuid4(),
            transport=NotificationKind.SMTP_EMAIL,
            outcome=DeliveryOutcome.ACCEPTED_BY_SERVICE,
            delivered=True,
        )
        with self.assertRaises(FrozenInstanceError):
            result.delivered = False  # type: ignore[misc]  # ty: ignore[invalid-assignment]


class NotificationTransportAbstractCase(unittest.TestCase):
    def test_transport_must_be_subclassed(self) -> None:
        with self.assertRaises(TypeError):
            NotificationTransport()  # type: ignore[abstract]  # ty: ignore[call-non-callable]
