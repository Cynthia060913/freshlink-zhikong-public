"""Administrator-only HTTP ingestion until service credentials are available.

Internal A/C service calls have their own identity checks. A browser read role
does not grant permission to manufacture durable device or telemetry events.
"""

from fastapi import HTTPException, Request

from app.api.deps import CurrentUser
from app.core.config import settings


def guard_alert_ingestion(request: Request, actor: CurrentUser) -> None:
    write_paths = {
        f"{settings.API_V1_STR}/candidates",
        f"{settings.API_V1_STR}/device-events",
    }
    if (
        request.method == "POST"
        and request.url.path in write_paths
        and not actor.is_superuser
    ):
        raise HTTPException(
            status_code=403, detail="Alert ingestion requires an administrator"
        )
