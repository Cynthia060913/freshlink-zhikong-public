"""Opt-in, durable C correction delivery candidate; no scheduler is installed.

Only this port may claim C's internal ``held_contract`` correction rows. It
projects the separate B 1.0 wire body under the claim transaction and leaves
the internal body/hash untouched. Network calls happen after that commit.
Activation still depends on B's reviewed linked-correction contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from app.modules.telemetry.correction_wire_candidate import (
    _prepare_correction_wire_on_connection_candidate,
)
from app.modules.telemetry.data_quality_correction_candidate import _utc as _stored_utc
from app.modules.telemetry.delivery_candidate import (
    CandidateDeliveryPort,
    InvalidLocalCandidate,
    RemoteCandidateReceipt,
)
from app.modules.telemetry.outbox_candidate import _aware_now, outbox

CorrectionDeliveryOutcome = Literal[
    "not_due", "delivered", "retry", "rejected", "lost_lease"
]
_Disposition = Literal["delivered", "retry", "rejected"]


@dataclass(frozen=True)
class CorrectionDeliveryClaimCandidate:
    source_event_id: UUID
    lease_token: UUID
    wire_body: dict[str, Any]
    wire_digest: str
    internal_body_sha256: str
    attempt_count: int


def claim_correction_wire_candidate(
    engine: Engine,
    *,
    source_event_id: UUID,
    now: datetime,
    lease_seconds: int = 30,
    contract_confirmed: bool = False,
) -> CorrectionDeliveryClaimCandidate | None:
    """Claim one due linked correction with a fenced, committed lease.

    Recovery's generic claim requires frozen A frontier fields and cannot be
    used for a correction. This function is not registered with a scheduler.
    The default holds every row until B's corrected 1.0 contract is reviewed
    and the integration caller explicitly enables this candidate.
    """
    if not isinstance(source_event_id, UUID):
        raise ValueError("source_event_id must be a UUID")
    if not isinstance(contract_confirmed, bool):
        raise ValueError("contract_confirmed must be a bool")
    if not contract_confirmed:
        return None
    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int)
        or not 1 <= lease_seconds <= 300
    ):
        raise ValueError("lease_seconds must be 1-300")
    current = _aware_now(now)
    token = uuid4()
    with engine.begin() as connection:
        row = (
            connection.execute(
                select(outbox)
                .where(outbox.c.source_event_id == source_event_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["state"] not in {
            "held_contract",
            "pending",
            "leased",
        }:
            return None
        if row["state"] == "leased":
            if (
                row["lease_until_utc"] is None
                or _stored_utc(row["lease_until_utc"], stored=True) > current
            ):
                return None
        elif _stored_utc(row["next_attempt_at_utc"], stored=True) > current:
            return None

        projection = _prepare_correction_wire_on_connection_candidate(
            connection, source_event_id=source_event_id, claim_retry=True
        )
        if projection is None:
            return None
        prior_token = row["lease_token"]
        lease_match = (
            outbox.c.lease_token.is_(None)
            if prior_token is None
            else outbox.c.lease_token == prior_token
        )
        changed = connection.execute(
            update(outbox)
            .where(
                outbox.c.source_event_id == source_event_id,
                outbox.c.state == row["state"],
                outbox.c.attempt_count == row["attempt_count"],
                outbox.c.body_sha256 == projection.internal_body_sha256,
                lease_match,
            )
            .values(
                state="leased",
                lease_token=token,
                lease_until_utc=current + timedelta(seconds=lease_seconds),
                attempt_count=outbox.c.attempt_count + 1,
            )
        )
        if changed.rowcount != 1:
            return None
    return CorrectionDeliveryClaimCandidate(
        source_event_id=source_event_id,
        lease_token=token,
        wire_body=projection.body,
        wire_digest=projection.content_digest,
        internal_body_sha256=projection.internal_body_sha256,
        attempt_count=row["attempt_count"] + 1,
    )


def finish_correction_wire_candidate(
    engine: Engine,
    *,
    claim: CorrectionDeliveryClaimCandidate,
    disposition: _Disposition,
    now: datetime,
    error_code: str = "",
    retry_after_seconds: int = 0,
) -> bool:
    """Only the current lease and unchanged internal evidence may finish."""
    if not isinstance(claim, CorrectionDeliveryClaimCandidate):
        raise ValueError("claim must be CorrectionDeliveryClaimCandidate")
    if disposition not in ("delivered", "retry", "rejected"):
        raise ValueError("invalid correction disposition")
    if not isinstance(error_code, str) or len(error_code) > 80:
        raise ValueError("error_code must be at most 80 characters")
    if disposition != "delivered" and not error_code:
        raise ValueError("failed correction delivery requires error_code")
    if (
        isinstance(retry_after_seconds, bool)
        or not isinstance(retry_after_seconds, int)
        or not 0 <= retry_after_seconds <= 3600
    ):
        raise ValueError("retry_after_seconds must be 0-3600")
    current = _aware_now(now)
    next_state = {"delivered": "delivered", "retry": "pending", "rejected": "rejected"}[
        disposition
    ]
    with engine.begin() as connection:
        changed = connection.execute(
            update(outbox)
            .where(
                outbox.c.source_event_id == claim.source_event_id,
                outbox.c.state == "leased",
                outbox.c.lease_token == claim.lease_token,
                outbox.c.body_sha256 == claim.internal_body_sha256,
            )
            .values(
                state=next_state,
                next_attempt_at_utc=current + timedelta(seconds=retry_after_seconds),
                lease_token=None,
                lease_until_utc=None,
                last_error_code=error_code or None,
            )
        )
    return changed.rowcount == 1


def _decision(
    receipt: RemoteCandidateReceipt, claim: CorrectionDeliveryClaimCandidate
) -> tuple[_Disposition, str]:
    if receipt.source_event_id != claim.source_event_id:
        return "retry", "b_source_unverifiable"
    if receipt.status in ("accepted_new", "accepted_duplicate"):
        if receipt.body_sha256 == claim.wire_digest.removeprefix("sha256:"):
            return "delivered", ""
        return "retry", "b_wire_digest_unverifiable"
    if receipt.status == "conflict":
        return "rejected", "b_content_conflict"
    if receipt.status == "invalid":
        return "rejected", "b_invalid_candidate"
    return "retry", "b_retryable_failure"


def _finish_decision(
    engine: Engine,
    claim: CorrectionDeliveryClaimCandidate,
    decision: tuple[_Disposition, str],
    *,
    now: datetime,
    max_attempts: int,
) -> CorrectionDeliveryOutcome:
    disposition, error_code = decision
    if disposition == "retry" and claim.attempt_count >= max_attempts:
        disposition, error_code = "rejected", "retry_budget_exhausted"
    delay = min(30 * (2 ** min(claim.attempt_count - 1, 7)), 3600)
    finished = finish_correction_wire_candidate(
        engine,
        claim=claim,
        disposition=disposition,
        now=now,
        error_code=error_code,
        retry_after_seconds=delay if disposition == "retry" else 0,
    )
    return disposition if finished else "lost_lease"


def deliver_correction_once_candidate(
    engine: Engine,
    *,
    source_event_id: UUID,
    now: datetime,
    port: CandidateDeliveryPort,
    max_attempts: int = 10,
    contract_confirmed: bool = False,
) -> CorrectionDeliveryOutcome:
    """Resolve uncertainty by source before POST, then persist a fenced result.

    A lost POST reply may follow a committed B insert. This path reads B again
    before retrying the same immutable wire body and source identity. No call
    occurs while a C transaction holds the outbox row lock.
    """
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or not 1 <= max_attempts <= 100
    ):
        raise ValueError("max_attempts must be 1-100")
    claim = claim_correction_wire_candidate(
        engine,
        source_event_id=source_event_id,
        now=now,
        contract_confirmed=contract_confirmed,
    )
    if claim is None:
        return "not_due"

    try:
        prior = port.lookup(source_event_id)
    except PermissionError:
        return _finish_decision(
            engine,
            claim,
            ("retry", "b_auth_unavailable"),
            now=now,
            max_attempts=max_attempts,
        )
    except (TimeoutError, ConnectionError):  # fmt: skip
        return _finish_decision(
            engine,
            claim,
            ("retry", "b_lookup_unavailable"),
            now=now,
            max_attempts=max_attempts,
        )
    if prior is not None:
        return _finish_decision(
            engine,
            claim,
            _decision(prior, claim),
            now=now,
            max_attempts=max_attempts,
        )

    receipt: RemoteCandidateReceipt | None
    try:
        receipt = port.send(claim.wire_body)
    except InvalidLocalCandidate:
        return _finish_decision(
            engine,
            claim,
            ("rejected", "invalid_local_candidate"),
            now=now,
            max_attempts=max_attempts,
        )
    except PermissionError:
        return _finish_decision(
            engine,
            claim,
            ("retry", "b_auth_unavailable"),
            now=now,
            max_attempts=max_attempts,
        )
    except (TimeoutError, ConnectionError):  # fmt: skip
        try:
            receipt = port.lookup(source_event_id)
        except PermissionError:
            return _finish_decision(
                engine,
                claim,
                ("retry", "b_auth_unavailable"),
                now=now,
                max_attempts=max_attempts,
            )
        except (TimeoutError, ConnectionError):  # fmt: skip
            receipt = None
    decision = (
        _decision(receipt, claim)
        if receipt is not None
        else ("retry", "b_timeout_unresolved")
    )
    return _finish_decision(engine, claim, decision, now=now, max_attempts=max_attempts)
