import json
from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.identity.models import Tenant
from app.pricing.dependencies import get_vendor_catalog
from app.pricing.vendor_catalog import CatalogSnapshot, VendorRate
from app.security.models import ApiKeyMode, Provider, ProviderKey
from app.security.service import issue_api_key
from app.security.vault import build_vault


async def _seed_tenant_with_keys(engine) -> str:
    settings = get_settings()
    vault = build_vault(settings)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    secrets_map = {
        Provider.OPENAI: "sk-openai",
        Provider.ANTHROPIC: "sk-ant",
        Provider.GEMINI: "sk-gem",
    }
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
        for prov, sec in secrets_map.items():
            db.add(
                ProviderKey(
                    tenant_id=tenant.id,
                    provider=prov,
                    label="default",
                    encrypted_secret=vault.encrypt(sec),
                    last_four=sec[-4:],
                )
            )
        await db.commit()
    return plaintext


def _seed_catalog():
    settings = get_settings()
    catalog = get_vendor_catalog(settings)
    catalog._snapshot = CatalogSnapshot(
        rates={
            (Provider.OPENAI, "gpt-4o"): VendorRate(2, 10),
            (Provider.ANTHROPIC, "claude-opus-4-7"): VendorRate(15, 75),
            (Provider.GEMINI, "gemini-1.5-pro"): VendorRate(1, 5),
        },
        source="openrouter",
        fetched_at=datetime.now(UTC),
        version="t",
    )


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_openai_sync(client, engine):
    token = await _seed_tenant_with_keys(engine)
    _seed_catalog()
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "cmpl",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "Hi!"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            },
        )
    )
    r = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "Hi!"
    assert r.headers.get("x-request-id")


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_openai_stream(client, engine):
    token = await _seed_tenant_with_keys(engine)
    _seed_catalog()
    sse = (
        "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "Hi"}, "finish_reason": None}]}) + "\n\n"
        "data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + "\n\n"
        "data: [DONE]\n\n"
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    )
    r = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    )
    assert r.status_code == 200
    text = r.text
    assert "Hi" in text
    assert "[DONE]" in text


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_anthropic_sync(client, engine):
    token = await _seed_tenant_with_keys(engine)
    _seed_catalog()
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4-7",
                "content": [{"type": "text", "text": "Yo"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )
    )
    r = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 50,
        },
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Yo"


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_gemini_sync(client, engine):
    token = await _seed_tenant_with_keys(engine)
    _seed_catalog()
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"role": "model", "parts": [{"text": "Hey"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {
                    "promptTokenCount": 2,
                    "candidatesTokenCount": 1,
                    "totalTokenCount": 3,
                },
            },
        )
    )
    r = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hey"


@pytest.mark.asyncio
@respx.mock
async def test_estimate_openai(client, engine):
    token = await _seed_tenant_with_keys(engine)
    _seed_catalog()
    r = await client.post(
        "/v1/estimate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hello world"}],
            "max_tokens": 100,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "openai"
    assert body["input_tokens"] > 0
    assert body["max_output_cost_micros"] == 100 * 10
