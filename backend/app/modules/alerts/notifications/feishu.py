"""Feishu custom-group-bot webhook transport (the second channel).

队长 2026-10-20 决议（#8 评论 6293916011）：第二渠道固定为
**飞书手机 App 自定义群机器人**（材料写“集成飞书 App 推送”，绝不称
自研 App / 原生 SDK）。本模块只实现传输适配；真实发送须由本人私下
配置 webhook、明确授权收件范围后才允许。

安全与协议约束（同评论，逐条落实）：

- 仅允许 **HTTPS + 固定官方 host/路径**（``open.feishu.cn`` +
  ``/open-apis/bot/v2/hook/``），拒绝任何查询串/片段；不跟随重定向；
  不提供关闭证书校验的选项。
- 签名按官方自定义机器人协议：``string_to_sign = "{timestamp}\\n{secret}"``，
  HMAC-SHA256 以该串为 key、空消息，再 base64。密钥只存在于运行时
  配置，**绝不**进入 Git / 日志 / 异常正文 / 公共数据库。
- ``HTTP 200 不是自动成功``：必须核对平台业务码（``code == 0``）。
  非零业务码为永久失败；429/5xx/网络错误为可重试；超时单独分类。
  超时不声称远端绝未收到；本结果只证明“服务接受”，不证明终端实收。

配置来自环境变量（``FEISHU_WEBHOOK_URL`` / ``FEISHU_WEBHOOK_SECRET``），
不新增共享根 ``app/core/config.py`` 字段——避免与 A 的共享文件接线
冲突；派发层负责判断“未配置”。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .transport import (
    DeliveryOutcome,
    NotificationEnvelope,
    NotificationKind,
    NotificationResult,
    NotificationTransport,
)

_ALLOWED_HOST = "open.feishu.cn"
_ALLOWED_PATH_PREFIX = "/open-apis/bot/v2/hook/"
_DEFAULT_TIMEOUT_S = 5.0
_MAX_RESPONSE_BYTES = 8192
_MAX_TIMEOUT_S = 30.0

# Injectable HTTP seam (mirrors SMTPTransport's smtp_factory): tests
# pass a deterministic ``open_fn``; production uses a no-redirect
# opener built once in __init__.
OpenFn = Callable[[urllib.request.Request, float], Any]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: the A-decided constraint is a FIXED host/path."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


@dataclass(frozen=True)
class FeishuWebhookConfig:
    """Runtime configuration for one custom group bot.

    ``webhook_url`` is the FULL official hook URL and ``secret`` the
    optional signing secret — both are credentials: never logged, never
    committed, never echoed in errors (validation messages are static
    strings on purpose).
    """

    webhook_url: str
    secret: str = ""
    timeout_seconds: float = _DEFAULT_TIMEOUT_S

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> FeishuWebhookConfig:
        source = os.environ if env is None else env
        timeout_raw = source.get("FEISHU_WEBHOOK_TIMEOUT_S", "")
        return cls(
            webhook_url=source.get("FEISHU_WEBHOOK_URL", ""),
            secret=source.get("FEISHU_WEBHOOK_SECRET", ""),
            timeout_seconds=_parse_timeout(timeout_raw),
        )

    def validate(self) -> None:
        try:
            parsed = urllib.parse.urlsplit(self.webhook_url)
        except ValueError:
            raise ValueError("invalid feishu webhook URL") from None
        if parsed.scheme != "https":
            raise ValueError("feishu webhook must use https")
        if parsed.netloc.lower() != _ALLOWED_HOST:
            raise ValueError("feishu webhook host is not the official endpoint")
        if not parsed.path.startswith(_ALLOWED_PATH_PREFIX):
            raise ValueError("feishu webhook path is not the official bot hook path")
        if parsed.query or parsed.fragment:
            raise ValueError("feishu webhook must not carry a query or fragment")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= _MAX_TIMEOUT_S
        ):
            raise ValueError("timeout_seconds must be finite and within 0-30 seconds")

    def __repr__(self) -> str:
        # Dataclass repr would print the URL and secret; mask both.
        return (
            f"FeishuWebhookConfig(webhook_url=<redacted>, secret=<redacted>, "
            f"timeout_seconds={self.timeout_seconds})"
        )


def feishu_sign(timestamp: str, secret: str) -> str:
    """Official custom-bot signature: HMAC-SHA256 keyed by
    ``"{timestamp}\\n{secret}"`` over an empty message, base64-encoded."""

    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class FeishuWebhookTransport(NotificationTransport):
    """Send one text message to the configured custom group bot."""

    kind = NotificationKind.APP_PUSH

    def __init__(
        self,
        config: FeishuWebhookConfig,
        *,
        open_fn: OpenFn | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        config.validate()
        self._config = config
        self._open: OpenFn
        if open_fn is not None:
            self._open = open_fn
        else:
            opener = urllib.request.build_opener(_NoRedirect())
            self._open = lambda request, timeout: opener.open(request, timeout=timeout)
        self._clock = clock or (lambda: datetime.now(UTC))

    def send(self, envelope: NotificationEnvelope) -> NotificationResult:
        attempt_id = uuid.uuid4()
        response: Any = None
        try:
            request = self._build_request(envelope)
            response = self._open(request, self._config.timeout_seconds)
            return self._classify_response(attempt_id, response)
        except TimeoutError:
            return self._result(
                attempt_id,
                DeliveryOutcome.TIMEOUT,
                "bounded timeout reached; whether the remote received the "
                "request is UNKNOWN and must not be claimed either way",
                None,
            )
        except urllib.error.HTTPError as exc:
            response = exc
            outcome, detail, retry_after = _classify_http_error(exc.code, exc.headers)
            return self._result(attempt_id, outcome, detail, retry_after)
        except (urllib.error.URLError, OSError):  # fmt: skip
            return self._result(
                attempt_id,
                DeliveryOutcome.TRANSIENT_FAILURE,
                "feishu connection or response read failed",
                None,
            )
        except (ValueError, TypeError, UnicodeError):  # fmt: skip
            return self._result(
                attempt_id,
                DeliveryOutcome.PERMANENT_FAILURE,
                "invalid local feishu request",
                None,
            )
        except Exception:
            # The transport contract is never-raise. In particular, no remote
            # body, URL, header or exception text may enter persisted results.
            return self._result(
                attempt_id,
                DeliveryOutcome.TRANSIENT_FAILURE,
                "feishu transport failed",
                None,
            )
        finally:
            try:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass

    # -- internals ------------------------------------------------------

    def _build_request(self, envelope: NotificationEnvelope) -> urllib.request.Request:
        payload = self._payload(envelope)
        return urllib.request.Request(
            self._config.webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    def _payload(self, envelope: NotificationEnvelope) -> dict[str, Any]:
        # source_event_id rides IN the message so the group post can be
        # traced back to the alert event (INTERFACES 按事件关联).
        text_body = (
            f"{envelope.subject}\n{envelope.body_text}\n"
            f"来源事件: {envelope.source_event_id}"
        )
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": text_body},
        }
        if self._config.secret:
            timestamp = str(int(self._clock().timestamp()))
            payload["timestamp"] = timestamp
            payload["sign"] = feishu_sign(timestamp, self._config.secret)
        return payload

    def _classify_response(
        self, attempt_id: uuid.UUID, response: Any
    ) -> NotificationResult:
        status = getattr(response, "status", None)
        if status != 200:
            outcome, detail, retry_after = _classify_http_error(
                status, response.headers
            )
            return self._result(
                attempt_id,
                outcome,
                detail,
                retry_after,
            )
        try:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
                raise ValueError("invalid response size")
            body = json.loads(raw.decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError):  # fmt: skip
            # A 200 with a non-JSON body is a gateway/proxy artifact:
            # do not claim acceptance, do not claim permanent rejection.
            return self._result(
                attempt_id,
                DeliveryOutcome.TRANSIENT_FAILURE,
                "HTTP 200 with an unrecognized (non-JSON) body",
                None,
            )
        code = (
            body.get("code", body.get("StatusCode")) if isinstance(body, dict) else None
        )
        if type(code) is not int:
            return self._result(
                attempt_id,
                DeliveryOutcome.TRANSIENT_FAILURE,
                "feishu response has no numeric business code",
                None,
            )
        if code == 0:
            return self._result(
                attempt_id,
                DeliveryOutcome.ACCEPTED_BY_SERVICE,
                "feishu accepted the message (code=0); this is SERVICE "
                "acceptance only — group members' phones may still have "
                "notifications muted and MUST be verified out-of-band",
                None,
                server_response="code=0",
            )
        detail = f"feishu business rejection code={code}"
        return self._result(attempt_id, DeliveryOutcome.PERMANENT_FAILURE, detail, None)

    @staticmethod
    def _result(
        attempt_id: uuid.UUID,
        outcome: DeliveryOutcome,
        detail: str,
        retry_after_s: float | None,
        server_response: str | None = None,
    ) -> NotificationResult:
        return NotificationResult(
            attempt_id=attempt_id,
            transport=NotificationKind.APP_PUSH,
            outcome=outcome,
            delivered=(outcome is DeliveryOutcome.ACCEPTED_BY_SERVICE),
            detail=detail,
            server_response=server_response,
            retry_after_s=retry_after_s,
        )


def _parse_timeout(value: str) -> float:
    if not value:
        return _DEFAULT_TIMEOUT_S
    try:
        result = float(value)
    except ValueError:
        raise ValueError("invalid feishu timeout") from None
    if not math.isfinite(result) or not 0 < result <= _MAX_TIMEOUT_S:
        raise ValueError("invalid feishu timeout")
    return result


def _classify_http_error(
    code: int | None, headers: Any
) -> tuple[DeliveryOutcome, str, float | None]:
    """Map a non-2xx HTTP response to the four-way outcome."""

    if code == 429:
        retry_after: float | None = None
        raw = (headers or {}).get("Retry-After") if headers else None
        if raw:
            try:
                value = float(raw)
                retry_after = (
                    value if math.isfinite(value) and 0 <= value <= 3600 else None
                )
            except (TypeError, ValueError):  # fmt: skip
                retry_after = None
        return (
            DeliveryOutcome.TRANSIENT_FAILURE,
            "feishu rate-limited the request (429)",
            retry_after,
        )
    if isinstance(code, int) and 500 <= code <= 599:
        return (
            DeliveryOutcome.TRANSIENT_FAILURE,
            f"feishu server error ({code}); retriable with backoff",
            None,
        )
    if isinstance(code, int) and 300 <= code <= 399:
        return (
            DeliveryOutcome.PERMANENT_FAILURE,
            "redirect responses are not allowed for the fixed official "
            "endpoint (constraint from the channel decision)",
            None,
        )
    if isinstance(code, int) and 400 <= code <= 499:
        return (
            DeliveryOutcome.PERMANENT_FAILURE,
            f"feishu rejected the request ({code}); not retriable",
            None,
        )
    return (
        DeliveryOutcome.TRANSIENT_FAILURE,
        "unexpected feishu HTTP status",
        None,
    )
