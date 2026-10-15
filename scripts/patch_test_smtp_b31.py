#!/usr/bin/env python3
"""Patch tests/modules/alerts/test_smtp.py for B3-1 staged classification."""

from __future__ import annotations

import pathlib

p = pathlib.Path("backend/tests/modules/alerts/test_smtp.py")
s = p.read_text(encoding="utf-8")
n0 = s

R = [
    # NOOP is now phase-labelled
    (
        '        self.assertTrue((result.server_response or "").startswith("250"))\n        self.assertIn("OK", result.server_response or "")',
        '        # NOOP reply is explicitly phase-labelled (not a DATA receipt)\n        self.assertTrue((result.server_response or "").startswith("noop 250"))\n        self.assertIn("OK", result.server_response or "")',
    ),
    (
        '        self.assertEqual(result.server_response, "250 OK from server")',
        '        self.assertEqual(result.server_response, "noop 250 OK from server")',
    ),
    # mixed partial refusal: TRANSIENT so retryable addresses are not lost
    (
        """        result = SMTPTransport(_config(), _factory()).send(envelope)
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        # Permanent wins: no retry-after.
        self.assertIsNone(result.retry_after_s)
        self.assertIn("busy@example.com", result.detail)
        self.assertIn("dead@example.com", result.detail)""",
        """        result = SMTPTransport(_config(), _factory()).send(envelope)
        self.assertFalse(result.delivered)
        # Mixed 4yz+5yz: top-level TRANSIENT so the 450 address stays
        # retryable; per-recipient records carry the permanent flag.
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(result.retry_after_s, 30.0)
        self.assertIn("busy@example.com", result.detail)
        self.assertIn("dead@example.com", result.detail)
        by_addr = {r.address: r for r in result.recipient_outcomes}
        self.assertTrue(by_addr["busy@example.com"].refused)
        self.assertTrue(by_addr["busy@example.com"].retriable)
        self.assertEqual(by_addr["busy@example.com"].code, 450)
        self.assertTrue(by_addr["dead@example.com"].refused)
        self.assertFalse(by_addr["dead@example.com"].retriable)
        self.assertEqual(by_addr["dead@example.com"].code, 550)""",
    ),
]

misses = 0
for old, new in R:
    if old in s:
        s = s.replace(old, new, 1)
    else:
        misses += 1
        print("MISS:", old.splitlines()[0][:70])

NEW_CASES = '''

class SMTPTransportFullRefusalClassificationCase(unittest.TestCase):
    """A replay 6108497780: SMTPRecipientsRefused must be classified by
    the actual reply codes — all-450 is transient, not permanent."""

    def _refused(self, mapping: dict[str, tuple[int, bytes]]) -> object:
        return SMTPTransport(_config(), _factory()).send(
            NotificationEnvelope(
                source_event_id=uuid.uuid4(),
                kind=NotificationKind.SMTP_EMAIL,
                subject="s",
                body_text="b",
                recipients=tuple(mapping),
            )
        )

    def test_all_450_yields_transient_with_backoff(self) -> None:
        MockSMTP.state = _State(
            send_raises=smtplib.SMTPRecipientsRefused(
                {"a@example.com": (450, b"try later")}
            )
        )
        result = self._refused({"a@example.com": (450, b"try later")})
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(result.retry_after_s, 30.0)

    def test_all_550_yields_permanent(self) -> None:
        MockSMTP.state = _State(
            send_raises=smtplib.SMTPRecipientsRefused(
                {"a@example.com": (550, b"no such user")}
            )
        )
        result = self._refused({"a@example.com": (550, b"no such user")})
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIsNone(result.retry_after_s)

    def test_mixed_full_refusal_keeps_retryable(self) -> None:
        MockSMTP.state = _State(
            send_raises=smtplib.SMTPRecipientsRefused(
                {
                    "temp@example.com": (451, b"local error"),
                    "dead@example.com": (550, b"no such user"),
                }
            )
        )
        result = self._refused(
            {
                "temp@example.com": (451, b"local error"),
                "dead@example.com": (550, b"no such user"),
            }
        )
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(result.retry_after_s, 30.0)
        by_addr = {r.address: r for r in result.recipient_outcomes}
        self.assertTrue(by_addr["temp@example.com"].retriable)
        self.assertFalse(by_addr["dead@example.com"].retriable)
        # No accepted recipients in a full refusal.
        self.assertFalse(any(not r.refused for r in result.recipient_outcomes))


class SMTPTransportAuthAndHeloClassificationCase(unittest.TestCase):
    """A replay 6110998037: auth 454 → transient; HELO 550 → permanent."""

    def test_auth_454_yields_transient(self) -> None:
        MockSMTP.state = _State(
            login_raises=smtplib.SMTPAuthenticationError(454, b"temporarily unavailable")
        )
        cfg = SMTPConfig(
            host="smtp.example.com",
            port=587,
            timeout_seconds=2.0,
            use_tls=True,
            from_email="bot@example.com",
            username="bot@example.com",
            password="placeholder-not-a-real-secret",
        )
        result = SMTPTransport(cfg, _factory()).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(result.retry_after_s, 30.0)
        self.assertIn("454", result.detail)

    def test_auth_535_yields_permanent(self) -> None:
        MockSMTP.state = _State(
            login_raises=smtplib.SMTPAuthenticationError(535, b"creds invalid")
        )
        cfg = SMTPConfig(
            host="smtp.example.com",
            port=587,
            timeout_seconds=2.0,
            use_tls=True,
            from_email="bot@example.com",
            username="bot@example.com",
            password="placeholder-not-a-real-secret",
        )
        result = SMTPTransport(cfg, _factory()).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)

    def test_helo_550_yields_permanent(self) -> None:
        MockSMTP.state = _State(
            connect_raises=smtplib.SMTPHeloError(550, b"go away")
        )
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIsNone(result.retry_after_s)
        self.assertIn("550", result.detail)

    def test_helo_421_yields_transient(self) -> None:
        MockSMTP.state = _State(
            connect_raises=smtplib.SMTPHeloError(421, b"service not available")
        )
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(result.retry_after_s, 30.0)


class SMTPTransportPartialAcceptRecordsTestCase(unittest.TestCase):
    """Partial acceptance records every recipient so retries target
    refused-retryable addresses only (A replay 6110998037)."""

    def test_partial_acceptance_records_accepted_and_refused(self) -> None:
        MockSMTP.state = _State(
            partial_refused={"dead@example.com": (550, b"no such user")}
        )
        envelope = NotificationEnvelope(
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.SMTP_EMAIL,
            subject="s",
            body_text="b",
            recipients=("ok@example.com", "dead@example.com"),
        )
        result = SMTPTransport(_config(), _factory()).send(envelope)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        by_addr = {r.address: r for r in result.recipient_outcomes}
        self.assertEqual(set(by_addr), {"ok@example.com", "dead@example.com"})
        ok = by_addr["ok@example.com"]
        self.assertFalse(ok.refused)
        self.assertFalse(ok.retriable)  # accepted → never re-send
        dead = by_addr["dead@example.com"]
        self.assertTrue(dead.refused)
        self.assertFalse(dead.retriable)  # 5yz → permanent
        self.assertIn("never re-send accepted", result.detail)


if __name__ == "__main__":'''

s = s.replace('\n\nif __name__ == "__main__":', NEW_CASES, 1)
p.write_text(s, encoding="utf-8")
print("done; misses:", misses, "; changed:", s != n0)
