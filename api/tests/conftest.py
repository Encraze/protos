import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base, get_session


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    monkeypatch.setenv(
        "APP_DATABASE_URL",
        os.environ.get(
            "APP_TEST_DATABASE_URL",
            "postgresql+asyncpg://protos:protos@postgres:5432/protos_test",
        ),
    )
    monkeypatch.setenv("APP_ENVIRONMENT", "test")


@pytest_asyncio.fixture
async def engine():
    from app.config import get_settings
    from app import models as _models  # noqa: F401

    get_settings.cache_clear()
    eng = create_async_engine(get_settings().database_url, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s


@pytest_asyncio.fixture
async def client(engine) -> AsyncIterator[AsyncClient]:
    from app.main import app

    sm = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
