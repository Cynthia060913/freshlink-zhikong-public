"""C-only synchronous F03 HTTP port for B's versioned candidate routes.

No endpoint, credentials, worker schedule, or shared root route is installed.
The caller supplies a short-lived bearer credential through a callback; this
module never logs or persists it. A redirect is refused to avoid forwarding
that credential to a different origin.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPMessage
from typing import IO, Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    Request,
    build_opener,
)
from uuid import UUID

from app.modules.telemetry.correction_wire_candidate import (
    validate_correction_wire_body_candidate,
)
from app.modules.telemetry.delivery_candidate import (
    InvalidLocalCandidate,
    ReceiptStatus,
    RemoteCandidateReceipt,
)
from app.modules.telemetry.f03_body_candidate import f03_digest_candidate

_MAX_REPLY_BYTES = 65536
# One delivery can perform up to four requests (preflight lookup, POST,
# uncertain-POST lookup, recovery-original lookup). Keep their combined
# network timeout below the outbox's 30-second lease.
_MAX_REQUEST_TIMEOUT_S = 5.0
_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
_POST_STATUSES: dict[int, ReceiptStatus] = {
    201: "accepted_new",
    200: "accepted_duplicate",
    409: "conflict",
    422: "invalid",
    503: "retryable_failure",
}


@dataclass(frozen=True)
class HttpCandidateReply:
    status_code: int
    body: bytes


class CandidateHttpTransport(Protocol):
    def request(
        self, method: str, path: str, body: bytes | None = None
    ) -> HttpCandidateReply: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        return None


class UrllibCandidateTransport:
    """Opt-in network transport; never creates or sends a token itself."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer: Callable[[], str],
        timeout_s: float = 5.0,
        opener: OpenerDirector | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("base_url must be one HTTP origin without credentials")
        if parsed.scheme == "http" and parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            raise ValueError("bearer over plain HTTP is limited to local loopback")
        if not 0 < timeout_s <= _MAX_REQUEST_TIMEOUT_S:
            raise ValueError("timeout_s must be 0-5 seconds within the outbox lease")
        self._base_url = base_url.rstrip("/")
        self._bearer = bearer
        self._timeout_s = timeout_s
        self._opener = opener or build_opener(_NoRedirect())

    def request(
        self, method: str, path: str, body: bytes | None = None
    ) -> HttpCandidateReply:
        if method not in ("GET", "POST") or not path.startswith("/api/v1/"):
            raise ValueError("unsupported B candidate route")
        token = self._bearer()
        if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
            raise PermissionError("B service bearer credential unavailable")
        request = Request(
            self._base_url + path,
            data=body,
            method=method,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout_s) as response:
                payload = response.read(_MAX_REPLY_BYTES + 1)
                status = response.status
        except HTTPError as exc:
            try:
                if 300 <= exc.code < 400:
                    raise ConnectionError("B candidate redirect refused") from exc
                payload = exc.read(_MAX_REPLY_BYTES + 1)
                status = exc.code
            finally:
                exc.close()
        except URLError as exc:
            raise ConnectionError("B candidate transport unavailable") from exc
        if len(payload) > _MAX_REPLY_BYTES:
            raise ConnectionError("B candidate response exceeds limit")
        return HttpCandidateReply(status, payload)


def _json_object(reply: HttpCandidateReply) -> dict[str, Any]:
    try:
        value = json.loads(reply.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectionError("B candidate response is not JSON") from exc
    if not isinstance(value, dict):
        raise ConnectionError("B candidate response is not an object")
    return value


def _remote_receipt(
    data: dict[str, Any], expected_status: ReceiptStatus
) -> RemoteCandidateReceipt:
    if data.get("status") != expected_status:
        raise ConnectionError("B candidate status disagrees with HTTP status")
    try:
        source_id = UUID(data["source_event_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConnectionError("B candidate source ID is unverifiable") from exc
    digest = data.get("content_digest")
    match = _DIGEST.fullmatch(digest) if isinstance(digest, str) else None
    return RemoteCandidateReceipt(
        source_event_id=source_id,
        status=expected_status,
        body_sha256=match.group(1) if match else None,
    )


class BHttpCandidatePort:
    """Map B's HTTP status/JSON to the durable C delivery port semantics."""

    def __init__(self, transport: CandidateHttpTransport) -> None:
        self._transport = transport

    def lookup(self, source_event_id: UUID) -> RemoteCandidateReceipt | None:
        reply = self._transport.request(
            "GET", f"/api/v1/candidates/by-source/{source_event_id}"
        )
        if reply.status_code == 404:
            return None
        if reply.status_code in (400, 401, 403):
            raise PermissionError("B candidate lookup authorization failed")
        if reply.status_code != 200:
            raise ConnectionError("B candidate lookup unavailable")
        data = _json_object(reply)
        try:
            returned = UUID(data["source_event_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConnectionError("B candidate lookup source is unverifiable") from exc
        digest = data.get("content_digest")
        match = _DIGEST.fullmatch(digest) if isinstance(digest, str) else None
        status = data.get("status")
        alert_status = (
            status if status in ("open", "acknowledged", "resolved") else None
        )
        return RemoteCandidateReceipt(
            source_event_id=returned,
            status="accepted_duplicate",
            body_sha256=match.group(1) if match else None,
            alert_status=alert_status,
        )

    def send(self, body: dict[str, Any]) -> RemoteCandidateReceipt:
        # A partial or previously unnormalized body must never reach B: its
        # canonical DTO field set could differ from C's persisted digest.
        try:
            f03_digest_candidate(body)
        except (TypeError, ValueError) as exc:  # fmt: skip
            raise InvalidLocalCandidate("frozen F03 body is incompatible") from exc
        encoded = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        reply = self._transport.request("POST", "/api/v1/candidates", encoded)
        if reply.status_code in (400, 401, 403):
            raise PermissionError("B candidate send authorization failed")
        expected = _POST_STATUSES.get(reply.status_code)
        if expected is None:
            raise ConnectionError("B candidate send returned unexpected HTTP status")
        if reply.status_code == 422:
            # FastAPI may reject malformed JSON before B creates a structured
            # CandidateReceipt. That input is permanent, never a retryable 503.
            try:
                data = _json_object(reply)
                if data.get("status") == "invalid":
                    return _remote_receipt(data, "invalid")
            except ConnectionError:
                pass
            return RemoteCandidateReceipt(UUID(body["source_event_id"]), "invalid")
        return _remote_receipt(_json_object(reply), expected)


class BHttpLinkedCorrectionPort(BHttpCandidatePort):
    """Explicit opt-in B transport for C's linked data-quality correction.

    The ordinary F03 port intentionally rejects a related data-quality row.
    This narrow adapter accepts only the exact projected correction wire DTO;
    it never forwards C's internal v0 body or enables a background sender.
    """

    def send(self, body: dict[str, Any]) -> RemoteCandidateReceipt:
        try:
            validate_correction_wire_body_candidate(body)
        except (TypeError, ValueError) as exc:  # fmt: skip
            raise InvalidLocalCandidate("linked correction wire is invalid") from exc
        encoded = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        reply = self._transport.request("POST", "/api/v1/candidates", encoded)
        if reply.status_code in (400, 401, 403):
            raise PermissionError("B correction send authorization failed")
        expected = _POST_STATUSES.get(reply.status_code)
        if expected is None:
            raise ConnectionError("B correction send returned unexpected HTTP status")
        if reply.status_code == 422:
            try:
                data = _json_object(reply)
                if data.get("status") == "invalid":
                    return _remote_receipt(data, "invalid")
            except ConnectionError:
                pass
            return RemoteCandidateReceipt(UUID(body["source_event_id"]), "invalid")
        return _remote_receipt(_json_object(reply), expected)
