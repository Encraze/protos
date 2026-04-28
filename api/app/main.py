from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth.router import router as auth_router
from app.auth.seed import seed_stub_users
from app.config import get_settings
from app.db import get_sessionmaker
from app.health.router import router as health_router
from app.logging import configure_logging


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.auth_mode == "stub" and settings.auth_seed:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as db:
            await seed_stub_users(db)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(title="Protos LLM Gateway", version="0.1.0", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(auth_router)
    return app


app = create_app()
