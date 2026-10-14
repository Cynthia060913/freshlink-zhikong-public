from fastapi import APIRouter

from app.api.routes import items, login, private, users, utils
from app.core.config import settings
from app.modules.iot.routes import router as devices_router

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(devices_router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
