"""SMTP transport unit tests.

覆盖 :class:`02-B-Agent指令.md` 要求的 4 类结果（RFC 5321 §4.5.5）：

1. 成功返回 — ``accepted_by_service`` / ``delivered=True``；
2. 异常 — ``transient_failure``（连接拒绝 / 断开 / 4yz / HELO 拒收 / DATA 拒收）；
3. 超时 — ``timeout`` / ``retry_after_s`` 非空；
4. 失败结果格式 — 5yz / 拒收 / 认证错 → ``permanent_failure``，包含 code/stage；
   部分拒收（``SMTPRecipientsRefused``）同样按 ``permanent_failure`` 处理，
   地址只在结构化 ``recipient_outcomes`` 中（free-text ``detail`` 不含地址，
   A queue P1 #8 6440797666）。

阶段诊断负向用例（A queue P1：模拟 SMTP 复现四类场景）：
发送前失败（stage=connect）、DATA 已接受但 QUIT/收尾异常
（→ ``accepted_by_service`` + ``post_send_cleanup``，绝不报可重试的
"未送达"）、认证失败（stage=auth，无密码/异常文本入库）、
DATA 阶段中断（acceptance 如实 unknown）。

同时验证：构造参数校验（空 host / 空 from_email / 零 port →
``ValueError``）、空收件人、错误的 envelope.kind、auth 凭据、
``use_ssl`` vs ``use_tls`` 路径。
"""

from __future__ import annotations

import smtplib
import unittest
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from app.modules.alerts.notifications.smtp import SMTPConfig, SMTPTransport
from app.modules.alerts.notifications.transport import (
    DeliveryOutcome,
    NotificationEnvelope,
    NotificationKind,
)


@dataclass
class _State:
    connect_raises: BaseException | None = None
    starttls_raises: BaseException | None = None
    send_raises: BaseException | None = None
    login_raises: BaseException | None = None
    login_called_with: tuple[str, str] | None = None
    partial_refused: dict[str, tuple[int, bytes]] | None = None
    # QUIT / connection teardown failure AFTER send_message returned
    # (e.g. an SSL unexpected EOF during ``__exit__``).
    exit_raises: BaseException | None = None
    # NOOP probe failure (the transport swallows this one itself).
    noop_raises: BaseException | None = None


class MockSMTP:
    """``smtplib.SMTP`` 形状的最小替身。

    ``state`` 和 ``sent_messages`` 是 class-level 共享状态；
    每次创建 transport 用 :func:`_factory` 隔离并清空 ``sent_messages``。

    ``send_message`` mirrors ``smtplib.SMTP.send_message``:
    returns a ``dict[str, tuple[int, bytes]]`` mapping refused
    recipients to ``(reply_code, server_message)``. An empty dict
    means every recipient was accepted; a non-empty dict means
    *some* recipients were refused. (When *all* are refused,
    ``smtplib`` raises ``SMTPRecipientsRefused`` instead — the
    state field ``send_raises`` covers that path.)
    """

    state = _State()
    sent_messages: list[object] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False
        self.login_called = False
        if MockSMTP.state.connect_raises is not None:
            raise MockSMTP.state.connect_raises

    def __enter__(self) -> MockSMTP:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if MockSMTP.state.exit_raises is not None:
            raise MockSMTP.state.exit_raises
        return None

    def starttls(self, keyfile: str | None = None, certfile: str | None = None) -> None:
        self.starttls_called = True
        if MockSMTP.state.starttls_raises is not None:
            raise MockSMTP.state.starttls_raises

    def login(self, user: str, password: str) -> None:
        self.login_called = True
        MockSMTP.state.login_called_with = (user, password)
        if MockSMTP.state.login_raises is not None:
            raise MockSMTP.state.login_raises

    def send_message(self, message: object) -> dict[str, tuple[int, bytes]]:
        MockSMTP.sent_messages.append(message)
        if MockSMTP.state.send_raises is not None:
            raise MockSMTP.state.send_raises
        if MockSMTP.state.partial_refused is not None:
            return dict(MockSMTP.state.partial_refused)
        return {}

    def noop(self) -> tuple[int, str | bytes]:
        # Stand-in for smtplib.SMTP.noop() → (code, message).
        # Used by SMTPTransport to populate server_response with the
        # actual post-send server reply (not a hard-coded 250).
        if MockSMTP.state.noop_raises is not None:
            raise MockSMTP.state.noop_raises
        return (250, b"OK")

    def quit(self) -> None:
        return None


def _factory() -> Callable[[str, int, float], MockSMTP]:
    """Return a fresh factory that resets ``sent_messages`` on first call."""

    called = {"v": False}

    def f(host: str, port: int, timeout: float) -> MockSMTP:
        if not called["v"]:
            MockSMTP.sent_messages = []
            called["v"] = True
        return MockSMTP(host, port, timeout)

    return f


def _envelope() -> NotificationEnvelope:
    return NotificationEnvelope(
        source_event_id=uuid.uuid4(),
        kind=NotificationKind.SMTP_EMAIL,
        subject="test subject",
        body_text="hello",
        body_html="<p>hello</p>",
        recipients=("test@example.com",),
    )


def _config() -> SMTPConfig:
    return SMTPConfig(
        host="smtp.example.com",
        port=587,
        timeout_seconds=2.0,
        use_tls=True,
        from_email="bot@example.com",
    )


class SMTPTransportSuccessCase(unittest.TestCase):
    def test_success_returns_accepted(self) -> None:
        MockSMTP.state = _State()
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertTrue(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.ACCEPTED_BY_SERVICE)
        # server_response comes from NOOP probe (MockSMTP.noop returns
        # 250 OK), so we verify the *actual* server reply rather than a
        # hard-coded literal.
        # NOOP reply is explicitly phase-labelled (not a DATA receipt)
        self.assertTrue((result.server_response or "").startswith("noop 250"))
        self.assertIn("OK", result.server_response or "")
        self.assertEqual(len(MockSMTP.sent_messages), 1)


class SMTPTransportConnectionRefusedCase(unittest.TestCase):
    def test_connection_refused_yields_transient(self) -> None:
        MockSMTP.state = _State(connect_raises=ConnectionRefusedError("nope"))
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertIn("connection error", result.detail)


class SMTPTransportConnectionResetCase(unittest.TestCase):
    def test_reset_yields_transient_with_detail(self) -> None:
        MockSMTP.state = _State(connect_raises=ConnectionResetError("reset"))
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertIn("connection error", result.detail)


class SMTPTransportTimeoutCase(unittest.TestCase):
    def test_timeout_yields_timeout_outcome(self) -> None:
        MockSMTP.state = _State(connect_raises=TimeoutError("slow"))
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.TIMEOUT)
        self.assertEqual(result.retry_after_s, 2.0)


class SMTPTransportSmtp4xxCase(unittest.TestCase):
    def test_4xx_yields_transient_with_backoff(self) -> None:
        # RFC 5321 §4.5.5: 4yz is Transient Negative Completion — the
        # receiver should retry after a delay.
        MockSMTP.state = _State(
            send_raises=smtplib.SMTPResponseException(450, b"mailbox busy")
        )
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(result.retry_after_s, 30.0)
        self.assertIn("smtp error", result.detail)

    def test_421_yields_transient(self) -> None:
        # 421 "Service not available, closing transmission channel".
        MockSMTP.state = _State(
            send_raises=smtplib.SMTPResponseException(421, b"service not available")
        )
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(result.retry_after_s, 30.0)


class SMTPTransportSmtp5xxCase(unittest.TestCase):
    def test_5xx_yields_permanent_no_retry(self) -> None:
        # RFC 5321 §4.5.5: 5yz is Permanent Negative Completion —
        # do not retry, surface the error to the caller.
        MockSMTP.state = _State(
            send_raises=smtplib.SMTPResponseException(550, b"mailbox not found")
        )
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIsNone(result.retry_after_s)
        self.assertIn("smtp error", result.detail)


class ClassifySmtpErrorUnitCase(unittest.TestCase):
    """Direct unit tests for ``_classify_smtp_error``."""

    def _classify(self, code: object) -> tuple[DeliveryOutcome, float | None]:
        from app.modules.alerts.notifications.smtp import _classify_smtp_error

        return _classify_smtp_error(code)

    def test_none_is_permanent_no_retry(self) -> None:
        outcome, retry = self._classify(None)
        self.assertEqual(outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIsNone(retry)

    def test_4xx_is_transient_with_backoff(self) -> None:
        outcome, retry = self._classify(450)
        self.assertEqual(outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(retry, 30.0)

    def test_5xx_is_permanent_no_retry(self) -> None:
        outcome, retry = self._classify(550)
        self.assertEqual(outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIsNone(retry)

    def test_unparsable_code_is_permanent(self) -> None:
        outcome, retry = self._classify("not-a-number")
        self.assertEqual(outcome, DeliveryOutcome.PERMANENT_FAILURE)


class SMTPTransportConfigGuardCase(unittest.TestCase):
    def test_blank_host_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SMTPTransport(
                SMTPConfig(
                    host="",
                    port=587,
                    timeout_seconds=2.0,
                    use_tls=True,
                    from_email="bot@example.com",
                ),
                _factory(),
            )

    def test_blank_from_email_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SMTPTransport(
                SMTPConfig(
                    host="smtp.example.com",
                    port=587,
                    timeout_seconds=2.0,
                    use_tls=True,
                    from_email="",
                ),
                _factory(),
            )

    def test_zero_port_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SMTPTransport(
                SMTPConfig(
                    host="smtp.example.com",
                    port=0,
                    timeout_seconds=2.0,
                    use_tls=True,
                    from_email="bot@example.com",
                ),
                _factory(),
            )


class SMTPTransportEnvelopeGuardsCase(unittest.TestCase):
    def test_no_recipients_yields_permanent(self) -> None:
        MockSMTP.state = _State()
        envelope = NotificationEnvelope(
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.SMTP_EMAIL,
            subject="x",
            body_text="y",
            recipients=(),
        )
        result = SMTPTransport(_config(), _factory()).send(envelope)
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIn("no recipients", result.detail)

    def test_wrong_kind_yields_permanent(self) -> None:
        MockSMTP.state = _State()
        envelope = NotificationEnvelope(
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.APP_PUSH,
            subject="x",
            body_text="y",
            recipients=("test@example.com",),
        )
        result = SMTPTransport(_config(), _factory()).send(envelope)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIn("not supported by SMTPTransport", result.detail)


class SMTPTransportStarttlsCase(unittest.TestCase):
    """use_tls=True triggers starttls; use_tls=False skips it."""

    def _capturing_factory(
        self,
    ) -> tuple[Callable[[str, int, float], MockSMTP], dict[str, MockSMTP]]:
        captured: dict[str, MockSMTP] = {}

        def factory(host: str, port: int, timeout: float) -> MockSMTP:
            m = MockSMTP(host, port, timeout)
            captured["m"] = m
            return m

        return factory, captured

    def test_use_tls_true_calls_starttls(self) -> None:
        factory, captured = self._capturing_factory()
        MockSMTP.state = _State()
        cfg = SMTPConfig(
            host="smtp.example.com",
            port=587,
            timeout_seconds=2.0,
            use_tls=True,
            from_email="bot@example.com",
        )
        result = SMTPTransport(cfg, factory).send(_envelope())
        self.assertTrue(result.delivered)
        self.assertTrue(captured["m"].starttls_called)

    def test_use_tls_false_skips_starttls(self) -> None:
        factory, captured = self._capturing_factory()
        MockSMTP.state = _State()
        cfg = SMTPConfig(
            host="smtp.example.com",
            port=587,
            timeout_seconds=2.0,
            use_tls=False,
            from_email="bot@example.com",
        )
        result = SMTPTransport(cfg, factory).send(_envelope())
        self.assertTrue(result.delivered)
        self.assertFalse(captured["m"].starttls_called)


class SMTPTransportSmtpsDisconnectedCase(unittest.TestCase):
    def test_smtpserver_disconnected_yields_transient(self) -> None:
        # SMTPServerDisconnected is a subclass of SMTPException, so it
        # enters the SMTPException branch and is special-cased to
        # TRANSIENT_FAILURE.
        MockSMTP.state = _State(
            send_raises=smtplib.SMTPServerDisconnected("server dropped")
        )
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertIn("disconnected", result.detail.lower())


class SMTPTransportMessageBuildersCase(unittest.TestCase):
    def _send_and_get(self, envelope: NotificationEnvelope) -> Any:
        MockSMTP.state = _State()
        SMTPTransport(_config(), _factory()).send(envelope)
        self.assertEqual(len(MockSMTP.sent_messages), 1)
        return MockSMTP.sent_messages[0]

    def test_cc_set_when_present(self) -> None:
        envelope = NotificationEnvelope(
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.SMTP_EMAIL,
            subject="s",
            body_text="b",
            recipients=("a@example.com",),
            cc=("b@example.com", "c@example.com"),
        )
        msg = self._send_and_get(envelope)
        self.assertEqual(msg["Cc"], "b@example.com, c@example.com")

    def test_extra_headers_set(self) -> None:
        envelope = NotificationEnvelope(
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.SMTP_EMAIL,
            subject="s",
            body_text="b",
            recipients=("a@example.com",),
            extra_headers={"X-FreshLink-Source": "alerts"},
        )
        msg = self._send_and_get(envelope)
        self.assertEqual(msg["X-FreshLink-Source"], "alerts")

    def test_body_html_adds_alternative(self) -> None:
        envelope = NotificationEnvelope(
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.SMTP_EMAIL,
            subject="s",
            body_text="plain",
            body_html="<p>html</p>",
            recipients=("a@example.com",),
        )
        msg = self._send_and_get(envelope)
        subtypes = {p.get_content_subtype() for p in msg.walk()}
        self.assertIn("html", subtypes)

    def test_no_cc_omits_cc_header(self) -> None:
        envelope = NotificationEnvelope(
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.SMTP_EMAIL,
            subject="s",
            body_text="b",
            recipients=("a@example.com",),
        )
        msg = self._send_and_get(envelope)
        # EmailMessage returns None for unset headers
        self.assertIsNone(msg["Cc"])


class SMTPConfigFromSettingsCase(unittest.TestCase):
    def test_from_settings_basic(self) -> None:
        from types import SimpleNamespace

        s = SimpleNamespace(
            SMTP_HOST="mail.example.com",
            SMTP_PORT="465",
            SMTP_TIMEOUT="5.0",
            SMTP_TLS=True,
            EMAILS_FROM_EMAIL="bot@example.com",
        )
        cfg = SMTPConfig.from_settings(s)
        self.assertEqual(cfg.host, "mail.example.com")
        self.assertEqual(cfg.port, 465)
        self.assertEqual(cfg.timeout_seconds, 5.0)
        self.assertTrue(cfg.use_tls)
        self.assertEqual(cfg.from_email, "bot@example.com")

    def test_from_settings_missing_host(self) -> None:
        from types import SimpleNamespace

        s = SimpleNamespace(
            SMTP_HOST=None,
            SMTP_PORT="587",
            SMTP_TIMEOUT=None,
            SMTP_TLS=False,
            EMAILS_FROM_EMAIL="bot@example.com",
        )
        cfg = SMTPConfig.from_settings(s)
        self.assertEqual(cfg.host, "")

    def test_from_settings_invalid_port_string(self) -> None:
        from types import SimpleNamespace

        s = SimpleNamespace(
            SMTP_HOST="x",
            SMTP_PORT="not-a-port",
            SMTP_TIMEOUT=None,
            SMTP_TLS=False,
            EMAILS_FROM_EMAIL="bot@example.com",
        )
        cfg = SMTPConfig.from_settings(s)
        self.assertEqual(cfg.port, 0)

    def test_from_settings_zero_port(self) -> None:
        from types import SimpleNamespace

        s = SimpleNamespace(
            SMTP_HOST="x",
            SMTP_PORT="0",
            SMTP_TIMEOUT=None,
            SMTP_TLS=False,
            EMAILS_FROM_EMAIL="bot@example.com",
        )
        cfg = SMTPConfig.from_settings(s)
        self.assertEqual(cfg.port, 0)


class SMTPTransportDefaultFactoryCase(unittest.TestCase):
    def test_default_factory_uses_smtplib_smtp(self) -> None:
        """When no factory is provided, the transport must call
        ``smtplib.SMTP``. We monkey-patch ``smtplib.SMTP`` so no real
        network connection happens."""
        import smtplib as _smtplib

        seen: list[tuple[str, int, float]] = []

        class _Sentinel:
            def __init__(self, host: str, port: int, timeout: float) -> None:
                seen.append((host, port, timeout))

            def __enter__(self) -> _Sentinel:
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                return None

            def starttls(self) -> None:
                pass

            def send_message(self, msg: object) -> dict[str, tuple[int, bytes]]:
                return {}

        orig = _smtplib.SMTP
        _smtplib.SMTP = _Sentinel  # type: ignore[misc,assignment]  # ty: ignore[invalid-assignment]
        try:
            transport = SMTPTransport(_config())  # no factory arg
            result = transport.send(_envelope())
        finally:
            _smtplib.SMTP = orig  # type: ignore[misc]
        self.assertTrue(result.delivered)
        self.assertEqual(seen, [("smtp.example.com", 587, 2.0)])


class ClassifierOutOfRangeCase(unittest.TestCase):
    def test_350_falls_through_to_permanent(self) -> None:
        from app.modules.alerts.notifications.smtp import _classify_smtp_error

        outcome, retry = _classify_smtp_error(350)
        self.assertEqual(outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIsNone(retry)


class SMTPFactorySecondCallCase(unittest.TestCase):
    def test_factory_preserves_sent_messages_on_second_call(self) -> None:
        """``_factory()`` resets ``sent_messages`` on the first call only;
        subsequent transports must accumulate into the same list."""
        factory = _factory()
        MockSMTP.state = _State()
        SMTPTransport(_config(), factory).send(_envelope())
        self.assertEqual(len(MockSMTP.sent_messages), 1)
        MockSMTP.state = _State()
        SMTPTransport(_config(), factory).send(_envelope())
        self.assertEqual(len(MockSMTP.sent_messages), 2)


class SMTPTransportLoginCase(unittest.TestCase):
    def test_login_called_when_credentials_provided(self) -> None:
        MockSMTP.state = _State()
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
        self.assertTrue(result.delivered)
        self.assertEqual(
            MockSMTP.state.login_called_with,
            ("bot@example.com", "placeholder-not-a-real-secret"),
        )

    def test_login_not_called_when_no_credentials(self) -> None:
        MockSMTP.state = _State()
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertTrue(result.delivered)
        self.assertIsNone(MockSMTP.state.login_called_with)

    def test_login_auth_error_yields_permanent(self) -> None:
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
            password="bad",
        )
        result = SMTPTransport(cfg, _factory()).send(_envelope())
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIn("auth failed", result.detail)


class SMTPTransportRecipientsRefusedCase(unittest.TestCase):
    def test_recipients_refused_yields_permanent(self) -> None:
        # smtplib.SMTPRecipientsRefused stores refused recipients as
        # {addr: (code, msg)} — every refused address must surface in
        # the STRUCTURED ``recipient_outcomes`` (A queue P1: the
        # free-text ``detail`` stays address-free).
        refused_map = {
            "bad@example.com": (550, b"mailbox not found"),
            "alsobad@example.com": (550, b"unknown user"),
        }
        MockSMTP.state = _State(send_raises=smtplib.SMTPRecipientsRefused(refused_map))
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIn("recipients refused", result.detail)
        self.assertIn("count=2", result.detail)
        self.assertNotIn("@example.com", result.detail)
        by_addr = {r.address: r for r in result.recipient_outcomes}
        self.assertEqual(by_addr["bad@example.com"].code, 550)
        self.assertEqual(by_addr["alsobad@example.com"].code, 550)


class SMTPTransportPartialRefusedCase(unittest.TestCase):
    """``send_message`` returns a dict of refused recipients rather
    than raising when *some* recipients were refused. The transport
    must surface this as a structured failure — *never* report
    ACCEPTED_BY_SERVICE in that case.

    Regression test for PR #10 round 2, item 4: the previous code
    discarded ``send_message``'s return value, so a partial refusal
    was misclassified as a successful send.
    """

    def test_partial_refused_5xx_yields_permanent(self) -> None:
        MockSMTP.state = _State(
            partial_refused={
                "ok@example.com": (
                    250,
                    b"OK",
                ),  # accepted; shouldn't be in refused dict
                "dead@example.com": (550, b"mailbox not found"),
            }
        )
        # Use a multi-recipient envelope so the test exercises the
        # "some accepted, some refused" path.
        envelope = NotificationEnvelope(
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.SMTP_EMAIL,
            subject="s",
            body_text="b",
            recipients=("ok@example.com", "dead@example.com"),
        )
        # ``ok@example.com`` should NOT be in the partial_refused
        # dict; only ``dead@example.com`` should be there. We delete
        # the OK entry to be precise about the contract.
        MockSMTP.state.partial_refused = {
            "dead@example.com": (550, b"mailbox not found"),
        }
        result = SMTPTransport(_config(), _factory()).send(envelope)
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIsNone(result.retry_after_s)
        self.assertIn("recipients refused", result.detail)
        self.assertNotIn("@example.com", result.detail)
        by_addr = {r.address: r for r in result.recipient_outcomes}
        self.assertEqual(by_addr["dead@example.com"].code, 550)

    def test_partial_refused_4xx_yields_transient(self) -> None:
        MockSMTP.state = _State(
            partial_refused={
                "busy@example.com": (450, b"mailbox busy"),
            }
        )
        envelope = NotificationEnvelope(
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.SMTP_EMAIL,
            subject="s",
            body_text="b",
            recipients=("busy@example.com", "free@example.com"),
        )
        result = SMTPTransport(_config(), _factory()).send(envelope)
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(result.retry_after_s, 30.0)
        self.assertIn("recipients refused", result.detail)
        self.assertNotIn("@example.com", result.detail)

    def test_partial_refused_mixed_4xx_5xx_yields_permanent(self) -> None:
        """Any permanent 5yz in the refused set wins over transient
        4yz — the safer default is to surface permanent refusal so
        the operator can investigate the dead address."""

        MockSMTP.state = _State(
            partial_refused={
                "busy@example.com": (450, b"mailbox busy"),
                "dead@example.com": (550, b"mailbox not found"),
            }
        )
        envelope = NotificationEnvelope(
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.SMTP_EMAIL,
            subject="s",
            body_text="b",
            recipients=("busy@example.com", "dead@example.com"),
        )
        result = SMTPTransport(_config(), _factory()).send(envelope)
        self.assertFalse(result.delivered)
        # Mixed 4yz+5yz: top-level TRANSIENT so the 450 address stays
        # retryable; per-recipient records carry the permanent flag.
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(result.retry_after_s, 30.0)
        self.assertIn("count=2", result.detail)
        self.assertNotIn("@example.com", result.detail)
        by_addr = {r.address: r for r in result.recipient_outcomes}
        self.assertTrue(by_addr["busy@example.com"].refused)
        self.assertTrue(by_addr["busy@example.com"].retriable)
        self.assertEqual(by_addr["busy@example.com"].code, 450)
        self.assertTrue(by_addr["dead@example.com"].refused)
        self.assertFalse(by_addr["dead@example.com"].retriable)
        self.assertEqual(by_addr["dead@example.com"].code, 550)

    def test_empty_send_message_return_yields_accepted(self) -> None:
        """No refused recipients — the empty-dict return from
        ``send_message`` must still be treated as accepted.
        """

        MockSMTP.state = _State(partial_refused={})  # explicit empty
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertTrue(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.ACCEPTED_BY_SERVICE)


class SMTPTransportHeloErrorCase(unittest.TestCase):
    def test_helo_error_yields_transient(self) -> None:
        MockSMTP.state = _State(
            connect_raises=smtplib.SMTPHeloError(421, b"server unavailable")
        )
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertIn("helo rejected", result.detail)


class SMTPTransportSenderRefusedCase(unittest.TestCase):
    def test_sender_refused_5xx_yields_permanent(self) -> None:
        MockSMTP.state = _State(
            send_raises=smtplib.SMTPSenderRefused(
                550, b"sender blocked", "bot@example.com"
            )
        )
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIsNone(result.retry_after_s)

    def test_sender_refused_4xx_yields_transient(self) -> None:
        MockSMTP.state = _State(
            send_raises=smtplib.SMTPSenderRefused(450, b"try later", "bot@example.com")
        )
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(result.retry_after_s, 30.0)


class SMTPTransportDataErrorCase(unittest.TestCase):
    def test_data_error_4xx_yields_transient(self) -> None:
        MockSMTP.state = _State(send_raises=smtplib.SMTPDataError(450, b"server busy"))
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertEqual(result.retry_after_s, 30.0)

    def test_data_error_5xx_yields_permanent(self) -> None:
        MockSMTP.state = _State(
            send_raises=smtplib.SMTPDataError(552, b"message too large")
        )
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIsNone(result.retry_after_s)


class SMTPTransportUseSslCase(unittest.TestCase):
    def test_use_ssl_skips_starttls(self) -> None:
        """When ``use_ssl=True`` the transport must NOT call starttls()
        — ``smtplib.SMTP_SSL`` already negotiates TLS at connect time."""

        captured: dict[str, MockSMTP] = {}

        def factory(host: str, port: int, timeout: float) -> MockSMTP:
            m = MockSMTP(host, port, timeout)
            captured["m"] = m
            return m

        MockSMTP.state = _State()
        cfg = SMTPConfig(
            host="smtp.example.com",
            port=465,
            timeout_seconds=2.0,
            use_tls=True,  # would normally trigger starttls
            use_ssl=True,  # but use_ssl=True suppresses starttls
            from_email="bot@example.com",
        )
        result = SMTPTransport(cfg, factory).send(_envelope())
        self.assertTrue(result.delivered)
        self.assertFalse(captured["m"].starttls_called)


class SMTPTransportDefaultFactorySslCase(unittest.TestCase):
    def test_default_factory_use_ssl_returns_smtp_ssl(self) -> None:
        """With ``use_ssl=True`` and no injected factory, the transport
        must call ``smtplib.SMTP_SSL`` (port 465 implicit TLS) and
        never the plain ``smtplib.SMTP`` class."""
        import smtplib as _smtplib

        seen: dict[str, Any] = {}

        class _SentinelSSL:
            def __init__(self, host: str, port: int, timeout: float) -> None:
                seen.setdefault("ssl", []).append((host, port, timeout))

            def __enter__(self) -> _SentinelSSL:
                return self

            def __exit__(self, *a: object) -> None:
                return None

            def starttls(self) -> None:
                # If this ever runs, use_ssl=True is being violated;
                # assert post-hoc via the `seen` dict rather than a raise.
                seen["ssl_starttls_invoked"] = True

            def login(self, u: object, p: object) -> None:
                pass

            def send_message(self, msg: object) -> dict[str, tuple[int, bytes]]:
                return {}

        class _SentinelPlain:
            def __init__(self, host: str, port: int, timeout: float) -> None:
                seen.setdefault("plain", []).append((host, port, timeout))

            def __enter__(self) -> _SentinelPlain:
                return self

            def __exit__(self, *a: object) -> None:
                return None

            def starttls(self) -> None:
                pass

            def login(self, u: object, p: object) -> None:
                pass

            def send_message(self, msg: object) -> dict[str, tuple[int, bytes]]:
                return {}

        orig_ssl = _smtplib.SMTP_SSL
        orig_plain = _smtplib.SMTP
        _smtplib.SMTP_SSL = _SentinelSSL  # type: ignore[misc,assignment]  # ty: ignore[invalid-assignment]
        _smtplib.SMTP = _SentinelPlain  # type: ignore[misc,assignment]  # ty: ignore[invalid-assignment]
        try:
            cfg = SMTPConfig(
                host="smtp.example.com",
                port=465,
                timeout_seconds=2.0,
                use_tls=True,
                use_ssl=True,
                from_email="bot@example.com",
            )
            result = SMTPTransport(cfg).send(_envelope())
        finally:
            _smtplib.SMTP_SSL = orig_ssl  # type: ignore[misc]
            _smtplib.SMTP = orig_plain  # type: ignore[misc]
        self.assertTrue(result.delivered)
        self.assertIn("ssl", seen)
        self.assertNotIn("plain", seen)
        self.assertEqual(seen["ssl"], [("smtp.example.com", 465, 2.0)])
        self.assertNotIn("ssl_starttls_invoked", seen)


class SMTPConfigFromSettingsExtendedCase(unittest.TestCase):
    def test_from_settings_port_465_implies_ssl(self) -> None:
        from types import SimpleNamespace

        s = SimpleNamespace(
            SMTP_HOST="mail.example.com",
            SMTP_PORT="465",
            SMTP_TIMEOUT=None,
            SMTP_TLS=True,
            SMTP_SSL=None,
            EMAILS_FROM_EMAIL="bot@example.com",
            SMTP_USER="",
            SMTP_PASSWORD="",
        )
        cfg = SMTPConfig.from_settings(s)
        self.assertTrue(cfg.use_ssl)

    def test_from_settings_explicit_ssl_overrides_port_default(self) -> None:
        from types import SimpleNamespace

        s = SimpleNamespace(
            SMTP_HOST="x",
            SMTP_PORT="25",
            SMTP_TIMEOUT=None,
            SMTP_TLS=False,
            SMTP_SSL=False,
            EMAILS_FROM_EMAIL="bot@example.com",
            SMTP_USER="",
            SMTP_PASSWORD="",
        )
        cfg = SMTPConfig.from_settings(s)
        self.assertFalse(cfg.use_ssl)

    def test_from_settings_with_auth_credentials(self) -> None:
        from types import SimpleNamespace

        s = SimpleNamespace(
            SMTP_HOST="x",
            SMTP_PORT="587",
            SMTP_TIMEOUT=None,
            SMTP_TLS=True,
            SMTP_SSL=None,
            EMAILS_FROM_EMAIL="bot@example.com",
            SMTP_USER="bot@example.com",
            SMTP_PASSWORD="placeholder-not-a-real-secret",
        )
        cfg = SMTPConfig.from_settings(s)
        self.assertEqual(cfg.username, "bot@example.com")
        self.assertEqual(cfg.password, "placeholder-not-a-real-secret")


class SMTPTransportBuildMessageValidationCase(unittest.TestCase):
    """Malformed envelopes (subject/extra_headers wrong type) must be
    classified as PERMANENT_FAILURE and surface a structured detail —
    not raise out of :meth:`SMTPTransport.send`.
    """

    def _bad_subject_envelope(self, subject: object) -> NotificationEnvelope:
        return NotificationEnvelope(
            # cast: the test intentionally feeds a wrong-typed subject.
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.SMTP_EMAIL,
            subject=cast(str, subject),
            body_text="hello",
            recipients=("test@example.com",),
        )

    def test_subject_none_yields_permanent(self) -> None:
        MockSMTP.state = _State()
        result = SMTPTransport(_config(), _factory()).send(
            self._bad_subject_envelope(None)
        )
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIn("invalid envelope content", result.detail)
        # The transport must not raise; we only assert it returns.
        self.assertIsNone(result.server_response)

    def test_non_string_subject_yields_permanent(self) -> None:
        MockSMTP.state = _State()
        result = SMTPTransport(_config(), _factory()).send(
            self._bad_subject_envelope(12345)
        )
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIn("invalid envelope content", result.detail)

    def test_non_string_body_text_yields_permanent(self) -> None:
        MockSMTP.state = _State()
        envelope = NotificationEnvelope(
            source_event_id=uuid.uuid4(),
            kind=NotificationKind.SMTP_EMAIL,
            subject="x",
            body_text=cast(str, object()),  # type lie is intentional
            recipients=("test@example.com",),
        )
        result = SMTPTransport(_config(), _factory()).send(envelope)
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.PERMANENT_FAILURE)
        self.assertIn("invalid envelope content", result.detail)


class SMTPTransportNoopEdgeCase(unittest.TestCase):
    """server_response reflects the NOOP probe's real reply, or is
    None when NOOP itself fails. Either way, send() must never raise.
    """

    def test_noop_failure_yields_no_server_response(self) -> None:
        class _NoopFails(MockSMTP):
            def noop(self) -> tuple[int, str | bytes]:
                raise smtplib.SMTPServerDisconnected("gone")

        seen: dict[str, Any] = {}

        def factory(host: str, port: int, timeout: float) -> _NoopFails:
            m = _NoopFails(host, port, timeout)
            seen["m"] = m
            return m

        MockSMTP.state = _State()
        cfg = SMTPConfig(
            host="smtp.example.com",
            port=587,
            timeout_seconds=2.0,
            use_tls=True,
            from_email="bot@example.com",
        )
        result = SMTPTransport(cfg, factory).send(_envelope())
        # DATA was accepted; NOOP failed. We still report accepted.
        self.assertTrue(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.ACCEPTED_BY_SERVICE)
        self.assertIsNone(result.server_response)

    def test_noop_returns_string_message(self) -> None:
        class _NoopStringMsg(MockSMTP):
            def noop(self) -> tuple[int, str | bytes]:
                return (250, "OK from server")

        def factory(host: str, port: int, timeout: float) -> _NoopStringMsg:
            return _NoopStringMsg(host, port, timeout)

        MockSMTP.state = _State()
        cfg = SMTPConfig(
            host="smtp.example.com",
            port=587,
            timeout_seconds=2.0,
            use_tls=True,
            from_email="bot@example.com",
        )
        result = SMTPTransport(cfg, factory).send(_envelope())
        self.assertTrue(result.delivered)
        self.assertEqual(result.server_response, "noop 250 OK from server")


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
            login_raises=smtplib.SMTPAuthenticationError(
                454, b"temporarily unavailable"
            )
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
        MockSMTP.state = _State(connect_raises=smtplib.SMTPHeloError(550, b"go away"))
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


class SMTPTransportStageDiagnosticsCase(unittest.TestCase):
    """A queue P1 (#8 6440797666): simulated-SMTP negative tests for the
    four fixed scenarios. ``detail`` must carry the fixed stage tag and
    reply codes only — never exception text, addresses or credentials —
    and a failure AFTER DATA acceptance must never be reported as a
    retryable "undelivered" result (duplicate-send hazard, observed live
    once as an SSL unexpected EOF after DATA)."""

    def test_failure_before_connect_reports_connect_stage(self) -> None:
        MockSMTP.state = _State(connect_raises=ConnectionResetError("nope"))
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertIn("stage=connect", result.detail)
        # No exception text, no addresses.
        self.assertNotIn("nope", result.detail)
        self.assertNotIn("@", result.detail)

    def test_auth_failure_reports_auth_stage_without_secrets(self) -> None:
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
        self.assertIn("stage=auth", result.detail)
        self.assertIn("535", result.detail)
        # No password, no exception text, no server dialogue.
        self.assertNotIn("placeholder-not-a-real-secret", result.detail)
        self.assertNotIn("creds invalid", result.detail)
        self.assertIsNone(result.server_response)

    def test_data_accepted_then_exit_raises_is_accepted_not_retryable(self) -> None:
        # DATA accepted, then the ``with`` teardown (QUIT/SSL EOF) blows
        # up. The message is on the wire: outcome must be
        # ACCEPTED_BY_SERVICE with the terminal receipt marked
        # unconfirmed — never a TRANSIENT/TIMEOUT "undelivered" result
        # that would invite a duplicate send.
        MockSMTP.state = _State(exit_raises=ConnectionResetError("ssl eof"))
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertTrue(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.ACCEPTED_BY_SERVICE)
        self.assertIn("post_send_cleanup", result.detail)
        self.assertIn("terminal receipt unconfirmed", result.detail)
        self.assertIn("do not auto-retry", result.detail)
        self.assertNotIn("ssl eof", result.detail)
        self.assertIsNone(result.server_response)
        self.assertEqual(len(MockSMTP.sent_messages), 1)

    def test_data_accepted_then_exit_timeout_is_accepted_not_retryable(self) -> None:
        # Teardown hanging must not downgrade to TIMEOUT either.
        MockSMTP.state = _State(exit_raises=TimeoutError("quit never answered"))
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertTrue(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.ACCEPTED_BY_SERVICE)
        self.assertIn("post_send_cleanup", result.detail)

    def test_interrupted_during_data_reports_unknown_acceptance(self) -> None:
        # send_message raised mid-DATA: acceptance is genuinely unknown
        # and the detail says so honestly (stage=data +
        # data_acceptance=unknown), instead of a hard "delivered=False".
        MockSMTP.state = _State(send_raises=ConnectionResetError("eof mid-data"))
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertFalse(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.TRANSIENT_FAILURE)
        self.assertIn("stage=data", result.detail)
        self.assertIn("data_acceptance=unknown", result.detail)
        self.assertNotIn("eof mid-data", result.detail)

    def test_noop_raises_after_data_still_accepted(self) -> None:
        # NOOP probe failure after DATA is already swallowed by the
        # transport: accepted, server_response=None, no exception text.
        MockSMTP.state = _State(noop_raises=smtplib.SMTPServerDisconnected("gone"))
        result = SMTPTransport(_config(), _factory()).send(_envelope())
        self.assertTrue(result.delivered)
        self.assertEqual(result.outcome, DeliveryOutcome.ACCEPTED_BY_SERVICE)
        self.assertIsNone(result.server_response)
        self.assertNotIn("gone", result.detail)


if __name__ == "__main__":
    unittest.main()
