from fastapi import APIRouter, Depends

from app.api.ingestion_auth import guard_alert_ingestion
from app.api.routes import items, login, private, users, utils
from app.core.config import settings
from app.modules.alerts.routes import router as alerts_router
from app.modules.iot.binding_routes import router as device_binding_router
from app.modules.iot.control_routes import router as device_control_router
from app.modules.iot.routes import router as devices_router
from app.modules.telemetry.http_candidate import router as telemetry_router

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(devices_router)
api_router.include_router(device_binding_router)
api_router.include_router(alerts_router, dependencies=[Depends(guard_alert_ingestion)])
api_router.include_router(device_control_router)
api_router.include_router(telemetry_router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
