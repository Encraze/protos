from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_session
from app.identity.models import Tenant
from app.proxy.dependencies import gateway_error_handler, require_api_key
from app.proxy.errors import GatewayError
from app.security.models import ApiKey, ApiKeyMode
from app.security.service import issue_api_key


def _build_app(scope: str):
    app = FastAPI()
    app.add_exception_handler(GatewayError, gateway_error_handler)

    @app.get("/probe")
    async def probe(api_key: ApiKey = Depends(require_api_key(scope=scope))):
        return {"id": str(api_key.id), "tenant_id": str(api_key.tenant_id)}

    return app


async def _seed_tenant_and_key(
    sm, *, scopes=("chat:write",), expires_at=None, revoke=False
):
    async with sm() as db:
        tenant = Tenant(slug="acme", name="Acme")
        db.add(tenant)
        await db.flush()
        record, plaintext = await issue_api_key(
            db,
            tenant_id=tenant.id,
            created_by_user_id=None,
            name="probe",
            scopes=list(scopes),
            mode=ApiKeyMode.LIVE,
            expires_at=expires_at,
        )
        if revoke:
            record.revoked_at = datetime.now(UTC)
        await db.commit()
        return tenant.id, plaintext, record.id


@pytest.mark.asyncio
async def test_valid_bearer_with_required_scope_passes(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    _, token, key_id = await _seed_tenant_and_key(sm, scopes=["chat:write"])
    app = _build_app("chat:write")

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["id"] == str(key_id)


@pytest.mark.asyncio
async def test_missing_bearer_returns_401(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    app = _build_app("chat:write")

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/probe")
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_invalid_bearer_returns_401(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    app = _build_app("chat:write")

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/probe", headers={"Authorization": "Bearer pk_live_garbage"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_revoked_bearer_returns_401(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    _, token, _ = await _seed_tenant_and_key(sm, revoke=True)
    app = _build_app("chat:write")

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_expired_bearer_returns_401(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    past = datetime.now(UTC) - timedelta(hours=1)
    _, token, _ = await _seed_tenant_and_key(sm, expires_at=past)
    app = _build_app("chat:write")

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bearer_without_required_scope_returns_403(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    _, token, _ = await _seed_tenant_and_key(sm, scopes=["usage:read"])
    app = _build_app("chat:write")

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert r.json()["error"]["type"] == "insufficient_scope"
