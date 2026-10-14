"""Notification transport abstractions and shared types.

公共契约（v4 + 02-B-Agent指令.md）：

- ``send`` 边界不抛未捕获异常；所有失败映射为结构化
  :class:`NotificationResult`。
- :attr:`NotificationResult.delivered` 仅证明上游服务已接受，**不**证明
  终端实收。终端证据需要带外渠道（手机弹窗、APP 通知中心）单独记录。
- B agent 必须**分别**记录 "服务接受" 与 "终端实收" 两份证据；只在
  ``accepted_by_service`` 即宣称"两渠道完成"违背 v4 第 7 章交付状态。
"""

from __future__ import annotations

import abc
import enum
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime


class NotificationKind(enum.StrEnum):
    """Logical channel tag used by caller and persistence layer."""

    SMTP_EMAIL = "smtp_email"
    APP_PUSH = "app_push"


class DeliveryOutcome(enum.StrEnum):
    """Standard four-way classification required by the FreshLink v4
    notification contract (see 02-B-Agent指令.md, *Notification result
    contract*). Outcomes are pinned so persistence, retry, and audit
    layers can dispatch on them uniformly.

    - ``ACCEPTED_BY_SERVICE`` — upstream returned success
    - ``TRANSIENT_FAILURE``   — connection / rate-limit; retry allowed
    - ``PERMANENT_FAILURE``   — invalid input / non-retriable; do NOT retry
    - ``TIMEOUT``             — connect/send timed out; retry with backoff
    """

    ACCEPTED_BY_SERVICE = "accepted_by_service"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class NotificationEnvelope:
    """Minimum payload accepted by every transport.

    ``source_event_id`` is mandatory so persistence and audits can correlate
    against alerts / device events / risk candidates (see INTERFACES
    ``按 source_event_id 去重``).
    """

    source_event_id: uuid.UUID
    kind: NotificationKind
    subject: str
    body_text: str
    body_html: str | None = None
    recipients: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    issued_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class RecipientOutcome:
    """Per-recipient delivery record for one ``send`` attempt.

    Required by the PR #10 review (6110998037 / 6108497780): when a
    message is only partially accepted, retries must target the
    refused, retryable addresses only — never re-send to accepted
    recipients, and never drop a retryable address just because
    another address returned a permanent code.
    """

    address: str
    code: int | None  # SMTP reply code if the server gave one
    refused: bool
    retriable: bool  # 4yz refused → retryable; 5yz/unknown → not


@dataclass(frozen=True)
class NotificationResult:
    """Structured outcome of one ``send`` attempt."""

    attempt_id: uuid.UUID
    transport: NotificationKind
    outcome: DeliveryOutcome
    delivered: bool
    detail: str = ""
    server_response: str | None = None
    retry_after_s: float | None = None
    recipient_outcomes: tuple[RecipientOutcome, ...] = ()
    finished_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class NotificationTransport(abc.ABC):
    """Abstract transport. Concrete subclasses handle SMTP / APNs / FCM."""

    kind: NotificationKind

    @abc.abstractmethod
    def send(self, envelope: NotificationEnvelope) -> NotificationResult:
        """Return a :class:`NotificationResult`; never raise."""
