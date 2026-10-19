"""B module — warehouses / batches / batch rule snapshots models.

Model split:

- :class:`Warehouse` — physical cold-chain warehouse (cold room);
- :class:`Batch` — a batch of perishable goods stored in a warehouse
  for a bounded time window;
- :class:`BatchRuleSnapshot` — an immutable list of rule dicts that
  govern what counts as an alert for one batch. Snapshots are
  append-only: once a row is committed, no UPDATE or DELETE is
  permitted by the service layer. New versions land as a brand-new
  row; "current snapshot" is simply the latest ``created_at`` for
  the ``batch_id``. No ``is_current`` / ``superseded_by`` columns.

This package deliberately avoids coupling to FastAPI or SMTP so it
can be imported by alembic env.py and by tests without dragging in
HTTP dependencies.
"""

from .batch import (
    Batch,
    BatchCreate,
    BatchPublic,
)
from .event import (
    AlertEvent,
    AlertEventPublic,
    AlertStatus,
    AlertStatusTransition,
    CandidateCreate,
    CandidateKind,
    CandidateReceipt,
    DeviceEventCreate,
    DeviceEventKind,
    NotificationAttempt,
    NotificationAttemptPublic,
    ReceiptStatus,
    canonical_payload_json,
    compute_content_digest,
)
from .rule import (
    BatchRuleSnapshot,
    BatchRuleSnapshotCreate,
    BatchRuleSnapshotPublic,
    RecoverySpec,
    RuleDict,
    RuleSeverity,
    compute_snapshot_signature,
)
from .warehouse import (
    Warehouse,
    WarehouseCreate,
    WarehousePublic,
)

__all__ = [
    "AlertEvent",
    "AlertEventPublic",
    "AlertStatus",
    "AlertStatusTransition",
    "Batch",
    "BatchCreate",
    "BatchPublic",
    "BatchRuleSnapshot",
    "BatchRuleSnapshotCreate",
    "BatchRuleSnapshotPublic",
    "CandidateCreate",
    "CandidateKind",
    "CandidateReceipt",
    "DeviceEventCreate",
    "DeviceEventKind",
    "NotificationAttempt",
    "NotificationAttemptPublic",
    "ReceiptStatus",
    "RecoverySpec",
    "RuleDict",
    "RuleSeverity",
    "Warehouse",
    "WarehouseCreate",
    "WarehousePublic",
    "canonical_payload_json",
    "compute_content_digest",
    "compute_snapshot_signature",
]
