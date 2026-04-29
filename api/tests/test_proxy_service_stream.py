import json

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.identity.models import Tenant
from app.proxy.results import AbortReason, ChunkContext, ChunkDecision, ProxyResult
from app.proxy.service import ProxyService
from app.security.models import ApiKey, ApiKeyMode, Provider, ProviderKey
from app.security.service import issue_api_key
from app.security.vault import build_vault
from app.usage.models import UsageStatus


async def _seed(sm) -> tuple[ApiKey, str]:
    settings = get_settings()
    vault = build_vault(settings)
    async with sm() as db:
        tenant = Tenant(slug="acme", name="Acme")
        db.add(tenant)
        await db.flush()
        record, plaintext = await issue_api_key(
            db,
            tenant_id=tenant.id,
            created_by_user_id=None,
            name="probe",
            scopes=["chat:write"],
            mode=ApiKeyMode.LIVE,
        )
        pk = ProviderKey(
            tenant_id=tenant.id,
            provider=Provider.OPENAI,
            label="default",
            encrypted_secret=vault.encrypt("sk-tenant"),
            last_four="nant",
        )
        db.add(pk)
        await db.commit()
        return record, plaintext


def _sse_body(parts: list[str]) -> str:
    out = ""
    for text in parts:
        out += "data: " + json.dumps(
            {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
        ) + "\n\n"
    out += "data: " + json.dumps(
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    ) + "\n\n"
    out += "data: [DONE]\n\n"
    return out


@pytest.mark.asyncio
@respx.mock
async def test_streaming_completes_and_emits_proxy_result(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    api_key, _ = await _seed(sm)
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, text=_sse_body(["He", "llo"]), headers={"content-type": "text/event-stream"}
        )
    )

    completed: list[ProxyResult] = []

    async def on_done(r: ProxyResult) -> None:
        completed.append(r)

    settings = get_settings()
    service = ProxyService(settings)
    service.on_completed.append(on_done)

    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    async with sm() as db:
        provider, secret = await service.prepare_stream(
            db=db, api_key=api_key, request_body=body
        )
    chunks: list[bytes] = []
    async for chunk in service.chat_completion_stream(
        api_key=api_key,
        request_body=body,
        provider=provider,
        upstream_secret=secret,
    ):
        chunks.append(chunk)
    text = b"".join(chunks).decode()
    assert "Hello" in "".join(
        json.loads(line[len("data: "):]).get("choices", [{}])[0].get("delta", {}).get("content", "")
        for line in text.splitlines()
        if line.startswith("data: ") and not line.endswith("[DONE]")
    )
    assert completed and completed[0].streamed is True
    assert completed[0].status == UsageStatus.SUCCESS


@pytest.mark.asyncio
@respx.mock
async def test_streaming_aborts_on_chunk_hook(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    api_key, _ = await _seed(sm)
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, text=_sse_body(["He", "llo", " world"]), headers={"content-type": "text/event-stream"}
        )
    )

    seen: list[ProxyResult] = []

    async def on_done(r: ProxyResult) -> None:
        seen.append(r)

    async def block_after_first(ctx: ChunkContext) -> ChunkDecision:
        if ctx.completion_tokens >= 1:
            return AbortReason(kind="banned_term", message="contains 'world'", error_code="banned_term")
        return "continue"

    settings = get_settings()
    service = ProxyService(settings)
    service.on_completed.append(on_done)
    service.on_chunk.append(block_after_first)

    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    async with sm() as db:
        provider, secret = await service.prepare_stream(
            db=db, api_key=api_key, request_body=body
        )
    out: list[bytes] = []
    async for chunk in service.chat_completion_stream(
        api_key=api_key,
        request_body=body,
        provider=provider,
        upstream_secret=secret,
    ):
        out.append(chunk)
    raw = b"".join(out).decode()
    assert "banned_term" in raw or '"error"' in raw
    assert seen and seen[0].status == UsageStatus.BLOCKED
