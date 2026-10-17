"""B module — alerts routes package.

Contract with A (PR #10 review round 2 + A comment 6110998037):

- The aggregated :data:`router` defined here is what A mounts in
  ``backend/app/api/main.py`` with a **prefix-free**
  ``api_router.include_router(alerts_router)``.
- The global ``/api/v1`` prefix is applied **exactly once**, at the
  outermost layer: ``backend/app/main.py`` already does
  ``app.include_router(api_router, prefix=settings.API_V1_STR)``.
  Passing ``prefix="/api/v1"`` again here would duplicate it
  (``/api/v1/api/v1/...``) — B must never do that.
- **B never writes the global prefix inside these sub-routers.**
  Concretely: no ``prefix="/api/v1"`` anywhere under
  ``app/modules/alerts/routes/`` and no occurrence of the literal
  ``api/v1`` or ``API_V1_STR`` in any file under
  ``app/modules/alerts/``.
- Current aggregated sub-routers: warehouses / batches /
  rule-snapshots / candidates + device-events (F03/F04 ingestion,
  five-way receipts) / notification attempts listing. **Not**
  implemented yet: rule evaluation → notification dispatch, so
  mounting this router does not mean alerts fire automatically.
- If a future endpoint ever needs to deviate from this contract,
  it must be flagged in the PR description and re-reviewed by A
  before merge.

Each submodule defines its own ``APIRouter`` instance; this file
only aggregates them.
"""

from __future__ import annotations

from fastapi import APIRouter

from .batches import router as batches_router
from .events import router as events_router
from .rule_snapshots import router as rule_snapshots_router
from .warehouses import router as warehouse_router

router = APIRouter()
router.include_router(warehouse_router)
router.include_router(batches_router)
router.include_router(rule_snapshots_router)
router.include_router(events_router)

__all__ = ["router"]
