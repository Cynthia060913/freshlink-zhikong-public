"""Isolated durable C→B delivery coordinator with an injected transport port.

Its receipt digest must represent the same frozen JSON body stored by C; an
unverifiable reply never marks work delivered. The HTTP adapter is opt-in; no
scheduler, shared migration, or B route is installed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy.engine import Engine

from app.modules.telemetry.outbox_candidate import (
    DeliveryClaimCandidate,
    claim_candidate,
    due_source_ids_candidate,
    finish_candidate,
)

ReceiptStatus = Literal[
    "accepted_new",
    "accepted_duplicate",
    "conflict",
    "invalid",
    "retryable_failure",
]
DeliveryOutcome = Literal["not_due", "delivered", "retry", "rejected", "lost_lease"]


@dataclass(frozen=True)
class RemoteCandidateReceipt:
    source_event_id: UUID
    status: ReceiptStatus
    body_sha256: str | None = None
    alert_status: Literal["open", "acknowledged", "resolved"] | None = None


class CandidateDeliveryPort(Protocol):
    def lookup(self, source_event_id: UUID) -> RemoteCandidateReceipt | None: ...

    def send(self, body: dict[str, Any]) -> RemoteCandidateReceipt: ...


def _decision(
    receipt: RemoteCandidateReceipt,
    claim: DeliveryClaimCandidate,
) -> tuple[Literal["delivered", "retry", "rejected"], str]:
    if receipt.source_event_id != claim.source_event_id:
        return "retry", "b_source_unverifiable"
    if receipt.status in ("accepted_new", "accepted_duplicate"):
        if receipt.body_sha256 == claim.body_sha256:
            return "delivered", ""
        return "retry", "b_body_unverifiable"
    if receipt.status == "conflict":
        return "rejected", "b_content_conflict"
    if receipt.status == "invalid":
        return "rejected", "b_invalid_candidate"
    if receipt.status == "retryable_failure":
        return "retry", "b_retryable_failure"
    return "retry", "b_receipt_unverifiable"


def _confirm_recovery(
    port: CandidateDeliveryPort,
    claim: DeliveryClaimCandidate,
    decision: tuple[Literal["delivered", "retry", "rejected"], str],
) -> tuple[Literal["delivered", "retry", "rejected"], str]:
    """A recovery is delivered only after B reports its original as resolved.

    B promises to insert the recovery and resolve the original in one
    transaction. The read-back checks that promise even when the POST reply
    was lost; uncertainty keeps the same durable source ID and frozen body.
    """
    if decision[0] != "delivered" or claim.body.get("kind") != "temperature_recovered":
        return decision
    try:
        related = UUID(claim.body["related_source_event_id"])
    except (KeyError, TypeError, ValueError):  # fmt: skip
        return "rejected", "invalid_recovery_reference"
    try:
        original = port.lookup(related)
    except PermissionError:
        return "retry", "b_auth_unavailable"
    except (TimeoutError, ConnectionError):  # fmt: skip
        return "retry", "b_recovery_unverifiable"
    if (
        original is None
        or original.source_event_id != related
        or original.alert_status != "resolved"
    ):
        return "retry", "b_recovery_unverifiable"
    return decision


def _finish(
    engine: Engine,
    claim: DeliveryClaimCandidate,
    decision: tuple[Literal["delivered", "retry", "rejected"], str],
    *,
    now: datetime,
    max_attempts: int,
) -> DeliveryOutcome:
    disposition, error_code = decision
    if disposition == "retry" and claim.attempt_count >= max_attempts:
        disposition, error_code = "rejected", "retry_budget_exhausted"
    # Keep a bounded durable delay; never spin on a timeout or 503.
    delay = min(30 * (2 ** min(claim.attempt_count - 1, 7)), 3600)
    finished = finish_candidate(
        engine,
        claim=claim,
        disposition=disposition,
        now=now,
        error_code=error_code,
        retry_after_seconds=delay if disposition == "retry" else 0,
    )
    return disposition if finished else "lost_lease"


def deliver_once_candidate(
    engine: Engine,
    *,
    source_event_id: UUID,
    now: datetime,
    port: CandidateDeliveryPort,
    max_attempts: int = 10,
) -> DeliveryOutcome:
    """Claim one durable event and resolve uncertain delivery before re-sending.

    The transport adapter converts timeouts/connectivity failures into
    TimeoutError/ConnectionError and authorization failures into
    PermissionError. These retain bounded durable retry evidence; they never
    fabricate an acceptance. Unexpected exceptions leave the lease to expire.
    """
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or not 1 <= max_attempts <= 100
    ):
        raise ValueError("max_attempts must be 1-100")
    claim = claim_candidate(engine, source_event_id=source_event_id, now=now)
    if claim is None:
        return "not_due"

    try:
        prior = port.lookup(claim.source_event_id)
    except PermissionError:
        return _finish(
            engine,
            claim,
            ("retry", "b_auth_unavailable"),
            now=now,
            max_attempts=max_attempts,
        )
    except (TimeoutError, ConnectionError):  # fmt: skip
        return _finish(
            engine,
            claim,
            ("retry", "b_lookup_unavailable"),
            now=now,
            max_attempts=max_attempts,
        )
    if prior is not None:
        return _finish(
            engine,
            claim,
            _confirm_recovery(port, claim, _decision(prior, claim)),
            now=now,
            max_attempts=max_attempts,
        )

    receipt: RemoteCandidateReceipt | None
    try:
        receipt = port.send(claim.body)
    except PermissionError:
        return _finish(
            engine,
            claim,
            ("retry", "b_auth_unavailable"),
            now=now,
            max_attempts=max_attempts,
        )
    except (TimeoutError, ConnectionError):  # fmt: skip
        # The send may have committed at B. Read by source before any retry.
        try:
            receipt = port.lookup(claim.source_event_id)
        except (TimeoutError, ConnectionError):  # fmt: skip
            receipt = None
    if receipt is None:
        return _finish(
            engine,
            claim,
            ("retry", "b_timeout_unresolved"),
            now=now,
            max_attempts=max_attempts,
        )
    return _finish(
        engine,
        claim,
        _confirm_recovery(port, claim, _decision(receipt, claim)),
        now=now,
        max_attempts=max_attempts,
    )


def deliver_due_batch_candidate(
    engine: Engine,
    *,
    now: datetime,
    port: CandidateDeliveryPort,
    limit: int = 100,
    max_attempts: int = 10,
) -> list[tuple[UUID, DeliveryOutcome]]:
    """Process one bounded due page without marking discovery as a claim.

    Another worker may win between discovery and claim; such an item reports
    ``not_due``. A later invocation discovers the next page. The caller owns
    scheduling and must not treat this helper as an installed service.
    """
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or not 1 <= max_attempts <= 100
    ):
        raise ValueError("max_attempts must be 1-100")
    due = due_source_ids_candidate(engine, now=now, limit=limit)
    return [
        (
            source_id,
            deliver_once_candidate(
                engine,
                source_event_id=source_id,
                now=now,
                port=port,
                max_attempts=max_attempts,
            ),
        )
        for source_id in due
    ]
