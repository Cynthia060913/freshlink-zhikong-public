"""APP push transport skeleton.

第二轮才会真正接 APNs / FCM。本轮只交付：

- :class:`AppPushTransport` 抽象基类（继承 :class:`NotificationTransport`
  之外，它会复用 :meth:`send` 边界做结果映射）；
- :class:`DisabledAppPushTransport` 默认实现：当 APP push 凭据没配置时，
  显式抛 :class:`NotConfigured` 并把结果标记为 :attr:`PERMANENT_FAILURE`，
  而**不**悄悄 no-op，确保系统不会因为缺少凭据就假称"已送达"。

要真正发出推送之前，请与赛事组委会确认第二渠道资质（v4 第 19 行 /
第 81 行），并在第二阶段提供：
- FCM ``server_key`` 或服务账号 JSON；
- APNs ``auth_key.p8`` + topic；
- 接收对象设备 token / topic 列表。
"""

from __future__ import annotations

import abc
import uuid

from .transport import (
    DeliveryOutcome,
    NotificationEnvelope,
    NotificationKind,
    NotificationResult,
    NotificationTransport,
)


class AppPushError(Exception):
    """Base class for APP push errors raised before structured mapping."""


class NotConfigured(AppPushError):
    """Operator has not provisioned APP push credentials yet."""


class TransientPushError(AppPushError):
    """Retriable network / rate-limit / transient upstream failure."""


class PermanentPushError(AppPushError):
    """Non-retriable failure (token revoked, payload rejected)."""


class AppPushTransport(NotificationTransport, abc.ABC):
    """Common skeleton for APNs/FCM adapters.

    Subclasses override :meth:`_deliver`. The :meth:`send` wrapper exists
    only to honour the shared result contract.
    """

    kind = NotificationKind.APP_PUSH

    @abc.abstractmethod
    def _deliver(self, envelope: NotificationEnvelope) -> None:
        """Implement the actual outbound call. Raise a subclass of
        :class:`AppPushError` on failure."""

    def send(self, envelope: NotificationEnvelope) -> NotificationResult:
        if envelope.kind is not NotificationKind.APP_PUSH:
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=DeliveryOutcome.PERMANENT_FAILURE,
                delivered=False,
                detail=(
                    f"envelope.kind={envelope.kind.value} not supported "
                    "by AppPushTransport"
                ),
            )
        try:
            self._deliver(envelope)
        except NotConfigured as exc:
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=DeliveryOutcome.PERMANENT_FAILURE,
                delivered=False,
                detail=f"app push not configured: {exc}",
            )
        except TransientPushError as exc:
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=DeliveryOutcome.TRANSIENT_FAILURE,
                delivered=False,
                detail=f"app push transient: {exc}",
                retry_after_s=30.0,
            )
        except PermanentPushError as exc:
            return NotificationResult(
                attempt_id=uuid.uuid4(),
                transport=self.kind,
                outcome=DeliveryOutcome.PERMANENT_FAILURE,
                delivered=False,
                detail=f"app push permanent: {exc}",
            )
        return NotificationResult(
            attempt_id=uuid.uuid4(),
            transport=self.kind,
            outcome=DeliveryOutcome.ACCEPTED_BY_SERVICE,
            delivered=True,
            detail="app push upstream accepted message",
        )


class DisabledAppPushTransport(AppPushTransport):
    """Default used when APP push credentials are not yet provisioned.

    Always reports :attr:`PERMANENT_FAILURE` (``not configured``) so the
    system cannot silently swallow delivery when only SMTP is wired up.
    """

    def _deliver(self, envelope: NotificationEnvelope) -> None:
        raise NotConfigured(
            "APP push credentials not provisioned; second channel pending"
        )
