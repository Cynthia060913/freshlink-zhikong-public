"""Alert notification dispatch layer (B3): envelopes, channels, retry.

把已交付的传输适配（``SMTPTransport`` / ``FeishuWebhookTransport``）
串成业务派发：

1. **Envelope 构建**（:func:`build_envelope`）：从
   :class:`~app.modules.alerts.models.event.AlertEvent` 构建消息；
   ``source_event_id``（业务幂等键，字符串）进入正文以便回溯，
   envelope 契约的 UUID 字段用告警行的内部 ``id``。
2. **渠道选择**（:func:`build_transports`）：只收集**已配置**的渠道
   （SMTP 由 settings、飞书由环境变量）；未配置的渠道静默跳过，
   由调用方通过 ``DispatchReport`` 看到实际发送了哪些渠道。
3. **有界重试**：仅对 ``transient_failure`` / ``timeout`` 重试，
   ``permanent_failure`` 与 ``accepted_by_service`` 终止；每次尝试
   （含失败）都追加一行 attempt 记录，重试间隔为指数退避与
   ``Retry-After`` 取大、并以 ``max_delay_s`` 封顶。
4. **Attempt 持久化**：默认写入调用方 session（同一事务）；构造时
   传 ``attempt_session_factory`` 则用独立 session 提交——即使主
   事务回滚，审计记录仍然保留。记录失败**绝不**中断派发，只在
   结果 detail 上如实标注。
5. **同源去重**：同一告警事件 + 同一渠道若已有 ``delivered`` 的
   attempt 记录，则跳过该渠道（INTERFACES"按 source_event_id 去重"），
   不重复打扰收件人。

边界如实声明：``delivered=True`` 仍是"服务已接受"，不证明终端实收；
本层不做终端回执采集（那是带外证据流程）。
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from collections.abc import (
    Callable,
    Mapping,
    Sequence,
)
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from app.modules.alerts.models.event import AlertEvent, NotificationAttempt

from .feishu import FeishuWebhookConfig, FeishuWebhookTransport
from .smtp import SMTPConfig, SMTPTransport
from .transport import (
    DeliveryOutcome,
    NotificationEnvelope,
    NotificationKind,
    NotificationResult,
    NotificationTransport,
)

_DISPATCH_TIMEZONE = ZoneInfo("Asia/Shanghai")
_MAX_DELAY_S = 30.0
_UNPERSISTED_SUFFIX = " [attempt record NOT persisted]"

_KIND_LABELS: dict[str, str] = {
    "temperature_high": "温度超上限",
    "temperature_low": "温度低于下限",
    "humidity_high": "湿度超上限",
    "humidity_low": "湿度低于下限",
    "data_quality": "数据质量异常",
    "temperature_recovered": "温度恢复（机器判定）",
    "offline": "设备离线",
    "fault": "设备故障",
    "recovered": "设备恢复",
}
_SEVERITY_LABELS: dict[str, str] = {
    "info": "提示",
    "warning": "警告",
    "critical": "严重",
}

_RETRYABLE_OUTCOMES = frozenset(
    {DeliveryOutcome.TRANSIENT_FAILURE, DeliveryOutcome.TIMEOUT}
)


def format_alert_time(moment: datetime) -> str:
    """Render a UTC moment in the operations timezone (Asia/Shanghai)."""

    local = moment.astimezone(_DISPATCH_TIMEZONE)
    return local.strftime("%Y-%m-%d %H:%M:%S") + "（Asia/Shanghai）"


def build_envelope(
    event: AlertEvent,
    kind: NotificationKind,
    *,
    recipients: Sequence[str] = (),
) -> NotificationEnvelope:
    """Build the message payload for one alert event and one channel.

    The envelope contract's ``source_event_id`` is a UUID, so it carries
    the alert row's internal ``id``; the business idempotency key (the
    string ``event.source_event_id``) rides in the body for traceability.
    """

    severity_label = _SEVERITY_LABELS.get(event.severity or "", "")
    kind_label = _KIND_LABELS.get(event.kind, event.kind)
    subject = (
        f"[FreshLink] {severity_label}告警：{kind_label}"
        if severity_label
        else f"[FreshLink] 告警：{kind_label}"
    )
    lines = [subject, ""]
    if event.device_id:
        lines.append(f"设备: {event.device_id}")
    if event.metric_code and event.value is not None:
        value_text = f"{event.metric_code} = {event.value}"
        if event.unit:
            value_text += f" {event.unit}"
        lines.append(f"指标: {value_text}")
    lines.append(f"采样时间: {format_alert_time(event.sampled_at)}")
    lines.append(f"来源事件: {event.source_event_id}")
    lines.append(f"告警记录: {event.id}")
    lines.append("机器判定结果，现场处置请以人工复核为准。")
    return NotificationEnvelope(
        source_event_id=event.id,
        kind=kind,
        subject=subject,
        body_text="\n".join(lines),
        recipients=tuple(recipients),
    )


@dataclasses.dataclass(frozen=True)
class DispatchReport:
    """What actually happened across channels for one alert event.

    ``results`` holds the LAST attempt's result per attempted channel
    (earlier attempts are in the attempt table); ``skipped_channels``
    lists channels skipped by the delivered-attempt dedup guard.
    """

    results: tuple[NotificationResult, ...] = ()
    skipped_channels: tuple[NotificationKind, ...] = ()


class AlertNotificationDispatcher:
    """Fan one alert event out to every configured channel with bounded
    retries and append-only attempt persistence."""

    def __init__(
        self,
        transports: Sequence[NotificationTransport],
        *,
        max_attempts: int = 3,
        base_delay_s: float = 1.0,
        max_delay_s: float = _MAX_DELAY_S,
        sleep: Callable[[float], None] = time.sleep,
        attempt_session_factory: Callable[[], AbstractContextManager[Session]]
        | None = None,
    ) -> None:
        kinds = [transport.kind for transport in transports]
        if len(set(kinds)) != len(kinds):
            raise ValueError("duplicate transport kind in dispatch set")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if base_delay_s < 0 or max_delay_s < 0:
            raise ValueError("delays must be non-negative")
        self._transports = tuple(transports)
        self._max_attempts = max_attempts
        self._base_delay_s = base_delay_s
        self._max_delay_s = max_delay_s
        self._sleep = sleep
        self._attempt_session_factory = attempt_session_factory

    def dispatch(
        self,
        session: Session,
        event: AlertEvent,
        *,
        recipients: Sequence[str] = (),
    ) -> DispatchReport:
        """Send to every configured channel, skipping already-accepted
        (event, channel) pairs. Never raises: transport ``send`` and
        attempt persistence failures are contained by contract."""

        try:
            accepted = self._accepted_channels(session, event.id)
        except Exception:
            # A failed dedup read leaves delivery status unknown. Sending now
            # could duplicate a notification that was already accepted.
            return DispatchReport(
                results=tuple(
                    self._failure_result(
                        transport.kind,
                        DeliveryOutcome.TRANSIENT_FAILURE,
                        "dedup_store_unavailable",
                    )
                    for transport in self._transports
                )
            )
        results: list[NotificationResult] = []
        skipped: list[NotificationKind] = []
        for transport in self._transports:
            if transport.kind in accepted:
                skipped.append(transport.kind)
                continue
            envelope = build_envelope(event, transport.kind, recipients=recipients)
            results.append(self._send_with_retry(session, event, transport, envelope))
        return DispatchReport(results=tuple(results), skipped_channels=tuple(skipped))

    # -- internals -------------------------------------------------------

    @staticmethod
    def _failure_result(
        kind: NotificationKind, outcome: DeliveryOutcome, detail: str
    ) -> NotificationResult:
        # Exception messages can contain addresses, webhook URLs or secrets.
        return NotificationResult(
            attempt_id=uuid.uuid4(),
            transport=kind,
            outcome=outcome,
            delivered=False,
            detail=detail,
        )

    @staticmethod
    def _accepted_channels(
        session: Session, alert_event_id: Any
    ) -> set[NotificationKind]:
        statement = (
            select(NotificationAttempt.channel)
            .where(NotificationAttempt.alert_event_id == alert_event_id)
            .where(col(NotificationAttempt.delivered).is_(True))
        )
        accepted: set[NotificationKind] = set()
        for channel in session.exec(statement):
            try:
                accepted.add(NotificationKind(channel))
            except ValueError:
                continue
        return accepted

    def _send_with_retry(
        self,
        session: Session,
        event: AlertEvent,
        transport: NotificationTransport,
        envelope: NotificationEnvelope,
    ) -> NotificationResult:
        result = self._send_once(transport, envelope)
        result = self._record_attempt(session, event, result)
        attempt_no = 1
        while result.outcome in _RETRYABLE_OUTCOMES and attempt_no < self._max_attempts:
            self._sleep(self._next_delay(attempt_no, result.retry_after_s))
            attempt_no += 1
            result = self._send_once(transport, envelope)
            result = self._record_attempt(session, event, result)
        return result

    def _send_once(
        self, transport: NotificationTransport, envelope: NotificationEnvelope
    ) -> NotificationResult:
        try:
            result = transport.send(envelope)
            if not isinstance(result, NotificationResult):
                return self._failure_result(
                    transport.kind,
                    DeliveryOutcome.TRANSIENT_FAILURE,
                    "invalid_transport_result",
                )
            return result
        except TimeoutError:
            return self._failure_result(
                transport.kind, DeliveryOutcome.TIMEOUT, "transport_timeout"
            )
        except Exception:
            return self._failure_result(
                transport.kind,
                DeliveryOutcome.TRANSIENT_FAILURE,
                "transport_unavailable",
            )

    def _next_delay(self, completed_attempt: int, retry_after_s: float | None) -> float:
        delay = self._base_delay_s * float(2 ** (completed_attempt - 1))
        if retry_after_s is not None:
            delay = max(delay, retry_after_s)
        return min(delay, self._max_delay_s)

    def _record_attempt(
        self, session: Session, event: AlertEvent, result: NotificationResult
    ) -> NotificationResult:
        row = NotificationAttempt(
            alert_event_id=event.id,
            channel=result.transport.value,
            outcome=result.outcome.value,
            delivered=result.delivered,
            detail=result.detail or None,
            server_response=result.server_response,
            retry_after_s=result.retry_after_s,
        )
        if self._attempt_session_factory is not None:
            try:
                with self._attempt_session_factory() as attempt_session:
                    attempt_session.add(row)
                    attempt_session.commit()
            except Exception:
                return dataclasses.replace(
                    result, detail=result.detail + _UNPERSISTED_SUFFIX
                )
            return result
        # Shared-session mode: the row joins the caller's transaction.
        # A failed flush poisons that transaction; rolling back is the
        # only honest recovery, and the caller learns about it via the
        # annotation below (dispatch must not raise).
        try:
            session.add(row)
            session.flush()
        except Exception:
            session.rollback()
            return dataclasses.replace(
                result, detail=result.detail + _UNPERSISTED_SUFFIX
            )
        return result


def build_transports(
    *,
    settings: Any | None = None,
    env: Mapping[str, str] | None = None,
) -> list[NotificationTransport]:
    """Collect every CONFIGURED channel; unconfigured ones are omitted.

    SMTP is configured when the settings carry a non-empty host AND
    from-email; Feishu when the environment carries a valid official
    webhook URL. Invalid configuration is skipped (never raised past
    this function) so a broken optional channel cannot take down the
    dispatch of the other one.
    """

    if settings is None:
        from app.core.config import settings as app_settings

        settings = app_settings
    transports: list[NotificationTransport] = []
    smtp_host: str = str(getattr(settings, "SMTP_HOST", "") or "")
    from_email: str = str(getattr(settings, "EMAILS_FROM_EMAIL", "") or "")
    if smtp_host and from_email:
        try:
            transports.append(SMTPTransport(SMTPConfig.from_settings(settings)))
        except ValueError:
            pass
    try:
        feishu_config = FeishuWebhookConfig.from_env(env)
        feishu_config.validate()
        transports.append(FeishuWebhookTransport(feishu_config))
    except ValueError:
        pass
    return transports
