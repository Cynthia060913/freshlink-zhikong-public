"""Notification transports for the alerts module.

本轮（B 第一轮）只交付抽象与两个最小实现：

- :class:`NotificationTransport` 抽象基类（见 ``transport``）
- :class:`SMTPTransport` 真实 SMTP 客户端（见 ``smtp``）
- :class:`AppPushTransport` 抽象 + :class:`DisabledAppPushTransport`
  默认实现（见 ``app_push``）

设计原则（来自 02-B-Agent指令.md 第 17 行 + INTERFACES 第 18 行）：

1. ``send(envelope) -> NotificationResult`` 不抛未捕获异常；所有失败都
   通过结构化 ``NotificationResult`` 返回。
2. ``delivered=True`` 仅表示上游服务接受（"服务接受"），不证明终端实收。
   终端实收证据必须经带外渠道记录。
3. 适配器密钥/token 全部从运行时配置（``SMTPConfig`` / 未来
   ``AppPushConfig``）传入；本仓库不出现任何凭据。

阶段二接入真实 SMTP / APP 推送前，请确认：
- SMTP 收件人已获授权；
- 凭据仅在 :file:`.env.freshlink`（本机私有）配置，不入 git；
- APP 推送资格已与赛事组委会确认或保留为"候选"标签。
"""

from __future__ import annotations

from .app_push import (
    AppPushTransport,
    DisabledAppPushTransport,
    NotConfigured,
    PermanentPushError,
    TransientPushError,
)
from .dispatch import (
    AlertNotificationDispatcher,
    DispatchReport,
    build_envelope,
    build_transports,
    dispatch_committed_event,
    format_alert_time,
)
from .feishu import FeishuWebhookConfig, FeishuWebhookTransport
from .smtp import SMTPConfig, SMTPTransport
from .transport import (
    DeliveryOutcome,
    NotificationEnvelope,
    NotificationKind,
    NotificationResult,
    NotificationTransport,
)

__all__ = [
    "AlertNotificationDispatcher",
    "AppPushTransport",
    "DeliveryOutcome",
    "DisabledAppPushTransport",
    "DispatchReport",
    "FeishuWebhookConfig",
    "FeishuWebhookTransport",
    "NotConfigured",
    "NotificationEnvelope",
    "NotificationKind",
    "NotificationResult",
    "NotificationTransport",
    "PermanentPushError",
    "SMTPConfig",
    "SMTPTransport",
    "TransientPushError",
    "build_envelope",
    "build_transports",
    "dispatch_committed_event",
    "format_alert_time",
]
