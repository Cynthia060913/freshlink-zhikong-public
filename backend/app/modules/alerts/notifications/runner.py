"""Persistent dispatch runner (B3-R): standalone, crash-safe resumption.

:func:`~app.modules.alerts.notifications.dispatch.dispatch_committed_event`
dispatches ONE already-committed event per call; it has no memory
across process restarts. This module adds the running layer A asked
for (#8 6383401217/6382361717): an explicitly-started worker that
scans for committed alert events still owed a notification and
dispatches them through independent attempt transactions, so progress
survives crashes and restarts.

Durability properties (no new framework, no new tables, no migration):

- **Pending rule**: an event is pending while at least one configured
  channel still lacks a delivered attempt row. A restart re-picks it;
  the dispatcher's per-channel dedup guard completes only the missing
  channels, so a partially delivered event is finished, never
  duplicated. A channel with an ``acceptance_unknown`` attempt
  (acceptance unproven) or an unfinished ``send_intent`` row (the
  dispatcher persisted its pre-send intent but never completed it —
  crash or audit-write failure) is held for manual verification
  instead of being re-picked — no automatic resend after a restart.
- **Single-dispatcher mutex**: :class:`_DispatchLease` holds a
  session-scoped PG advisory lock (``pg_try_advisory_lock``) on a
  DEDICATED lease connection that runs no business transaction and
  takes no alert row locks — the lock only makes single-worker
  exclusion observable. A second worker fails to acquire it and stops
  before scanning/sending. PG releases the lock automatically when
  the connection dies, so a crashed worker cannot block the next one;
  the worker pings the lease connection every cycle and STOPS
  claiming new dispatches when the ping fails (connection lost).
- **Persistent retry budget**: every attempt is an append-only
  ``alerts_notification_attempt`` row. An event whose total attempt
  count reached ``max_attempts_per_event`` is left to a later cycle or
  a human reading the attempt trail (hammering guard); an event whose
  newest attempt is younger than ``min_attempt_age_s`` is deferred
  (backoff that survives restarts — the budget lives in the table,
  never in memory).
- **Dry-run**: ``dry_run=True`` reports what WOULD be dispatched and
  writes nothing — no sends, no delivered rows, no attempt rows.
- **Conservative failure**: dedup-store unavailability fails closed
  inside the dispatcher (zero sends); a worker cycle whose database
  access fails is recorded with the exception type name and the
  worker stops claiming new dispatches.
- **Inert by default**: with no channels configured,
  :func:`build_transports` yields nothing and every cycle is a
  no-op. Real sending requires explicit channel configuration
  AND a specific human authorization; this module never enables it
  on its own.

Run one worker per deployment:

    python -m app.modules.alerts.notifications.runner

The pending scan is an ordering guard, NOT concurrent exactly-once:
the lease makes single-worker exclusion real for one deployment, but
the scan itself claims nothing transactional.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from collections.abc import (
    Callable,
    Iterator,
    Sequence,
)
from contextlib import (
    AbstractContextManager,
    contextmanager,
)
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_
from sqlmodel import Session, col, func, select

from app.modules.alerts.models.event import AlertEvent, NotificationAttempt

from .dispatch import (
    ACCEPTANCE_UNKNOWN_OUTCOME,
    SEND_INTENT_OUTCOME,
    DispatchReport,
    build_transports,
    dispatch_committed_event,
)
from .transport import NotificationKind, NotificationTransport

_DEFAULT_LIMIT = 50
# 2 channels x max_attempts=3 x 2 full cycles: enough honest retries
# across restarts, small enough that a dead channel cannot loop hot.
_DEFAULT_MAX_ATTEMPTS_PER_EVENT = 12
# Persistent cross-cycle backoff: an event is not re-dispatched while
# its newest attempt row is younger than this.
_DEFAULT_MIN_ATTEMPT_AGE_S = 60.0
# Arbitrary fixed integer key for the single-dispatcher advisory lock.
_DISPATCH_LEASE_KEY = 745_982_001


class DispatchLeaseBusy(RuntimeError):
    """Another dispatch worker already holds the single-dispatcher lease."""


@dataclasses.dataclass(frozen=True)
class RunnerCycleReport:
    """What one worker cycle scanned and dispatched.

    ``pending`` holds the event ids picked this cycle (before any
    dispatch); ``reports`` pairs each id with the per-event
    :class:`DispatchReport` (check ``dedup_error`` there to see
    fail-closed cycles). ``dry_run=True`` means nothing was sent and
    nothing written. ``interrupted`` carries a FIXED exception/type
    label when the cycle could not run (``lease_busy`` = another
    worker holds the lease; otherwise the exception type name, e.g.
    a database availability failure) — the worker stops claiming new
    dispatches after an interruption.
    """

    pending: tuple[uuid.UUID, ...] = ()
    reports: tuple[tuple[uuid.UUID, DispatchReport], ...] = ()
    dry_run: bool = False
    interrupted: str | None = None


def pending_alert_event_ids(
    session_factory: Callable[[], AbstractContextManager[Session]],
    *,
    channels: Sequence[str] | None = None,
    limit: int = _DEFAULT_LIMIT,
    max_attempts_per_event: int = _DEFAULT_MAX_ATTEMPTS_PER_EVENT,
    min_attempt_age_s: float = _DEFAULT_MIN_ATTEMPT_AGE_S,
) -> list[uuid.UUID]:
    """Committed events still owed a notification, oldest first.

    An event is pending while at least one of ``channels`` (default:
    every known channel kind) still lacks a delivered attempt row, its
    total attempt count is below ``max_attempts_per_event`` (budget
    guard), and its newest attempt row is at least
    ``min_attempt_age_s`` old (persistent backoff; events with no
    attempts yet are always eligible). Partially delivered events stay
    pending: the dispatcher's dedup guard completes only the missing
    channels.

    A channel with an ``acceptance_unknown`` attempt (acceptance could
    not be proven — connection broke mid-DATA) or an UNFINISHED
    ``send_intent`` row (a send started but its final state was never
    persisted — crash or failed write-back) is held for manual
    verification and does NOT make the event pending again: an
    unproven acceptance must never be answered with an automatic
    re-send after a restart (C review 5678438821; intent hold A review
    5682106843).
    """

    if min_attempt_age_s < 0:
        raise ValueError("min_attempt_age_s must be non-negative")
    if channels is None:
        channels = tuple(kind.value for kind in NotificationKind)
    if not channels:
        return []
    # Settled on EVERY configured channel == nothing left to send.
    # Per-channel (not per-event) so partially delivered events stay
    # pending and get completed, not duplicated. Settled = delivered
    # OR acceptance-unknown OR an unfinished send_intent row (held for
    # manual verification, never auto-resent — A review 5682106843).
    settled_everywhere = and_(
        *(
            or_(
                select(NotificationAttempt.id)
                .where(
                    col(NotificationAttempt.alert_event_id) == col(AlertEvent.id),
                    col(NotificationAttempt.channel) == channel,
                    col(NotificationAttempt.delivered).is_(True),
                )
                .exists(),
                select(NotificationAttempt.id)
                .where(
                    col(NotificationAttempt.alert_event_id) == col(AlertEvent.id),
                    col(NotificationAttempt.channel) == channel,
                    col(NotificationAttempt.outcome) == ACCEPTANCE_UNKNOWN_OUTCOME,
                )
                .exists(),
                select(NotificationAttempt.id)
                .where(
                    col(NotificationAttempt.alert_event_id) == col(AlertEvent.id),
                    col(NotificationAttempt.channel) == channel,
                    col(NotificationAttempt.outcome) == SEND_INTENT_OUTCOME,
                )
                .exists(),
            )
            for channel in channels
        )
    )
    attempt_count = (
        select(func.count())
        .select_from(NotificationAttempt)
        .where(col(NotificationAttempt.alert_event_id) == col(AlertEvent.id))
        .scalar_subquery()
    )
    latest_attempt = (
        select(func.max(col(NotificationAttempt.created_at)))
        .where(col(NotificationAttempt.alert_event_id) == col(AlertEvent.id))
        .scalar_subquery()
    )
    backoff_elapsed = or_(
        attempt_count == 0,
        latest_attempt <= datetime.now(UTC) - timedelta(seconds=min_attempt_age_s),
    )
    statement = (
        select(AlertEvent.id)
        .where(
            ~settled_everywhere,
            attempt_count < max_attempts_per_event,
            backoff_elapsed,
        )
        .order_by(col(AlertEvent.created_at), col(AlertEvent.id))
        .limit(limit)
    )
    with session_factory() as session:
        return list(session.exec(statement))


def _try_advisory_lock(session: Session, key: int) -> bool:
    return bool(session.exec(select(func.pg_try_advisory_lock(key))).one())


class _DispatchLease:
    """Session-scoped PG advisory lock: the single-dispatcher mutex.

    Held on a DEDICATED lease connection for the worker's whole
    lifetime; that connection runs no business transaction and takes
    no alert row locks — it exists only to make exclusion observable.
    PG releases session advisory locks when the connection dies, so a
    crashed worker cannot block the next one. :meth:`ping` proves the
    lease connection is still alive; the worker calls it every cycle
    and stops claiming dispatches when it fails.
    """

    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager[Session]],
        *,
        key: int = _DISPATCH_LEASE_KEY,
    ) -> None:
        self._session_factory = session_factory
        self._key = key
        self.acquired = False
        self._cm: AbstractContextManager[Session] | None = None
        self._session: Session | None = None

    def __enter__(self) -> _DispatchLease:
        self._cm = self._session_factory()
        session = self._cm.__enter__()
        self._session = session
        if not _try_advisory_lock(session, self._key):
            self._release()
            raise DispatchLeaseBusy("another dispatch worker holds the advisory lease")
        self.acquired = True
        return self

    def ping(self) -> None:
        """Raise if the lease connection (and with it the lock) is gone."""

        if self._session is None:
            raise RuntimeError("lease not entered")
        self._session.exec(select(func.now())).one()

    def _release(self) -> None:
        session = self._session
        cm = self._cm
        self._session = None
        self._cm = None
        if session is not None and self.acquired:
            try:
                session.exec(select(func.pg_advisory_unlock(self._key)))
            except Exception:
                pass  # connection gone: PG released the lock with it
        if cm is not None:
            cm.__exit__(None, None, None)

    def __exit__(self, *exc: object) -> None:
        self._release()


def run_pending_dispatch_cycle(
    session_factory: Callable[[], AbstractContextManager[Session]],
    *,
    transports: Sequence[NotificationTransport] | None = None,
    recipients: Sequence[str] = (),
    limit: int = _DEFAULT_LIMIT,
    max_attempts_per_event: int = _DEFAULT_MAX_ATTEMPTS_PER_EVENT,
    min_attempt_age_s: float = _DEFAULT_MIN_ATTEMPT_AGE_S,
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 30.0,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> RunnerCycleReport:
    """Dispatch every pending event once, then return.

    One cycle = one bounded scan + one :func:`dispatch_committed_event`
    per pending event. With ``dry_run=True`` the cycle reports the
    pending ids and performs NO sends and NO writes. Transports are
    built once per cycle unless supplied, including for dry-run so its
    pending set reflects the actual configured channels.
    """

    if transports is None:
        transports = build_transports()
    pending = tuple(
        pending_alert_event_ids(
            session_factory,
            channels=tuple(transport.kind.value for transport in transports),
            limit=limit,
            max_attempts_per_event=max_attempts_per_event,
            min_attempt_age_s=min_attempt_age_s,
        )
    )
    if dry_run:
        return RunnerCycleReport(pending=pending, dry_run=True)
    reports: list[tuple[uuid.UUID, DispatchReport]] = []
    for event_id in pending:
        report = dispatch_committed_event(
            session_factory,
            event_id,
            transports=transports,
            recipients=recipients,
            max_attempts=max_attempts,
            base_delay_s=base_delay_s,
            max_delay_s=max_delay_s,
            sleep=sleep,
        )
        reports.append((event_id, report))
    return RunnerCycleReport(pending=pending, reports=tuple(reports))


@contextmanager
def _lease_or_none(
    with_lease: bool,
    lease_session_factory: Callable[[], AbstractContextManager[Session]],
    lease_key: int,
) -> Iterator[_DispatchLease | None]:
    if not with_lease:
        yield None
        return
    lease = _DispatchLease(lease_session_factory, key=lease_key)
    lease.__enter__()
    try:
        yield lease
    finally:
        lease.__exit__(None, None, None)


def run_dispatch_worker(
    session_factory: Callable[[], AbstractContextManager[Session]],
    *,
    transports: Sequence[NotificationTransport] | None = None,
    recipients: Sequence[str] = (),
    limit: int = _DEFAULT_LIMIT,
    max_attempts_per_event: int = _DEFAULT_MAX_ATTEMPTS_PER_EVENT,
    min_attempt_age_s: float = _DEFAULT_MIN_ATTEMPT_AGE_S,
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 30.0,
    dry_run: bool = False,
    poll_interval_s: float = 60.0,
    max_cycles: int | None = None,
    stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    with_lease: bool = True,
    lease_session_factory: Callable[[], AbstractContextManager[Session]] | None = None,
    lease_key: int = _DISPATCH_LEASE_KEY,
) -> list[RunnerCycleReport]:
    """Single-worker dispatch loop (the deployment's ONE runner).

    Acquires the single-dispatcher advisory lease on a dedicated
    connection (``with_lease=True``), then runs
    :func:`run_pending_dispatch_cycle` and sleeps ``poll_interval_s``
    between cycles. Semantics:

    - Another worker holding the lease → this call returns a single
      report with ``interrupted="lease_busy"`` and sends NOTHING.
    - The lease connection is pinged every cycle; a lost connection
      (or any database failure) is recorded as ``interrupted=<type
      name>`` and the worker stops claiming new dispatches. Restart
      the process to resume: the attempt table carries the budget.
    - ``max_cycles=None`` runs until ``stop()`` returns True (forever
      when no ``stop`` is given); ``max_cycles`` bounds the loop for
      tests/supervisors. ``dry_run=True`` never sends or writes.
    """

    if poll_interval_s < 0:
        raise ValueError("poll_interval_s must be non-negative")
    if max_cycles is not None and max_cycles < 1:
        raise ValueError("max_cycles must be at least 1")
    if min_attempt_age_s < 0:
        raise ValueError("min_attempt_age_s must be non-negative")
    reports: list[RunnerCycleReport] = []
    try:
        with _lease_or_none(
            with_lease,
            lease_session_factory or session_factory,
            lease_key,
        ) as lease:
            cycle = 0
            while max_cycles is None or cycle < max_cycles:
                if stop is not None and stop():
                    break
                try:
                    if lease is not None:
                        lease.ping()
                    reports.append(
                        run_pending_dispatch_cycle(
                            session_factory,
                            transports=transports,
                            recipients=recipients,
                            limit=limit,
                            max_attempts_per_event=max_attempts_per_event,
                            min_attempt_age_s=min_attempt_age_s,
                            max_attempts=max_attempts,
                            base_delay_s=base_delay_s,
                            max_delay_s=max_delay_s,
                            dry_run=dry_run,
                            sleep=sleep,
                        )
                    )
                except Exception as exc:
                    # Record honestly, then STOP claiming new
                    # dispatches: a dead lease connection or an
                    # unavailable database must not keep sending.
                    reports.append(RunnerCycleReport(interrupted=type(exc).__name__))
                    break
                cycle += 1
                if max_cycles is not None and cycle >= max_cycles:
                    break
                if stop is not None and stop():
                    break
                sleep(poll_interval_s)
    except DispatchLeaseBusy:
        return [RunnerCycleReport(interrupted="lease_busy")]
    except Exception as exc:
        # Lease-acquire phase database failure: record, don't send.
        reports.append(RunnerCycleReport(interrupted=type(exc).__name__))
        return reports
    return reports


def main() -> int:
    """Standalone entry: ``python -m app.modules.alerts.notifications.runner``.

    Inert by default: with no channels configured every cycle is a
    no-op scan. Real sending requires explicit channel configuration
    AND a specific human authorization. KeyboardInterrupt (Ctrl-C /
    SIGINT) stops cleanly; the attempt table carries all state a
    restart needs. SIGTERM ends the process directly — every COMPLETED
    attempt is already persisted, so a restart resumes without
    zeroing the budget.
    """
    from app.core.db import engine

    @contextmanager
    def _factory() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    try:
        run_dispatch_worker(_factory)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
