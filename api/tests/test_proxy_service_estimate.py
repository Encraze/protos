from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.identity.models import Tenant
from app.pricing.dependencies import get_vendor_catalog
from app.pricing.vendor_catalog import CatalogSnapshot, VendorRate
from app.proxy.service import ProxyService
from app.security.models import ApiKeyMode, Provider, ProviderKey
from app.security.service import issue_api_key
from app.security.vault import build_vault


async def _seed_provider_key(sm, *, provider: Provider, secret: str = "sk-x"):
    settings = get_settings()
    vault = build_vault(settings)
    async with sm() as db:
        tenant = Tenant(slug="acme", name="Acme")
        db.add(tenant)
        await db.flush()
        api_key, _ = await issue_api_key(
            db,
            tenant_id=tenant.id,
            created_by_user_id=None,
            name="probe",
            scopes=["chat:write"],
            mode=ApiKeyMode.LIVE,
        )
        db.add(
            ProviderKey(
                tenant_id=tenant.id,
                provider=provider,
                label="default",
                encrypted_secret=vault.encrypt(secret),
                last_four=secret[-4:],
            )
        )
        await db.commit()
        return api_key


@pytest.mark.asyncio
@respx.mock
async def test_estimate_openai_returns_tokens_and_cost(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    api_key = await _seed_provider_key(sm, provider=Provider.OPENAI)
    settings = get_settings()
    catalog = get_vendor_catalog(settings)
    catalog._snapshot = CatalogSnapshot(
        rates={(Provider.OPENAI, "gpt-4o"): VendorRate(2, 10)},
        source="openrouter",
        fetched_at=datetime.now(UTC),
        version="t",
    )
    service = ProxyService(settings)
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hello world"}],
        "max_tokens": 100,
    }
    async with sm() as db:
        out = await service.estimate(db=db, api_key=api_key, request_body=body)
    assert out["provider"] == "openai"
    assert out["input_tokens"] > 0
    assert out["input_cost_micros"] > 0
    assert out["max_output_cost_micros"] == 100 * 10
    assert out["max_output_tokens"] == 100
    assert out["currency"] == "USD"
    assert out["pricing_source"] == "openrouter"


@pytest.mark.asyncio
@respx.mock
async def test_estimate_anthropic_calls_count_api(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    api_key = await _seed_provider_key(sm, provider=Provider.ANTHROPIC, secret="ant-key")
    respx.post("https://api.anthropic.com/v1/messages/count_tokens").mock(
        return_value=httpx.Response(200, json={"input_tokens": 99})
    )
    settings = get_settings()
    catalog = get_vendor_catalog(settings)
    catalog._snapshot = CatalogSnapshot(
        rates={(Provider.ANTHROPIC, "claude-opus-4-7"): VendorRate(15, 75)},
        source="openrouter",
        fetched_at=datetime.now(UTC),
        version="t",
    )
    service = ProxyService(settings)
    body = {
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
    }
    async with sm() as db:
        out = await service.estimate(db=db, api_key=api_key, request_body=body)
    assert out["input_tokens"] == 99
    assert out["input_cost_micros"] == 99 * 15
    assert "max_output_cost_micros" not in out or out["max_output_cost_micros"] is None


@pytest.mark.asyncio
async def test_estimate_returns_null_cost_when_catalog_missing(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    api_key = await _seed_provider_key(sm, provider=Provider.OPENAI)
    settings = get_settings()
    catalog = get_vendor_catalog(settings)
    catalog._snapshot = CatalogSnapshot()
    service = ProxyService(settings)
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 100,
    }
    async with sm() as db:
        out = await service.estimate(db=db, api_key=api_key, request_body=body)
    assert out["input_cost_micros"] is None
    assert out["pricing_source"] == "unavailable"
