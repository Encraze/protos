from fastapi import FastAPI

from app.config import get_settings
from app.health.router import router as health_router
from app.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(title="Protos LLM Gateway", version="0.1.0")
    app.include_router(health_router)
    return app


app = create_app()
