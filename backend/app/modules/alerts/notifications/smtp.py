"""SMTP transport.

The transport accepts an :class:`SMTPConfig` and an optional
``smtp_factory`` so unit tests can inject a deterministic stand-in
``smtplib.SMTP``-shaped object (see ``tests/test_smtp.py::MockSMTP``).

Credentials are intentionally not stored on the transport instance
outside of the config. The real values come from
:class:`app.core.config.Settings` and are resolved by the caller.
This module never embeds any real password, token, or host. Anything
committed here must remain secret-free.
"""

from __future__ import annotations

import smtplib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any, Protocol

from .transport import (
    DeliveryOutcome,
    NotificationEnvelope,
    NotificationKind,
    NotificationResult,
    NotificationTransport,
    RecipientOutcome,
)


class _SupportsContextManager(Protocol):
    """Documented shape of the duck-typed SMTP client.

    Not used for static checking: real ``smtplib.SMTP`` and the test
    double cannot both satisfy a structural protocol (typeshed's
    ``login`` carries extra keyword-only parameters). The factory is
    therefore typed ``Callable[..., Any]`` and the client is treated
    as duck-typed at runtime; the contract is enforced by tests.
    """

    def __enter__(self) -> Any: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> object: ...
    def starttls(self, keyfile: object = None, certfile: object = None) -> object: ...
    def login(self, user: object, password: object) -> object: ...
    def send_message(self, message: EmailMessage) -> Mapping[str, tuple[Any, Any]]: ...
    def noop(self) -> tuple[Any, Any]: ...


SMTPFactory = Callable[[str, int, float], Any]


@dataclass(frozen=True)
class SMTPConfig:
    """SMTP server configuration.

    ``use_ssl=True`` selects implicit TLS (typically port 465) and uses
    ``smtplib.SMTP_SSL``. ``use_tls=True`` (without ``use_ssl``) means
    STARTTLS on a plain ``smtplib.SMTP`` connection (typically port 587).
    ``username`` / ``password`` are optional; if both are non-empty the
    transport calls ``client.login(...)`` before sending.
    """

    host: str
    port: int
    timeout_seconds: float
    use_tls: bool
    from_email: str
    use_ssl: bool = False
    username: str = ""
    password: str = ""

    @classmethod
    def from_settings(cls, settings: Any) -> SMTPConfig:
        """Construct from :class:`app.core.config.Settings`.

        Falls back to safe defaults when SMTP fields are missing, but
        raises :class:`ValueError` if the resolved host/port is invalid.
        Note: real host/port/credentials must come from configured env
        vars, never from any hard-coded value.
        """

        host = settings.SMTP_HOST or ""
        try:
            port = int(settings.SMTP_PORT or 0)
        except (TypeError, ValueError):  # fmt: skip
            port = 0
        # Implicit TLS by default on port 465 unless caller overrides.
        explicit_ssl = getattr(settings, "SMTP_SSL", None)
        use_ssl = bool(explicit_ssl) if explicit_ssl is not None else port == 465
        return cls(
            host=host,
            port=port,
            timeout_seconds=float(getattr(settings, "SMTP_TIMEOUT", 10) or 10),
            use_tls=bool(settings.SMTP_TLS),
            use_ssl=use_ssl,
            from_email=getattr(settings, "EMAILS_FROM_EMAIL", "") or "",
            username=getattr(settings, "SMTP_USER", "") or "",
            password=getattr(settings, "SMTP_PASSWORD", "") or "",
        )


class SMTPTransport(NotificationTransport):
    kind = NotificationKind.SMTP_EMAIL

    def __init__(
        self,
        config: SMTPConfig,
        smtp_factory: SMTPFactory | None = None,
    ) -> None:
        if not config.host:
            raise ValueError("SMTP host must be configured")
        if config.port <= 0:
            raise ValueError("SMTP port must be a positive integer")
        if not config.from_email:
            raise ValueError("SMTP from_email must be configured")
        self._config = config
        self._smtp_factory = smtp_factory or self._make_default_factory()

    def _make_default_factory(self) -> SMTPFactory:
        use_ssl = self._config.use_ssl

        def f(host: str, port: int, timeout: float) -> smtplib.SMTP:
            if use_ssl:
                # Implicit TLS (typical for port 465).
                return smtplib.SMTP_SSL(host, port, timeout=timeout)
            return smtplib.SMTP(host=host, port=port, timeout=timeout)

        return f

    def send(self, envelope: NotificationEnvelope) -> NotificationResult:
        if envelope.kind is not NotificationKind.SMTP_EMAIL:
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=DeliveryOutcome.PERMANENT_FAILURE,
                delivered=False,
                detail=(
                    f"envelope.kind={envelope.kind.value} not supported "
                    "by SMTPTransport"
                ),
            )
        if not envelope.recipients:
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=DeliveryOutcome.PERMANENT_FAILURE,
                delivered=False,
                detail="no recipients in envelope",
            )
        if not isinstance(envelope.subject, str):
            # EmailMessage silently coerces non-str subject headers
            # (e.g. None → "None"), so we reject at the transport
            # boundary with a deterministic PERMANENT_FAILURE rather
            # than risk sending a malformed message.
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=DeliveryOutcome.PERMANENT_FAILURE,
                delivered=False,
                detail=(
                    "invalid envelope content: subject must be str, "
                    f"got {type(envelope.subject).__name__}"
                ),
            )
        if not isinstance(envelope.body_text, str):
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=DeliveryOutcome.PERMANENT_FAILURE,
                delivered=False,
                detail=(
                    "invalid envelope content: body_text must be str, "
                    f"got {type(envelope.body_text).__name__}"
                ),
            )

        try:
            message = _build_message(envelope, self._config.from_email)
        except (TypeError, ValueError, AttributeError, KeyError) as exc:
            # Defensive fallback for any other shape EmailMessage may
            # reject in the future (extra_headers with non-str keys,
            # body set-content edge cases, etc.).
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=DeliveryOutcome.PERMANENT_FAILURE,
                delivered=False,
                detail=(f"invalid envelope content: {type(exc).__name__}: {exc}"),
            )

        noop_code: int | None = None
        noop_msg: object = None
        partial_refused: dict[str, int] = {}

        try:
            with self._smtp_factory(
                self._config.host,
                self._config.port,
                self._config.timeout_seconds,
            ) as client:
                # STARTTLS is only meaningful on a plain SMTP connection.
                if self._config.use_tls and not self._config.use_ssl:
                    client.starttls()
                if self._config.username:
                    client.login(self._config.username, self._config.password)
                # ``send_message`` returns a mapping of refused
                # recipients ``{addr: (code, msg)}``. An empty mapping
                # means every recipient was accepted; a non-empty
                # mapping means *some* recipients were refused
                # (smtplib raises SMTPRecipientsRefused only when
                # *every* recipient was refused).
                refused = client.send_message(message)
                # Normalise to {addr: code}; the message text is not
                # needed for classification or the audit detail.
                if refused:
                    partial_refused = {
                        addr: int(entry[0]) for addr, entry in refused.items()
                    }
                # Capture the post-send server reply with NOOP so the
                # result surfaces the *actual* server-reported code and
                # message, not a hard-coded "250" string.
                try:
                    noop_code, noop_msg = client.noop()
                except Exception:  # noqa: BLE001
                    # NOOP probe failed (server went away after DATA);
                    # data was already accepted on the wire, so we still
                    # report ACCEPTED_BY_SERVICE but server_response is
                    # None because we have no honest server text to cite.
                    noop_code, noop_msg = None, None
        except smtplib.SMTPRecipientsRefused as exc:
            # Every recipient address was refused. Classify by the
            # actual reply codes (A replay 6108497780: all-450 must be
            # transient, not a blanket permanent).
            refused = _normalise_refused(exc.recipients)
            return self._refusal_result(
                envelope,
                refused,
                accepted=(),
                server_response=str(exc),
            )
        except TimeoutError as exc:
            # Python 3.10+ TimeoutError is OSError subclass — must be
            # before the generic OSError catch.
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=DeliveryOutcome.TIMEOUT,
                delivered=False,
                detail=f"timeout: {exc}",
                retry_after_s=self._config.timeout_seconds,
            )
        except smtplib.SMTPServerDisconnected as exc:
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=DeliveryOutcome.TRANSIENT_FAILURE,
                delivered=False,
                detail=f"server disconnected: {exc}",
            )
        except smtplib.SMTPAuthenticationError as exc:
            # Classify by the actual reply code: 535 → permanent, but
            # 454 ("temporarily unable to authenticate") is 4yz →
            # transient with backoff (A replay 6110998037).
            smtp_code = getattr(exc, "smtp_code", None)
            outcome, retry_after_s = _classify_smtp_error(smtp_code)
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=outcome,
                delivered=False,
                detail=f"auth failed code={smtp_code}: {exc}",
                server_response=str(exc),
                retry_after_s=retry_after_s,
            )
        except smtplib.SMTPHeloError as exc:
            # Classify by the actual reply code: 421 → transient,
            # 550 → permanent (A replay 6110998037).
            smtp_code = getattr(exc, "smtp_code", None)
            outcome, retry_after_s = _classify_smtp_error(smtp_code)
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=outcome,
                delivered=False,
                detail=f"helo rejected code={smtp_code}: {exc}",
                retry_after_s=retry_after_s,
            )
        except smtplib.SMTPSenderRefused as exc:
            # MAIL FROM rejected — classify by reply code.
            smtp_code = getattr(exc, "smtp_code", None)
            outcome, retry_after_s = _classify_smtp_error(smtp_code)
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=outcome,
                delivered=False,
                detail=f"sender refused code={smtp_code}",
                server_response=str(exc),
                retry_after_s=retry_after_s,
            )
        except smtplib.SMTPDataError as exc:
            # DATA phase rejected — classify by reply code.
            smtp_code = getattr(exc, "smtp_code", None)
            outcome, retry_after_s = _classify_smtp_error(smtp_code)
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=outcome,
                delivered=False,
                detail=f"data rejected code={smtp_code}",
                server_response=str(exc),
                retry_after_s=retry_after_s,
            )
        except smtplib.SMTPException as exc:
            # Catch-all for SMTP-layer exceptions
            # (e.g. SMTPNotSupportedError, SMTPConnectTimeoutError).
            smtp_code = getattr(exc, "smtp_code", None)
            smtp_error = getattr(exc, "smtp_error", None)
            outcome, retry_after_s = _classify_smtp_error(smtp_code)
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=outcome,
                delivered=False,
                detail=(f"smtp error code={smtp_code} error={smtp_error!r}"),
                server_response=str(exc),
                retry_after_s=retry_after_s,
            )
        except (ConnectionRefusedError, ConnectionResetError, OSError) as exc:
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=DeliveryOutcome.TRANSIENT_FAILURE,
                delivered=False,
                detail=f"connection error: {exc}",
                server_response=None,
            )

        if partial_refused:
            # Some recipients were refused but not all. ``send_message``
            # would have raised ``SMTPRecipientsRefused`` if every
            # recipient was refused; we caught that earlier. Here we
            # handle the *partial* refusal case — the data was accepted
            # by the server but one or more addresses were rejected.
            accepted = tuple(
                a
                for a in (*envelope.recipients, *envelope.cc)
                if a not in partial_refused
            )
            return self._refusal_result(
                envelope,
                partial_refused,
                accepted=accepted,
                server_response=None,
            )

        if noop_code is None:
            server_response: str | None = None
        else:
            if isinstance(noop_msg, bytes):
                msg_text = noop_msg.decode("utf-8", errors="replace")
            elif noop_msg is None:
                msg_text = ""
            else:
                msg_text = str(noop_msg)
            # A's replay (6108497780): the NOOP reply proves only that
            # NOOP succeeded — it is NOT a DATA delivery receipt, so it
            # is labelled explicitly with its phase.
            server_response = f"noop {noop_code} {msg_text}".strip()

        return NotificationResult(
            attempt_id=uuid.uuid4(),
            transport=self.kind,
            outcome=DeliveryOutcome.ACCEPTED_BY_SERVICE,
            delivered=True,
            detail=(
                "smtp server accepted message; server_response is the "
                "NOOP reply (response_phase=noop), not a DATA receipt"
            ),
            server_response=server_response,
            finished_at=datetime.now(UTC),
        )

    def _refusal_result(
        self,
        envelope: NotificationEnvelope,
        refused: Mapping[str, int | None],
        *,
        accepted: tuple[str, ...],
        server_response: str | None,
    ) -> NotificationResult:
        """Build the structured result for a partial/full refusal.

        Classification (per A's replays 6108497780 + 6110998037):

        - any 4yz refused → TRANSIENT_FAILURE with 30s backoff, so the
          retry layer can re-send to *only* the retryable addresses;
        - otherwise (all 5yz / unknown codes) → PERMANENT_FAILURE.

        ``recipient_outcomes`` records every recipient individually so
        the caller never re-sends to accepted addresses and never
        drops a retryable refused address because another address
        returned a permanent code.
        """

        retriable_codes = [
            c for c in refused.values() if c is not None and 400 <= c < 500
        ]
        outcome = (
            DeliveryOutcome.TRANSIENT_FAILURE
            if retriable_codes
            else DeliveryOutcome.PERMANENT_FAILURE
        )
        retry_after_s: float | None = 30.0 if retriable_codes else None

        outcomes: list[RecipientOutcome] = [
            RecipientOutcome(
                address=addr,
                code=code,
                refused=True,
                retriable=bool(code is not None and 400 <= code < 500),
            )
            for addr, code in sorted(refused.items())
        ]
        outcomes.extend(
            RecipientOutcome(address=addr, code=None, refused=False, retriable=False)
            for addr in accepted
        )
        codes = sorted({c for c in refused.values() if c is not None})
        refused_addrs = sorted(refused)
        detail = (
            f"recipients refused codes={codes} addresses={refused_addrs}; "
            "retry refused-retryable addresses only, never re-send accepted"
        )
        return NotificationResult(
            attempt_id=uuid.uuid4(),
            transport=self.kind,
            outcome=outcome,
            # ``delivered`` is False when *any* recipient was
            # refused; the audit trail cannot claim "every recipient
            # got the message" if even one was bounced.
            delivered=False,
            detail=detail,
            server_response=server_response,
            retry_after_s=retry_after_s,
            recipient_outcomes=tuple(outcomes),
            finished_at=datetime.now(UTC),
        )


def _build_message(envelope: NotificationEnvelope, from_email: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = envelope.subject
    message["From"] = from_email
    message["To"] = ", ".join(envelope.recipients)
    if envelope.cc:
        message["Cc"] = ", ".join(envelope.cc)
    for k, v in envelope.extra_headers.items():
        message[k] = v
    message.set_content(envelope.body_text)
    if envelope.body_html:
        message.add_alternative(envelope.body_html, subtype="html")
    return message


def _normalise_refused(raw: object) -> dict[str, int | None]:
    """Normalise an smtplib refusal mapping to ``{addr: code}``.

    ``raw`` is ``{addr: (code, msg)}`` from either
    ``SMTPRecipientsRefused.recipients`` or the partial-refusal dict
    returned by ``send_message``. Unparseable codes are kept as
    ``None`` (classified permanent, not retriable).
    """

    refused: dict[str, int | None] = {}
    if isinstance(raw, Mapping):
        for addr, entry in raw.items():
            code: object = None
            if isinstance(entry, (tuple, list)) and entry:
                code = entry[0]
            if not isinstance(code, (int, str)) or isinstance(code, bool):
                refused[str(addr)] = None
                continue
            try:
                refused[str(addr)] = int(code)
            except ValueError:
                refused[str(addr)] = None
    return refused


def _classify_smtp_error(smtp_code: object) -> tuple[DeliveryOutcome, float | None]:
    """Classify SMTP reply codes per RFC 5321 §4.5.5.

    - 4yz Transient Negative Completion → TRANSIENT_FAILURE (retry after 30s).
      Examples: 421 service not available, 450 mailbox busy, 451 aborted.
    - 5yz Permanent Negative Completion → PERMANENT_FAILURE (no retry).
      Examples: 550 mailbox not found, 552 message too large, 553 bad sequence.
    - Unknown / unparseable code → PERMANENT_FAILURE (do not silently retry).
    """

    try:
        code_int = int(smtp_code) if isinstance(smtp_code, (int, str)) else None
    except (TypeError, ValueError):  # fmt: skip
        code_int = None
    if code_int is None:
        return DeliveryOutcome.PERMANENT_FAILURE, None
    if 400 <= code_int < 500:
        return DeliveryOutcome.TRANSIENT_FAILURE, 30.0
    if 500 <= code_int < 600:
        return DeliveryOutcome.PERMANENT_FAILURE, None
    return DeliveryOutcome.PERMANENT_FAILURE, None
