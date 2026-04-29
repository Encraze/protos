from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth.router import router as auth_router
from app.auth.seed import seed_stub_users
from app.config import get_settings
from app.db import get_sessionmaker
from app.health.router import router as health_router
from app.logging import configure_logging
from app.security.router import router as security_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.auth_mode == "stub" and settings.auth_seed:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as db:
            await seed_stub_users(db)
    if settings.pricing_refresh_on_startup:
        from app.pricing.dependencies import get_vendor_catalog

        try:
            await get_vendor_catalog(settings).refresh()
        except Exception:
            pass
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(title="Protos LLM Gateway", version="0.1.0", lifespan=lifespan)

    from app.pricing.router import router as pricing_router
    from app.proxy.dependencies import gateway_error_handler
    from app.proxy.errors import GatewayError
    from app.proxy.router import router as proxy_router

    app.add_exception_handler(GatewayError, gateway_error_handler)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(security_router)
    app.include_router(pricing_router)
    app.include_router(proxy_router)
    return app


app = create_app()
