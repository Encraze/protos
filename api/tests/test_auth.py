from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.providers.stub import SEEDED_USERS
from app.auth.seed import seed_stub_users
from app.db import get_session
from app.identity.models import Membership, OAuthAccount, Session, Tenant, User


@pytest_asyncio.fixture
async def proxied_client(monkeypatch, engine):
    monkeypatch.setenv("APP_API_PUBLIC_URL", "http://browser.test/api")
    from app.config import get_settings

    get_settings.cache_clear()
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


def _state_from_redirect(location: str) -> str:
    qs = parse_qs(urlparse(location).query)
    return qs["state"][0]


@pytest.mark.asyncio
async def test_login_start_redirects_to_stub_picker(client):
    response = await client.get("/auth/google/start")
    assert response.status_code == 302
    assert response.headers["location"].startswith("/auth/stub/picker?")
    assert "provider=google" in response.headers["location"]
    assert "protos_oauth_state" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_picker_lists_seeded_users_for_provider(client):
    start = await client.get("/auth/google/start")
    response = await client.get(start.headers["location"])
    assert response.status_code == 200
    body = response.text
    assert "alice@protos.dev" in body
    assert "bob@protos.dev" in body
    assert "carol@protos.dev" in body
    assert "dave@protos.dev" not in body


@pytest.mark.asyncio
async def test_picker_lists_only_github_users_for_github(client):
    start = await client.get("/auth/github/start")
    response = await client.get(start.headers["location"])
    assert response.status_code == 200
    body = response.text
    assert "dave@protos.dev" in body
    assert "alice@protos.dev" not in body


@pytest.mark.asyncio
async def test_callback_creates_user_session_and_default_tenant(client, session):
    start = await client.get("/auth/google/start")
    state = _state_from_redirect(start.headers["location"])

    callback = await client.get(
        f"/auth/google/callback?code=carol-g-1&state={state}"
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "http://test-frontend"
    assert "protos_session" in callback.headers.get("set-cookie", "")

    user = (
        await session.execute(select(User).where(User.email == "carol@protos.dev"))
    ).scalar_one()
    assert user.name == "Carol"

    oauth = (
        await session.execute(
            select(OAuthAccount).where(OAuthAccount.user_id == user.id)
        )
    ).scalar_one()
    assert oauth.provider == "google"
    assert oauth.provider_user_id == "carol-g-1"

    membership = (
        await session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    tenant = await session.get(Tenant, membership.tenant_id)
    assert tenant is not None
    assert tenant.name == "carol's workspace"
    assert membership.role.value == "owner"

    sessions = (
        await session.execute(select(Session).where(Session.user_id == user.id))
    ).scalars().all()
    assert len(sessions) == 1


@pytest.mark.asyncio
async def test_callback_returns_existing_user_without_new_tenant(client, session):
    await seed_stub_users(session)

    user_before = (
        await session.execute(select(User).where(User.email == "alice@protos.dev"))
    ).scalar_one()
    initial_tenants = (await session.execute(select(Tenant))).scalars().all()

    start = await client.get("/auth/google/start")
    state = _state_from_redirect(start.headers["location"])
    callback = await client.get(
        f"/auth/google/callback?code=alice-g-1&state={state}"
    )
    assert callback.status_code == 302

    user_after = (
        await session.execute(select(User).where(User.email == "alice@protos.dev"))
    ).scalar_one()
    assert user_after.id == user_before.id

    final_tenants = (await session.execute(select(Tenant))).scalars().all()
    assert len(final_tenants) == len(initial_tenants)


@pytest.mark.asyncio
async def test_callback_rejects_state_mismatch(client):
    await client.get("/auth/google/start")
    response = await client.get(
        "/auth/google/callback?code=carol-g-1&state=tampered"
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_callback_rejects_missing_state_cookie(client):
    response = await client.get(
        "/auth/google/callback?code=carol-g-1&state=anything"
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_me_returns_user_and_memberships_for_valid_session(client):
    start = await client.get("/auth/google/start")
    state = _state_from_redirect(start.headers["location"])
    await client.get(f"/auth/google/callback?code=carol-g-1&state={state}")

    response = await client.get("/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "carol@protos.dev"
    assert body["name"] == "Carol"
    assert len(body["memberships"]) == 1
    assert body["memberships"][0]["role"] == "owner"


@pytest.mark.asyncio
async def test_me_returns_401_without_cookie(client):
    response = await client.get("/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_session_and_clears_cookie(client):
    start = await client.get("/auth/google/start")
    state = _state_from_redirect(start.headers["location"])
    await client.get(f"/auth/google/callback?code=carol-g-1&state={state}")

    me_before = await client.get("/auth/me")
    assert me_before.status_code == 200

    logout = await client.post("/auth/logout")
    assert logout.status_code == 204

    me_after = await client.get("/auth/me")
    assert me_after.status_code == 401


@pytest.mark.asyncio
async def test_redirect_target_includes_public_url_prefix_when_set(proxied_client):
    response = await proxied_client.get("/auth/google/start")
    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "http://browser.test/api/auth/stub/picker?"
    )


@pytest.mark.asyncio
async def test_picker_links_include_public_url_prefix_when_set(proxied_client):
    start = await proxied_client.get("/auth/google/start")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    picker = await proxied_client.get(
        f"/auth/stub/picker?provider=google&state={state}"
    )
    assert picker.status_code == 200
    assert 'href="http://browser.test/api/auth/google/callback?' in picker.text


@pytest.mark.asyncio
async def test_seed_is_idempotent(session):
    await seed_stub_users(session)
    users_first = (await session.execute(select(User))).scalars().all()
    tenants_first = (await session.execute(select(Tenant))).scalars().all()

    await seed_stub_users(session)
    users_second = (await session.execute(select(User))).scalars().all()
    tenants_second = (await session.execute(select(Tenant))).scalars().all()

    assert len(users_first) == len(users_second) == len(SEEDED_USERS)
    assert len(tenants_first) == len(tenants_second) == 1
