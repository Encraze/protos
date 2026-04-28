import pytest
from sqlalchemy import select

from app.security.models import ApiKey, ApiKeyMode, ProviderKey
from app.security.service import verify_api_key


@pytest.mark.asyncio
async def test_create_provider_key_returns_last_four_only(authed_client):
    response = await authed_client.post(
        "/v1/provider-keys",
        json={
            "provider": "openai",
            "label": "prod-openai",
            "secret": "sk-openai-1234567890abcdef-tail",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["last_four"] == "tail"
    assert body["label"] == "prod-openai"
    assert "secret" not in body
    assert "encrypted_secret" not in body


@pytest.mark.asyncio
async def test_provider_key_secret_is_recoverable_through_vault(authed_client, session):
    create = await authed_client.post(
        "/v1/provider-keys",
        json={
            "provider": "anthropic",
            "label": "prod-anthropic",
            "secret": "sk-ant-XXXXXXXXXXXXXXXXXXXXXXXX",
        },
    )
    assert create.status_code == 201
    record = (
        await session.execute(select(ProviderKey).where(ProviderKey.label == "prod-anthropic"))
    ).scalar_one()

    from app.security.dependencies import get_vault
    from app.config import get_settings

    vault = get_vault(get_settings())
    assert vault.decrypt(bytes(record.encrypted_secret)) == "sk-ant-XXXXXXXXXXXXXXXXXXXXXXXX"


@pytest.mark.asyncio
async def test_list_provider_keys_returns_only_current_tenant(authed_client, session):
    await authed_client.post(
        "/v1/provider-keys",
        json={"provider": "openai", "label": "carol-key", "secret": "sk-carol-secret-xxx"},
    )

    listing = await authed_client.get("/v1/provider-keys")
    assert listing.status_code == 200
    items = listing.json()
    labels = {item["label"] for item in items}
    assert labels == {"carol-key"}


@pytest.mark.asyncio
async def test_revoke_provider_key_marks_revoked_at(authed_client):
    create = await authed_client.post(
        "/v1/provider-keys",
        json={"provider": "gemini", "label": "to-revoke", "secret": "sk-gemini-revokeme"},
    )
    key_id = create.json()["id"]

    revoke = await authed_client.post(f"/v1/provider-keys/{key_id}/revoke")
    assert revoke.status_code == 200
    assert revoke.json()["revoked_at"] is not None


@pytest.mark.asyncio
async def test_revoke_unknown_provider_key_returns_404(authed_client):
    response = await authed_client.post(
        "/v1/provider-keys/00000000-0000-0000-0000-000000000000/revoke"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_provider_key_endpoints_return_401(client):
    response = await client.get("/v1/provider-keys")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_api_key_returns_token_only_once(authed_client):
    response = await authed_client.post(
        "/v1/api-keys",
        json={"name": "ci-pipeline", "scopes": ["proxy:invoke"], "mode": "live"},
    )
    assert response.status_code == 201
    body = response.json()
    token = body["token"]
    assert token.startswith("pk_live_")
    assert body["prefix"] == token[:16]
    assert body["scopes"] == ["proxy:invoke"]

    listing = await authed_client.get("/v1/api-keys")
    items = listing.json()
    assert len(items) == 1
    assert "token" not in items[0]


@pytest.mark.asyncio
async def test_verify_api_key_succeeds_for_active_token(authed_client, session):
    issue = await authed_client.post(
        "/v1/api-keys",
        json={"name": "test-key", "mode": "sandbox"},
    )
    token = issue.json()["token"]
    assert token.startswith("pk_sandbox_")

    record = await verify_api_key(session, token)
    assert record is not None
    assert record.name == "test-key"
    assert record.mode == ApiKeyMode.SANDBOX


@pytest.mark.asyncio
async def test_verify_api_key_returns_none_for_revoked(authed_client, session):
    issue = await authed_client.post("/v1/api-keys", json={"name": "rev"})
    token = issue.json()["token"]
    key_id = issue.json()["id"]

    await authed_client.post(f"/v1/api-keys/{key_id}/revoke")

    assert await verify_api_key(session, token) is None


@pytest.mark.asyncio
async def test_verify_api_key_returns_none_for_unknown_token(session):
    assert await verify_api_key(session, "pk_live_completelyfaketoken1234") is None


@pytest.mark.asyncio
async def test_verify_api_key_rejects_short_token(session):
    assert await verify_api_key(session, "tiny") is None


@pytest.mark.asyncio
async def test_revoke_api_key_marks_revoked_at(authed_client, session):
    issue = await authed_client.post("/v1/api-keys", json={"name": "revoke-me"})
    key_id = issue.json()["id"]

    revoke = await authed_client.post(f"/v1/api-keys/{key_id}/revoke")
    assert revoke.status_code == 200

    record = await session.get(ApiKey, key_id)
    assert record is not None
    assert record.revoked_at is not None


@pytest.mark.asyncio
async def test_unauthenticated_api_key_endpoints_return_401(client):
    response = await client.get("/v1/api-keys")
    assert response.status_code == 401
