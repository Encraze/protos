import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.identity.models import Tenant
from app.security.models import ApiKeyMode, Provider, ProviderKey
from app.security.service import issue_api_key
from app.security.vault import build_vault


async def _seed_tenant(engine, *, with_provider: Provider | None = Provider.OPENAI) -> str:
    settings = get_settings()
    vault = build_vault(settings)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        tenant = Tenant(slug="acme", name="Acme")
        db.add(tenant)
        await db.flush()
        _, plaintext = await issue_api_key(
            db,
            tenant_id=tenant.id,
            created_by_user_id=None,
            name="probe",
            scopes=["chat:write"],
            mode=ApiKeyMode.LIVE,
        )
        if with_provider is not None:
            db.add(
                ProviderKey(
                    tenant_id=tenant.id,
                    provider=with_provider,
                    label="default",
                    encrypted_secret=vault.encrypt("sk-x"),
                    last_four="sk-x",
                )
            )
        await db.commit()
    return plaintext


@pytest.mark.asyncio
async def test_chat_completions_no_bearer_returns_401(client, engine):
    await _seed_tenant(engine)
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_chat_completions_unsupported_model_returns_400(client, engine):
    token = await _seed_tenant(engine)
    r = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "foobar", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "unsupported_model"


@pytest.mark.asyncio
async def test_chat_completions_no_provider_key_returns_400(client, engine):
    token = await _seed_tenant(engine, with_provider=None)
    r = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "provider_key_missing"


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_upstream_5xx_retries_then_unavailable(client, engine):
    token = await _seed_tenant(engine)
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(503)
    )
    r = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "upstream_unavailable"


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_upstream_401_returns_502_request_error(client, engine):
    token = await _seed_tenant(engine)
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad"}})
    )
    r = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "upstream_request_error"
