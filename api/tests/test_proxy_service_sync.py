import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.identity.models import Tenant
from app.proxy.results import ProxyResult
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


@pytest.mark.asyncio
@respx.mock
async def test_sync_chat_completion_runs_end_to_end(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    api_key, _token = await _seed(sm)
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "cmpl-x",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            },
        )
    )

    completed: list[ProxyResult] = []

    async def hook(result: ProxyResult) -> None:
        completed.append(result)

    settings = get_settings()
    service = ProxyService(settings)
    service.on_completed.append(hook)

    async with sm() as db:
        result, response = await service.chat_completion_sync(
            db=db,
            api_key=api_key,
            request_body={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert response["choices"][0]["message"]["content"] == "hi"
    assert response["id"] == result.request_id
    assert result.status == UsageStatus.SUCCESS
    assert result.streamed is False
    assert result.prompt_tokens == 5
    assert result.completion_tokens == 1
    assert completed and completed[0].request_id == result.request_id


@pytest.mark.asyncio
async def test_sync_chat_completion_unsupported_model_raises(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    api_key, _ = await _seed(sm)
    settings = get_settings()
    service = ProxyService(settings)

    from app.proxy.errors import UnsupportedModelError

    async with sm() as db:
        with pytest.raises(UnsupportedModelError):
            await service.chat_completion_sync(
                db=db,
                api_key=api_key,
                request_body={
                    "model": "foobar",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )


@pytest.mark.asyncio
async def test_sync_chat_completion_provider_key_missing_raises(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    api_key, _ = await _seed(sm)
    settings = get_settings()
    service = ProxyService(settings)
    from app.proxy.errors import ProviderKeyMissingError

    async with sm() as db:
        with pytest.raises(ProviderKeyMissingError):
            await service.chat_completion_sync(
                db=db,
                api_key=api_key,
                request_body={
                    "model": "claude-opus-4-7",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
