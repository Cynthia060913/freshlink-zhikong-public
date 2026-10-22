"""A-owned bridge to B's real warehouse service, using the caller's Session.

Only an actual not-found result means False. Database failures must propagate
so HTTP callers can return 503 and workers can stop/retry, never accept a
phantom warehouse. No cache or default-True integration fallback is used.
"""

from uuid import UUID

from sqlmodel import Session

from app.modules.alerts.services.warehouses import WarehouseNotFound, get_warehouse
from app.modules.iot.registry import WarehouseExists


def warehouse_lookup_for_session(session: Session) -> WarehouseExists:
    def exists(identity: UUID) -> bool:
        try:
            get_warehouse(session, identity)
        except WarehouseNotFound:
            return False
        return True

    return exists
