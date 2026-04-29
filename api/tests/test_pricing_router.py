import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.identity.models import Membership, Role, Tenant


async def _seed_admin_membership(sm) -> None:
    async with sm() as db:
        from sqlalchemy import select

        from app.identity.models import User

        user = (await db.execute(select(User))).scalars().first()
        assert user is not None
        tenant = Tenant(slug="acme", name="Acme")
        db.add(tenant)
        await db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.ADMIN))
        await db.commit()


@pytest.mark.asyncio
@respx.mock
async def test_get_pricing_catalog_empty(authed_client, engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_admin_membership(sm)
    r = await authed_client.get("/v1/admin/pricing-catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "unavailable"
    assert body["rates"] == []


@pytest.mark.asyncio
@respx.mock
async def test_post_pricing_catalog_refresh_success(authed_client, engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_admin_membership(sm)
    settings = get_settings()
    respx.get(settings.openrouter_models_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "openai/gpt-4o",
                        "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
                        "top_provider": {"id": "openai"},
                    }
                ]
            },
        )
    )
    r = await authed_client.post("/v1/admin/pricing-catalog/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "openrouter"
    assert any(
        rate["provider"] == "openai" and rate["model"] == "gpt-4o" for rate in body["rates"]
    )


@pytest.mark.asyncio
async def test_get_pricing_catalog_unauthenticated_returns_401(client):
    r = await client.get("/v1/admin/pricing-catalog")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_pricing_catalog_non_admin_returns_403(authed_client, engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        from sqlalchemy import update
        await db.execute(update(Membership).values(role=Role.MEMBER))
        await db.commit()
    r = await authed_client.get("/v1/admin/pricing-catalog")
    assert r.status_code == 403
