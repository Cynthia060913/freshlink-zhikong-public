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
3. **发送前持久意图（durability guard，A review 5682106843）**：每次
   网络外发**之前**，先在独立事务里持久化一行
   ``send_intent``（派发意图/预占）attempt 行；**该行写失败则本次
   零发送**（provider 调用为 0——保护都没落库，就没有外发资格）。
   发送返回后按**精确意图行 ID + 未完成状态条件**把该行单向完成
   （``send_intent`` → 最终结果，绝不改写任何已完成行）。完成写失败、
   结果不明（``acceptance_unknown``）或进程中断都会留下未完成的
   ``send_intent`` 行——它对所有进程可见，去重守卫与 pending 扫描把
   该渠道保持为待人工核验，后续运行**保守暂停、不自动补发**。
4. **有界重试**：仅对 ``transient_failure`` / ``timeout`` 重试，
   ``permanent_failure`` 与 ``accepted_by_service`` 终止；接受无法证实
   的中断（DATA 阶段连接断开，``acceptance_unknown``）**绝不**自动重试，
   同次派发与后续运行都把该渠道保持为待人工核验；重试间隔为指数退避与
   ``Retry-After`` 取大、并以 ``max_delay_s`` 封顶。每次尝试=一行
   ``send_intent`` 行被单向完成为最终结果，行数与真实外发次数一致。
5. **Attempt 持久化**：默认写入调用方 session，但插入在 SAVEPOINT
   内——记录失败只回滚该插入，**绝不**rollback 调用方的业务事务；
   构造时传 ``attempt_session_factory`` 则用独立 session 提交（推荐，
   供 :func:`dispatch_committed_event` 与 runner 使用）。跨进程持久的
   发送前保护只在独立 session（factory）模式下成立：共享 session 模式
   的意图行随调用方事务提交/回滚，不具跨进程耐久性，如实作为非发行
   形态记录。
5. **同源去重（best-effort，非并发恰好一次）**：同一告警事件 + 同一
   渠道若已有 ``delivered`` 的 attempt 记录、或已有
   ``acceptance_unknown``（接受未证实，待人工核验）的 attempt 记录，
   则跳过该渠道；去重查询本身失败时**保守 fail-closed：零发送**
   （``DispatchReport.dedup_error=True``），绝不因查重丢失而重复打扰
   收件人。
6. **渠道隔离**：单个渠道违反"send 不抛异常"契约（如读取响应时
   TimeoutError 逃逸）只影响该渠道——结构化记录后继续其余渠道；
   异常文本与 URL 不入库。
7. **只消费已提交事件**：用 :func:`dispatch_committed_event` 在业务
   事务提交之后由派发 worker 调用；网络与退避 sleep 不进入业务
   事务、告警行锁或 HTTP 请求内。

边界如实声明：``delivered=True`` 仍是"服务已接受"，不证明终端实收；
本层不做终端回执采集（那是带外证据流程）。
"""

from __future__ import annotations

import dataclasses
import time
import urllib.error
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

from sqlalchemy import or_
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

# Persistent audit marker for a send whose acceptance cannot be proven:
# a connection-class break mid-DATA means the message MAY have been
# accepted, so the attempt row records this fixed outcome value (never
# a retryable one) and both the dedup guard and the pending scan hold
# the channel for manual verification instead of blind-resending
# (C review 5678438821). The DeliveryOutcome four-value contract is
# unchanged; this value lives only in the attempt-audit column.
ACCEPTANCE_UNKNOWN_OUTCOME = "acceptance_unknown"

# Persistent pre-send dispatch intent ("预占", A review 5682106843):
# written in its own transaction BEFORE any network call; a send may
# only proceed once this row is durably committed. After the transport
# returns, the SAME row is completed forward — only by exact row id and
# only from the unfinished state — into the final outcome. Any crash,
# unknown result, or failed write-back leaves the row as send_intent:
# a cross-process hold that the dedup guard and pending scan treat as
# "held for manual verification", so later runs never auto-resend.
SEND_INTENT_OUTCOME = "send_intent"
_INTENT_DETAIL = "dispatch intent recorded before network send"
_INTENT_HOLD_SUFFIX = " [send_intent hold retained; manual verification required]"


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
    if event.related_source_event_id:
        # Dual-ID traceability (#5 6394581672): corrections and
        # recoveries name the original anomaly so the notification
        # shows both ids without rewriting any history.
        lines.append(f"关联事件: {event.related_source_event_id}")
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
    ``dedup_error=True`` means the dedup query itself failed: the
    dispatcher FAILED CLOSED (zero sends) because a dispatch without a
    trustworthy dedup read risks duplicate notifications.
    """

    results: tuple[NotificationResult, ...] = ()
    skipped_channels: tuple[NotificationKind, ...] = ()
    dedup_error: bool = False


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
            accepted = self._settled_channels(session, event.id)
        except Exception:
            # FAIL CLOSED (A review 6320881681): without a trustworthy
            # dedup read, sending risks duplicate notifications to
            # recipients. Zero sends, no raise; every configured channel
            # reports a fixed, redacted transient failure so the caller
            # sees per-channel outcomes, and dedup_error=True explains
            # why nothing was attempted.
            guarded = tuple(
                self._failure_result(
                    transport.kind,
                    DeliveryOutcome.TRANSIENT_FAILURE,
                    "dedup_store_unavailable",
                )
                for transport in self._transports
            )
            return DispatchReport(results=guarded, dedup_error=True)
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
        # Exception messages can embed addresses, webhook URLs or
        # secrets: results carry FIXED text only (C PR #56 66d5f98).
        return NotificationResult(
            attempt_id=uuid.uuid4(),
            transport=kind,
            outcome=outcome,
            delivered=False,
            detail=detail,
        )

    def _send_once(
        self, transport: NotificationTransport, envelope: NotificationEnvelope
    ) -> NotificationResult:
        """One guarded send: any leak past the never-raise contract
        becomes a structured, redacted outcome for THIS channel only
        (C PR #56 66d5f98 shape; TimeoutError stays its own outcome,
        connection-class failures are transient, other contract
        violations permanent so they are not retried blindly)."""
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
        except (urllib.error.URLError, OSError):  # fmt: skip
            return self._failure_result(
                transport.kind,
                DeliveryOutcome.TRANSIENT_FAILURE,
                "transport_unavailable",
            )
        except Exception as exc:
            detail = (
                "transport_contract_violation "
                f"({type(exc).__name__}); exception text intentionally not stored"
            )
            return self._failure_result(
                transport.kind, DeliveryOutcome.PERMANENT_FAILURE, detail
            )

    @staticmethod
    def _settled_channels(
        session: Session, alert_event_id: Any
    ) -> set[NotificationKind]:
        """Channels this event must NOT be auto-sent to again.

        A channel is settled when a delivered attempt exists (dedup
        guard), an ``acceptance_unknown`` attempt exists (unproven
        acceptance — never answered with an automatic re-send, in this
        dispatch or any later run, C review 5678438821), OR an
        unfinished ``send_intent`` row exists (the send started but its
        final state was never persisted — hold for manual verification,
        A review 5682106843).
        """
        statement = (
            select(NotificationAttempt.channel)
            .where(NotificationAttempt.alert_event_id == alert_event_id)
            .where(
                or_(
                    col(NotificationAttempt.delivered).is_(True),
                    col(NotificationAttempt.outcome) == ACCEPTANCE_UNKNOWN_OUTCOME,
                    col(NotificationAttempt.outcome) == SEND_INTENT_OUTCOME,
                )
            )
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
        attempt_no = 1
        while True:
            result = self._guarded_send(session, event, transport, envelope)
            # acceptance_unknown is NEVER retried, not even within the
            # bounded budget: an unproven acceptance plus a re-send is
            # the duplicate-notification scenario this layer exists to
            # prevent (C review 5678438821).
            if not (
                result.outcome in _RETRYABLE_OUTCOMES
                and not result.acceptance_unknown
                and attempt_no < self._max_attempts
            ):
                return result
            self._sleep(self._next_delay(attempt_no, result.retry_after_s))
            attempt_no += 1

    def _guarded_send(
        self,
        session: Session,
        event: AlertEvent,
        transport: NotificationTransport,
        envelope: NotificationEnvelope,
    ) -> NotificationResult:
        """One intent-guarded send: durable pre-send intent first —
        if that persistence fails, ZERO provider calls happen (A
        review 5682106843: no intent, no send)."""
        intent_id = self._persist_intent(session, event, transport)
        if intent_id is None:
            return self._failure_result(
                transport.kind,
                DeliveryOutcome.TRANSIENT_FAILURE,
                "dispatch_intent_not_persisted",
            )
        result = self._send_once(transport, envelope)
        return self._complete_intent(session, intent_id, result)

    def _next_delay(self, completed_attempt: int, retry_after_s: float | None) -> float:
        delay = self._base_delay_s * float(2 ** (completed_attempt - 1))
        if retry_after_s is not None:
            delay = max(delay, retry_after_s)
        return min(delay, self._max_delay_s)

    def _persist_intent(
        self, session: Session, event: AlertEvent, transport: NotificationTransport
    ) -> uuid.UUID | None:
        """Durably record the pre-send dispatch intent, or return None.

        Independent-session mode (the release path): the intent row is
        COMMITTED in its own transaction before any network call, so a
        crash after this point leaves a cross-process hold. Shared-
        session mode: the row joins the caller's transaction via a
        SAVEPOINT (best-effort, non-durable — documented boundary).

        A failure here must never poison the caller's business
        transaction; the caller answers None with zero provider calls.
        """
        intent_id = uuid.uuid4()
        row = NotificationAttempt(
            id=intent_id,
            alert_event_id=event.id,
            channel=transport.kind.value,
            outcome=SEND_INTENT_OUTCOME,
            delivered=False,
            detail=_INTENT_DETAIL,
        )
        if self._attempt_session_factory is not None:
            try:
                with self._attempt_session_factory() as attempt_session:
                    attempt_session.add(row)
                    attempt_session.commit()
            except Exception:
                return None
            return intent_id
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
        except Exception:
            return None
        return intent_id

    def _complete_intent(
        self, session: Session, intent_id: uuid.UUID, result: NotificationResult
    ) -> NotificationResult:
        """Complete the pre-send intent forward, one-way only.

        The update is guarded by exact row id AND the unfinished state
        (``outcome == send_intent`` and not delivered): completed rows
        are never rewritten (A review 5682106843). An acceptance-unknown
        result completes the row to the fixed ``acceptance_unknown``
        sentinel (never a retryable classification, C review
        5678438821; delivered stays False — unknown is never recorded
        as accepted). If the completion write itself fails, the row
        STAYS ``send_intent``: the channel is held for manual
        verification by every later dispatch and pending scan instead
        of being blindly resent.
        """
        final_outcome = (
            ACCEPTANCE_UNKNOWN_OUTCOME
            if result.acceptance_unknown
            else result.outcome.value
        )
        if self._attempt_session_factory is not None:
            try:
                with self._attempt_session_factory() as attempt_session:
                    row = attempt_session.get(NotificationAttempt, intent_id)
                    if (
                        row is None
                        or row.outcome != SEND_INTENT_OUTCOME
                        or row.delivered
                    ):
                        # Never rewrite a completed (or unknown-state) row.
                        return result
                    row.outcome = final_outcome
                    row.delivered = result.delivered
                    row.detail = result.detail or None
                    row.server_response = result.server_response
                    row.retry_after_s = result.retry_after_s
                    attempt_session.commit()
            except Exception:
                return dataclasses.replace(
                    result, detail=result.detail + _INTENT_HOLD_SUFFIX
                )
            return result
        try:
            with session.begin_nested():
                row = session.get(NotificationAttempt, intent_id)
                if (
                    row is not None
                    and row.outcome == SEND_INTENT_OUTCOME
                    and not row.delivered
                ):
                    row.outcome = final_outcome
                    row.delivered = result.delivered
                    row.detail = result.detail or None
                    row.server_response = result.server_response
                    row.retry_after_s = result.retry_after_s
                session.flush()
        except Exception:
            return dataclasses.replace(
                result, detail=result.detail + _INTENT_HOLD_SUFFIX
            )
        return result


def dispatch_committed_event(
    session_factory: Callable[[], AbstractContextManager[Session]],
    alert_event_id: uuid.UUID,
    *,
    transports: Sequence[NotificationTransport] | None = None,
    recipients: Sequence[str] = (),
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = _MAX_DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
) -> DispatchReport:
    """Minimal dispatch entry point for an ALREADY-COMMITTED alert event.

    Call this AFTER the alert-ingestion transaction has committed (from
    the ingestion worker's post-commit step), never inside a business
    transaction, an HTTP request handler waiting on the response, or
    while holding alert row locks: this function performs bounded
    network I/O and retry sleeps.

    It opens its own read session via ``session_factory`` and records
    attempt rows through INDEPENDENT sessions, so an audit failure can
    never roll back business work. The dedup guard here is an ordering
    guard, NOT concurrent exactly-once: run a single dispatch worker
    per deployment to keep the mutual-exclusion boundary clear.
    """

    if transports is None:
        transports = build_transports()
    dispatcher = AlertNotificationDispatcher(
        transports,
        max_attempts=max_attempts,
        base_delay_s=base_delay_s,
        max_delay_s=max_delay_s,
        sleep=sleep,
        attempt_session_factory=session_factory,
    )
    with session_factory() as session:
        event = session.get(AlertEvent, alert_event_id)
        if event is None:
            return DispatchReport()
        return dispatcher.dispatch(session, event, recipients=recipients)


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
