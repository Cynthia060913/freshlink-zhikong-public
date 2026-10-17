"""APP push transport tests.

覆盖

- :class:`DisabledAppPushTransport` 默认状态（无凭据）返回永久失败；
- envelope.kind 与 transport 类型不匹配；
- 自定义 ``AppPushTransport`` 子类抛各种 AppPushError 时的结果分类；
- 成功路径。
"""

from __future__ import annotations

import unittest
import uuid
from collections.abc import Callable

from app.modules.alerts.notifications import (
    AppPushTransport,
    DeliveryOutcome,
    DisabledAppPushTransport,
    NotConfigured,
    NotificationEnvelope,
    NotificationKind,
    PermanentPushError,
    TransientPushError,
)


def _envelope() -> NotificationEnvelope:
    return NotificationEnvelope(
        source_event_id=uuid.uuid4(),
        kind=NotificationKind.APP_PUSH,
        subject="ping",
        body_text="ping body",
        recipients=("device-token-placeholder",),
    )


class DisabledAppPushTransportCase(unittest.TestCase):
    def test_default_returns_permanent_failure(self) -> None:
        result = DisabledAppPushTransport().send(_envelope())
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIn("not configured", result.detail.lower())

    def test_wrong_envelope_kind_rejected(self) -> None:
        envelope = NotificationEnvelope(
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.SMTP_EMAIL,
            subject="x",
            body_text="y",
            recipients=("someone@example.com",),
        )
        result = DisabledAppPushTransport().send(envelope)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIn("not supported by AppPushTransport", result.detail)


class _RecordingTransport(AppPushTransport):
    def __init__(self, deliver_impl: Callable[[NotificationEnvelope], None]) -> None:
        self._deliver_impl = deliver_impl

    def _deliver(self, envelope: NotificationEnvelope) -> None:
        self._deliver_impl(envelope)


class CustomAppPushSuccessCase(unittest.TestCase):
    def test_success_path(self) -> None:
        result = _RecordingTransport(lambda env: None).send(_envelope())
        self.assertTrue(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.ACCEPTED_BY_SERVICE)
        self.assertIn("accepted", result.detail.lower())


class CustomAppPushNotConfiguredCase(unittest.TestCase):
    def test_not_configured_classified_permanent(self) -> None:
        def fail(_env: NotificationEnvelope) -> None:
            raise NotConfigured("no token")

        result = _RecordingTransport(fail).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertFalse(result.delivered)
        self.assertIn("not configured", result.detail.lower())


class CustomAppPushTransientCase(unittest.TestCase):
    def test_transient_yields_transient_with_backoff(self) -> None:
        def fail(_env: NotificationEnvelope) -> None:
            raise TransientPushError("rate limited")

        result = _RecordingTransport(fail).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertFalse(result.delivered)
        self.assertEqual(result.retry_after_s, 30.0)


class CustomAppPushPermanentCase(unittest.TestCase):
    def test_permanent_yields_permanent(self) -> None:
        def fail(_env: NotificationEnvelope) -> None:
            raise PermanentPushError("token revoked")

        result = _RecordingTransport(fail).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertFalse(result.delivered)


if __name__ == "__main__":
    unittest.main()
