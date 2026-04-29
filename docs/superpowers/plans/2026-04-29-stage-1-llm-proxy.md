# Stage 1 — Unified LLM Proxy (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **User rule (auto-memory):** the user handles all git commits manually. **Do not run `git commit` or `git add` followed by commit in any task.** Each task ends when its tests pass. The user will commit when they choose.

**Goal:** Ship `POST /v1/chat/completions` (sync + SSE) and `POST /v1/estimate` over OpenAI / Anthropic / Gemini, OpenAI-wire-compatible, with bearer auth + scopes, retry/timeout/cancellation, vendor pricing catalog, and typed extension points for phase 5/7.

**Architecture:** Three new packages under `api/app/`: `proxy/` (routes, service, adapters, errors, retry, streaming), `pricing/` (in-memory vendor catalog + admin), `tokenizers/` (per-provider input counting). All upstream HTTP via raw httpx; no provider SDKs. ProxyService produces a typed `ProxyResult` per request and exposes empty `on_completed` / `on_chunk` hook lists for downstream phases.

**Tech Stack:** Python 3.12, FastAPI, async SQLAlchemy, httpx, Pydantic v2, anyio, structlog, tiktoken, pytest + pytest-asyncio + respx, Alembic. Reference spec: `docs/superpowers/specs/2026-04-29-stage-1-llm-proxy-design.md`.

**Conventions used in this plan:**
- File paths are absolute relative to the repo root (`api/app/...`, `api/tests/...`).
- Test commands assume `cd api` and a running Postgres (the test DB is already configured by `api/tests/conftest.py` against `postgres:5432/protos_test` from the docker-compose stack — run tests inside the `api` container or with the stack up).
- "Run: `pytest …`" means run the whole test file unless a specific node id is given.
- Each task ends at "tests pass". Commits are the user's call.

---

## Task 1: Bootstrap — dependencies, settings, package skeletons

**Files:**
- Modify: `api/pyproject.toml`
- Create: `api/app/proxy/__init__.py`, `api/app/proxy/adapters/__init__.py`, `api/app/pricing/__init__.py`, `api/app/tokenizers/__init__.py`
- Modify: `api/app/config.py`

- [ ] **Step 1.1: Add deps to `api/pyproject.toml`**

In the `dependencies` array, add:
```
"tiktoken>=0.8,<0.9",
```

In `[project.optional-dependencies].dev` array, add:
```
"respx>=0.21,<0.23",
```

- [ ] **Step 1.2: Create empty package init files**

Create four empty files:
```
api/app/proxy/__init__.py
api/app/proxy/adapters/__init__.py
api/app/pricing/__init__.py
api/app/tokenizers/__init__.py
```

Each file is empty (zero bytes).

- [ ] **Step 1.3: Add Settings entries in `api/app/config.py`**

In the `Settings` class (after `aws_region`), add:

```python
    proxy_timeout_connect_seconds: float = 5.0
    proxy_timeout_read_seconds: float = 60.0
    proxy_timeout_total_seconds: float = 120.0
    proxy_max_retries: int = 2
    proxy_retry_initial_backoff_ms: int = 250
    proxy_sse_keepalive_seconds: float = 15.0

    pricing_source_priority: list[str] = ["openrouter", "litellm"]
    pricing_refresh_on_startup: bool = True
    openrouter_models_url: str = "https://openrouter.ai/api/v1/models"
    litellm_pricing_url: str = (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/"
        "model_prices_and_context_window_backup.json"
    )

    anthropic_count_tokens_cache_ttl_seconds: int = 300
    gemini_count_tokens_cache_ttl_seconds: int = 300
```

- [ ] **Step 1.4: Install deps**

Run inside the api container or your local venv:
```
cd api && pip install -e ".[dev]"
```

Expected: tiktoken and respx install without conflicts.

- [ ] **Step 1.5: Verify smoke**

Run:
```
cd api && pytest tests/test_health.py -v
```

Expected: PASS (no behavior changed yet; just confirming the new config fields don't break startup).

---

## Task 2: Migration — backfill `api_keys.scopes` and update default

**Files:**
- Create: `api/alembic/versions/<new>_backfill_api_key_scopes.py` (Alembic generates the slug)
- Modify: `api/app/security/schemas.py`

- [ ] **Step 2.1: Generate the migration**

Run:
```
cd api && alembic revision -m "backfill api_key scopes default"
```

Expected: a new file in `api/alembic/versions/` is created.

- [ ] **Step 2.2: Edit the migration body**

Replace the generated `upgrade()` and `downgrade()` with:

```python
from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.execute(
        """
        UPDATE api_keys
        SET scopes = ARRAY['chat:write']::varchar[]
        WHERE scopes = '{}'::varchar[] OR scopes IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE api_keys
        SET scopes = '{}'::varchar[]
        WHERE scopes = ARRAY['chat:write']::varchar[]
        """
    )
```

- [ ] **Step 2.3: Update `ApiKeyCreate.scopes` default in `api/app/security/schemas.py`**

Change:
```python
class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list)
    mode: ApiKeyMode = ApiKeyMode.LIVE
    expires_at: datetime | None = None
```

To:
```python
class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["chat:write"])
    mode: ApiKeyMode = ApiKeyMode.LIVE
    expires_at: datetime | None = None
```

- [ ] **Step 2.4: Run migration up + tests**

Run:
```
cd api && alembic upgrade head
cd api && pytest tests/test_security.py -v
```

Expected: migration applies; all existing security tests still pass. If any test asserts `scopes == []` on a created key, update it to assert `["chat:write"]`.

---

## Task 3: Errors module + OpenAI envelope mapper

**Files:**
- Create: `api/app/proxy/errors.py`
- Create: `api/tests/test_proxy_errors.py`

- [ ] **Step 3.1: Write the test for envelope shape**

Create `api/tests/test_proxy_errors.py`:

```python
import pytest

from app.proxy.errors import (
    AuthenticationError,
    GatewayError,
    GatewayTimeoutError,
    InsufficientScopeError,
    InvalidRequestError,
    ProviderKeyMissingError,
    UnsupportedModelError,
    UpstreamRequestError,
    UpstreamUnavailableError,
    error_envelope,
)


def test_envelope_shape_for_authentication_error():
    err = AuthenticationError("missing bearer token")
    body, status_code = error_envelope(err)
    assert status_code == 401
    assert body == {
        "error": {
            "message": "missing bearer token",
            "type": "authentication_error",
            "code": None,
            "param": None,
        }
    }


def test_envelope_shape_for_unsupported_model_includes_supported_list():
    err = UnsupportedModelError("foo-bar", supported=["gpt-4o", "claude-opus-4-7"])
    body, status_code = error_envelope(err)
    assert status_code == 400
    assert body["error"]["type"] == "unsupported_model"
    assert "foo-bar" in body["error"]["message"]
    assert body["error"]["param"] == "model"


@pytest.mark.parametrize(
    "exc_cls,expected_type,expected_status",
    [
        (lambda: InsufficientScopeError(required="chat:write"), "insufficient_scope", 403),
        (lambda: ProviderKeyMissingError(provider="openai"), "provider_key_missing", 400),
        (lambda: InvalidRequestError("bad body"), "invalid_request_error", 400),
        (lambda: UpstreamRequestError("upstream said 401"), "upstream_request_error", 502),
        (lambda: UpstreamUnavailableError("retries exhausted"), "upstream_unavailable", 502),
        (lambda: GatewayTimeoutError("upstream timed out"), "gateway_timeout", 504),
    ],
)
def test_envelope_for_typed_errors(exc_cls, expected_type, expected_status):
    body, status_code = error_envelope(exc_cls())
    assert status_code == expected_status
    assert body["error"]["type"] == expected_type


def test_envelope_for_unknown_exception_is_internal_error():
    body, status_code = error_envelope(RuntimeError("oops"))
    assert status_code == 500
    assert body["error"]["type"] == "internal_error"
    assert body["error"]["message"] == "internal error"
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `cd api && pytest tests/test_proxy_errors.py -v`
Expected: ImportError on `app.proxy.errors`.

- [ ] **Step 3.3: Implement `app/proxy/errors.py`**

```python
from __future__ import annotations

from typing import Any


class GatewayError(Exception):
    error_type: str = "internal_error"
    http_status: int = 500
    code: str | None = None
    param: str | None = None

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(GatewayError):
    error_type = "authentication_error"
    http_status = 401


class InsufficientScopeError(GatewayError):
    error_type = "insufficient_scope"
    http_status = 403

    def __init__(self, *, required: str) -> None:
        super().__init__(f"token lacks required scope: {required}")
        self.code = required


class InvalidRequestError(GatewayError):
    error_type = "invalid_request_error"
    http_status = 400


class UnsupportedModelError(GatewayError):
    error_type = "unsupported_model"
    http_status = 400
    param = "model"

    def __init__(self, model: str, *, supported: list[str]) -> None:
        super().__init__(
            f"model '{model}' is not supported; supported models: {', '.join(supported)}"
        )
        self.supported = supported


class ProviderKeyMissingError(GatewayError):
    error_type = "provider_key_missing"
    http_status = 400

    def __init__(self, *, provider: str) -> None:
        super().__init__(f"no active provider key for provider '{provider}'")
        self.code = provider


class UpstreamRequestError(GatewayError):
    error_type = "upstream_request_error"
    http_status = 502


class UpstreamUnavailableError(GatewayError):
    error_type = "upstream_unavailable"
    http_status = 502


class GatewayTimeoutError(GatewayError):
    error_type = "gateway_timeout"
    http_status = 504


def error_envelope(exc: BaseException) -> tuple[dict[str, Any], int]:
    if isinstance(exc, GatewayError):
        return (
            {
                "error": {
                    "message": exc.message,
                    "type": exc.error_type,
                    "code": exc.code,
                    "param": exc.param,
                }
            },
            exc.http_status,
        )
    return (
        {
            "error": {
                "message": "internal error",
                "type": "internal_error",
                "code": None,
                "param": None,
            }
        },
        500,
    )
```

- [ ] **Step 3.4: Run test to verify it passes**

Run: `cd api && pytest tests/test_proxy_errors.py -v`
Expected: PASS.

---

## Task 4: Bearer auth dependency

**Files:**
- Create: `api/app/proxy/dependencies.py`
- Create: `api/tests/test_proxy_dependencies.py`

- [ ] **Step 4.1: Write the test**

Create `api/tests/test_proxy_dependencies.py`:

```python
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_session
from app.identity.models import Tenant
from app.proxy.dependencies import require_api_key
from app.security.models import ApiKey, ApiKeyMode
from app.security.service import issue_api_key


def _build_app(scope: str):
    app = FastAPI()

    @app.get("/probe")
    async def probe(api_key: ApiKey = Depends(require_api_key(scope=scope))):
        return {"id": str(api_key.id), "tenant_id": str(api_key.tenant_id)}

    return app


async def _seed_tenant_and_key(
    sm, *, scopes: list[str] = ("chat:write",), expires_at=None, revoke=False
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
            record.revoked_at = datetime.now(timezone.utc)
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
    past = datetime.now(timezone.utc) - timedelta(hours=1)
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
```

- [ ] **Step 4.2: Verify it fails**

Run: `cd api && pytest tests/test_proxy_dependencies.py -v`
Expected: ImportError on `app.proxy.dependencies`.

- [ ] **Step 4.3: Implement `app/proxy/dependencies.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Awaitable, Callable

from fastapi import BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.db import get_session
from app.proxy.errors import AuthenticationError, GatewayError, InsufficientScopeError, error_envelope
from app.security.models import ApiKey
from app.security.service import verify_api_key


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


async def _touch_last_used(api_key_id: uuid.UUID, settings: Settings) -> None:
    eng = create_async_engine(settings.database_url, future=True)
    try:
        sm = async_sessionmaker(eng, expire_on_commit=False)
        async with sm() as db:
            row = await db.get(ApiKey, api_key_id)
            if row is None:
                return
            row.last_used_at = datetime.now(timezone.utc)
            await db.commit()
    finally:
        await eng.dispose()


def require_api_key(*, scope: str) -> Callable[..., Awaitable[ApiKey]]:
    async def _dep(
        request: Request,
        background: BackgroundTasks,
        db: Annotated[AsyncSession, Depends(get_session)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> ApiKey:
        token = _extract_bearer(request)
        if token is None:
            raise AuthenticationError("missing bearer token")
        api_key = await verify_api_key(db, token)
        if api_key is None:
            raise AuthenticationError("invalid bearer token")
        if scope not in (api_key.scopes or []):
            raise InsufficientScopeError(required=scope)
        background.add_task(_touch_last_used, api_key.id, settings)
        return api_key

    return _dep


async def gateway_error_handler(_request: Request, exc: GatewayError) -> JSONResponse:
    body, status_code = error_envelope(exc)
    return JSONResponse(content=body, status_code=status_code)
```

- [ ] **Step 4.4: Register the exception handler in the test app**

Modify `_build_app` in `api/tests/test_proxy_dependencies.py` so the test app installs the handler. Replace `_build_app`:

```python
def _build_app(scope: str):
    from app.proxy.dependencies import gateway_error_handler
    from app.proxy.errors import GatewayError

    app = FastAPI()
    app.add_exception_handler(GatewayError, gateway_error_handler)

    @app.get("/probe")
    async def probe(api_key: ApiKey = Depends(require_api_key(scope=scope))):
        return {"id": str(api_key.id), "tenant_id": str(api_key.tenant_id)}

    return app
```

- [ ] **Step 4.5: Verify all six tests pass**

Run: `cd api && pytest tests/test_proxy_dependencies.py -v`
Expected: 6 PASS.

---

## Task 5: Vendor pricing catalog — types, sources, singleton

**Files:**
- Create: `api/app/pricing/vendor_catalog.py`
- Create: `api/app/pricing/sources.py`
- Create: `api/tests/test_vendor_catalog.py`

- [ ] **Step 5.1: Write the test**

Create `api/tests/test_vendor_catalog.py`:

```python
import json

import httpx
import pytest
import respx

from app.config import get_settings
from app.pricing.sources import LiteLLMJsonSource, OpenRouterSource
from app.pricing.vendor_catalog import VendorCatalog, VendorRate
from app.security.models import Provider


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_source_parses_known_models():
    settings = get_settings()
    body = {
        "data": [
            {
                "id": "openai/gpt-4o",
                "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
                "top_provider": {"id": "openai"},
            },
            {
                "id": "anthropic/claude-opus-4-7",
                "pricing": {"prompt": "0.000015", "completion": "0.000075"},
                "top_provider": {"id": "anthropic"},
            },
            {
                "id": "google/gemini-1.5-pro",
                "pricing": {"prompt": "0.00000125", "completion": "0.000005"},
                "top_provider": {"id": "google"},
            },
            {
                "id": "mistralai/something",
                "pricing": {"prompt": "0.0000005", "completion": "0.0000015"},
                "top_provider": {"id": "mistralai"},
            },
        ]
    }
    respx.get(settings.openrouter_models_url).mock(
        return_value=httpx.Response(200, json=body)
    )
    src = OpenRouterSource(settings)
    rates = await src.fetch()
    assert (Provider.OPENAI, "gpt-4o") in rates
    assert rates[(Provider.OPENAI, "gpt-4o")] == VendorRate(2, 10)
    assert rates[(Provider.ANTHROPIC, "claude-opus-4-7")] == VendorRate(15, 75)
    assert rates[(Provider.GEMINI, "gemini-1.5-pro")] == VendorRate(1, 5)
    # Mistral dropped (not in our enum).
    assert all(p in (Provider.OPENAI, Provider.ANTHROPIC, Provider.GEMINI) for (p, _) in rates)


@pytest.mark.asyncio
@respx.mock
async def test_litellm_source_parses_known_models():
    settings = get_settings()
    body = {
        "gpt-4o": {
            "input_cost_per_token": 0.0000025,
            "output_cost_per_token": 0.00001,
            "litellm_provider": "openai",
        },
        "claude-opus-4-7": {
            "input_cost_per_token": 0.000015,
            "output_cost_per_token": 0.000075,
            "litellm_provider": "anthropic",
        },
        "gemini-1.5-pro": {
            "input_cost_per_token": 0.00000125,
            "output_cost_per_token": 0.000005,
            "litellm_provider": "vertex_ai-language-models",
        },
        "ignored-model": {
            "input_cost_per_token": 0.0,
            "output_cost_per_token": 0.0,
            "litellm_provider": "cohere",
        },
        "sample_spec": {"description": "metadata key, not a model"},
    }
    respx.get(settings.litellm_pricing_url).mock(
        return_value=httpx.Response(200, json=body)
    )
    src = LiteLLMJsonSource(settings)
    rates = await src.fetch()
    assert rates[(Provider.OPENAI, "gpt-4o")] == VendorRate(2, 10)
    assert rates[(Provider.ANTHROPIC, "claude-opus-4-7")] == VendorRate(15, 75)
    assert rates[(Provider.GEMINI, "gemini-1.5-pro")] == VendorRate(1, 5)


@pytest.mark.asyncio
@respx.mock
async def test_catalog_refresh_uses_openrouter_first_then_falls_back():
    settings = get_settings()
    respx.get(settings.openrouter_models_url).mock(side_effect=httpx.ConnectError("boom"))
    respx.get(settings.litellm_pricing_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "gpt-4o": {
                    "input_cost_per_token": 0.0000025,
                    "output_cost_per_token": 0.00001,
                    "litellm_provider": "openai",
                }
            },
        )
    )
    cat = VendorCatalog(settings)
    snap = await cat.refresh()
    assert snap.source == "litellm"
    assert cat.get(Provider.OPENAI, "gpt-4o") == VendorRate(2, 10)


@pytest.mark.asyncio
@respx.mock
async def test_catalog_all_sources_fail_keeps_empty():
    settings = get_settings()
    respx.get(settings.openrouter_models_url).mock(side_effect=httpx.ConnectError("a"))
    respx.get(settings.litellm_pricing_url).mock(side_effect=httpx.ConnectError("b"))
    cat = VendorCatalog(settings)
    snap = await cat.refresh()
    assert snap.source == "unavailable"
    assert cat.get(Provider.OPENAI, "gpt-4o") is None


@pytest.mark.asyncio
@respx.mock
async def test_catalog_keeps_prior_rates_when_refresh_fails():
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
    cat = VendorCatalog(settings)
    await cat.refresh()
    assert cat.get(Provider.OPENAI, "gpt-4o") == VendorRate(2, 10)
    # Now both sources fail.
    respx.get(settings.openrouter_models_url).mock(side_effect=httpx.ConnectError("a"))
    respx.get(settings.litellm_pricing_url).mock(side_effect=httpx.ConnectError("b"))
    snap = await cat.refresh()
    assert snap.source == "openrouter"  # source from the prior successful snapshot
    assert cat.get(Provider.OPENAI, "gpt-4o") == VendorRate(2, 10)
```

- [ ] **Step 5.2: Verify it fails**

Run: `cd api && pytest tests/test_vendor_catalog.py -v`
Expected: ImportError.

- [ ] **Step 5.3: Implement `app/pricing/vendor_catalog.py`**

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import structlog

from app.config import Settings
from app.security.models import Provider

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class VendorRate:
    input_micros_per_token: int
    output_micros_per_token: int


PricingSource = Literal["openrouter", "litellm", "unavailable"]


@dataclass
class CatalogSnapshot:
    rates: dict[tuple[Provider, str], VendorRate] = field(default_factory=dict)
    source: PricingSource = "unavailable"
    fetched_at: datetime | None = None
    version: str | None = None


class VendorCatalog:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._snapshot = CatalogSnapshot()
        self._lock = asyncio.Lock()

    def get(self, provider: Provider, model: str) -> VendorRate | None:
        return self._snapshot.rates.get((provider, model))

    def snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    async def refresh(self) -> CatalogSnapshot:
        # Imported here to avoid circulars.
        from app.pricing.sources import LiteLLMJsonSource, OpenRouterSource

        sources_by_name = {
            "openrouter": OpenRouterSource(self._settings),
            "litellm": LiteLLMJsonSource(self._settings),
        }
        async with self._lock:
            for name in self._settings.pricing_source_priority:
                src = sources_by_name.get(name)
                if src is None:
                    continue
                try:
                    rates = await src.fetch()
                except Exception as exc:
                    logger.warning("pricing.source.failed", source=name, error=str(exc))
                    continue
                if not rates:
                    logger.warning("pricing.source.empty", source=name)
                    continue
                snap = CatalogSnapshot(
                    rates=rates,
                    source=name,  # type: ignore[arg-type]
                    fetched_at=datetime.now(timezone.utc),
                    version=src.version(),
                )
                self._snapshot = snap
                logger.info(
                    "pricing.refreshed",
                    source=name,
                    rate_count=len(rates),
                    version=snap.version,
                )
                return snap
            if self._snapshot.rates:
                logger.warning(
                    "pricing.refresh.all_failed_keeping_prior",
                    prior_source=self._snapshot.source,
                )
                return self._snapshot
            self._snapshot = CatalogSnapshot()
            logger.error("pricing.refresh.all_failed_no_prior")
            return self._snapshot
```

- [ ] **Step 5.4: Implement `app/pricing/sources.py`**

```python
from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from app.config import Settings
from app.pricing.vendor_catalog import VendorRate
from app.security.models import Provider

_OPENROUTER_PROVIDER_MAP = {
    "openai": Provider.OPENAI,
    "anthropic": Provider.ANTHROPIC,
    "google": Provider.GEMINI,
    "google-vertex": Provider.GEMINI,
    "google-ai-studio": Provider.GEMINI,
}

_LITELLM_PROVIDER_MAP = {
    "openai": Provider.OPENAI,
    "anthropic": Provider.ANTHROPIC,
    "gemini": Provider.GEMINI,
    "vertex_ai-language-models": Provider.GEMINI,
    "vertex_ai": Provider.GEMINI,
    "google": Provider.GEMINI,
}


def _dollars_to_micros(price: str | float | int) -> int:
    if isinstance(price, str):
        return int(Decimal(price) * Decimal(1_000_000))
    return int(Decimal(str(price)) * Decimal(1_000_000))


class OpenRouterSource:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._etag: str | None = None

    def version(self) -> str | None:
        return self._etag

    async def fetch(self) -> dict[tuple[Provider, str], VendorRate]:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(self._settings.openrouter_models_url)
            r.raise_for_status()
        self._etag = r.headers.get("etag")
        body: dict[str, Any] = r.json()
        rates: dict[tuple[Provider, str], VendorRate] = {}
        for entry in body.get("data", []):
            full_id = entry.get("id") or ""
            if "/" not in full_id:
                continue
            vendor_str, _, model = full_id.partition("/")
            top_provider = (entry.get("top_provider") or {}).get("id") or vendor_str
            provider = _OPENROUTER_PROVIDER_MAP.get(top_provider.lower())
            if provider is None:
                continue
            pricing = entry.get("pricing") or {}
            try:
                rates[(provider, model)] = VendorRate(
                    input_micros_per_token=_dollars_to_micros(pricing["prompt"]),
                    output_micros_per_token=_dollars_to_micros(pricing["completion"]),
                )
            except (KeyError, ValueError, TypeError):
                continue
        return rates


class LiteLLMJsonSource:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._version: str | None = None

    def version(self) -> str | None:
        return self._version

    async def fetch(self) -> dict[tuple[Provider, str], VendorRate]:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(self._settings.litellm_pricing_url)
            r.raise_for_status()
        self._version = r.headers.get("etag") or r.headers.get("last-modified")
        body: dict[str, Any] = r.json()
        rates: dict[tuple[Provider, str], VendorRate] = {}
        for model, entry in body.items():
            if not isinstance(entry, dict):
                continue
            ll_provider = entry.get("litellm_provider")
            if not isinstance(ll_provider, str):
                continue
            provider = _LITELLM_PROVIDER_MAP.get(ll_provider)
            if provider is None:
                continue
            try:
                rates[(provider, model)] = VendorRate(
                    input_micros_per_token=_dollars_to_micros(entry["input_cost_per_token"]),
                    output_micros_per_token=_dollars_to_micros(entry["output_cost_per_token"]),
                )
            except (KeyError, ValueError, TypeError):
                continue
        return rates
```

- [ ] **Step 5.5: Verify the tests pass**

Run: `cd api && pytest tests/test_vendor_catalog.py -v`
Expected: 5 PASS.

---

## Task 6: Pricing admin router + lifespan integration

**Files:**
- Create: `api/app/pricing/router.py`
- Create: `api/app/pricing/dependencies.py`
- Modify: `api/app/main.py`
- Create: `api/tests/test_pricing_router.py`

- [ ] **Step 6.1: Write the test**

Create `api/tests/test_pricing_router.py`:

```python
import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.identity.models import Membership, Role, Tenant


async def _seed_admin(authed_client, sm) -> None:
    async with sm() as db:
        # The authed_client fixture has already created carol-g and a default
        # session. We just need to ensure carol-g has an admin membership in a
        # tenant.
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
    await _seed_admin(authed_client, sm)
    r = await authed_client.get("/v1/admin/pricing-catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "unavailable"
    assert body["rates"] == []


@pytest.mark.asyncio
@respx.mock
async def test_post_pricing_catalog_refresh_success(authed_client, engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_admin(authed_client, sm)
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
        from sqlalchemy import select
        from app.identity.models import User

        user = (await db.execute(select(User))).scalars().first()
        assert user is not None
        tenant = Tenant(slug="acme", name="Acme")
        db.add(tenant)
        await db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.MEMBER))
        await db.commit()
    r = await authed_client.get("/v1/admin/pricing-catalog")
    assert r.status_code == 403
```

- [ ] **Step 6.2: Verify it fails**

Run: `cd api && pytest tests/test_pricing_router.py -v`
Expected: 404 / ImportError.

- [ ] **Step 6.3: Implement `app/pricing/dependencies.py`**

```python
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_user
from app.config import Settings, get_settings
from app.db import get_session
from app.identity.models import Membership, Role, User
from app.pricing.vendor_catalog import VendorCatalog

_catalog: VendorCatalog | None = None


def get_vendor_catalog(
    settings: Annotated[Settings, Depends(get_settings)],
) -> VendorCatalog:
    global _catalog
    if _catalog is None:
        _catalog = VendorCatalog(settings)
    return _catalog


def reset_vendor_catalog() -> None:
    global _catalog
    _catalog = None


async def require_admin_membership(
    user: Annotated[User, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Membership:
    stmt = (
        select(Membership)
        .where(Membership.user_id == user.id)
        .order_by(Membership.created_at)
    )
    memberships = list((await db.execute(stmt)).scalars())
    for m in memberships:
        if m.role in (Role.OWNER, Role.ADMIN):
            return m
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="admin role required",
    )
```

- [ ] **Step 6.4: Implement `app/pricing/router.py`**

```python
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.identity.models import Membership
from app.pricing.dependencies import get_vendor_catalog, require_admin_membership
from app.pricing.vendor_catalog import CatalogSnapshot, VendorCatalog
from app.security.models import Provider

router = APIRouter(prefix="/v1/admin/pricing-catalog", tags=["pricing-admin"])


class RateOut(BaseModel):
    provider: Provider
    model: str
    input_micros_per_token: int
    output_micros_per_token: int


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    source: str
    fetched_at: str | None
    version: str | None
    rates: list[RateOut]


def _serialize(snap: CatalogSnapshot) -> SnapshotOut:
    return SnapshotOut(
        source=snap.source,
        fetched_at=snap.fetched_at.isoformat() if snap.fetched_at else None,
        version=snap.version,
        rates=sorted(
            (
                RateOut(
                    provider=p,
                    model=m,
                    input_micros_per_token=r.input_micros_per_token,
                    output_micros_per_token=r.output_micros_per_token,
                )
                for (p, m), r in snap.rates.items()
            ),
            key=lambda x: (x.provider.value, x.model),
        ),
    )


@router.get("", response_model=SnapshotOut)
async def get_catalog(
    _admin: Annotated[Membership, Depends(require_admin_membership)],
    catalog: Annotated[VendorCatalog, Depends(get_vendor_catalog)],
) -> SnapshotOut:
    return _serialize(catalog.snapshot())


@router.post("/refresh", response_model=SnapshotOut)
async def refresh_catalog(
    _admin: Annotated[Membership, Depends(require_admin_membership)],
    catalog: Annotated[VendorCatalog, Depends(get_vendor_catalog)],
) -> SnapshotOut:
    snap = await catalog.refresh()
    if snap.source == "unavailable" and not snap.rates:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="all pricing sources failed and no prior snapshot exists",
        )
    return _serialize(snap)
```

- [ ] **Step 6.5: Wire router and lifespan in `app/main.py`**

Replace the body of `lifespan` and `create_app`:

```python
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.auth_mode == "stub" and settings.auth_seed:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as db:
            await seed_stub_users(db)
    if settings.pricing_refresh_on_startup:
        from app.pricing.dependencies import get_vendor_catalog

        try:
            await get_vendor_catalog(settings).refresh()
        except Exception:
            pass
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(title="Protos LLM Gateway", version="0.1.0", lifespan=lifespan)

    from app.proxy.dependencies import gateway_error_handler
    from app.proxy.errors import GatewayError
    from app.pricing.router import router as pricing_router

    app.add_exception_handler(GatewayError, gateway_error_handler)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(security_router)
    app.include_router(pricing_router)
    return app
```

- [ ] **Step 6.6: Reset catalog singleton between tests**

Modify `api/tests/conftest.py`'s `engine` fixture: in addition to `reset_vault()`, also call `reset_vendor_catalog()`. Add the import:

```python
from app.pricing.dependencies import reset_vendor_catalog
```

In the fixture body, after `reset_vault()`:
```python
reset_vendor_catalog()
```

- [ ] **Step 6.7: Verify tests pass**

Run: `cd api && pytest tests/test_pricing_router.py tests/test_vendor_catalog.py tests/test_proxy_dependencies.py tests/test_proxy_errors.py -v`
Expected: all PASS.

---

## Task 7: Tokenizers

**Files:**
- Create: `api/app/tokenizers/openai_tiktoken.py`
- Create: `api/app/tokenizers/anthropic_count.py`
- Create: `api/app/tokenizers/gemini_count.py`
- Create: `api/tests/test_tokenizers.py`

- [ ] **Step 7.1: Write the test**

Create `api/tests/test_tokenizers.py`:

```python
import httpx
import pytest
import respx

from app.config import get_settings
from app.tokenizers.anthropic_count import AnthropicCounter
from app.tokenizers.gemini_count import GeminiCounter
from app.tokenizers.openai_tiktoken import count_openai_input_tokens

OPENAI_MESSAGES = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hello, world!"},
]


def test_openai_tokenizer_known_model_returns_positive_count():
    n = count_openai_input_tokens(model="gpt-4o", messages=OPENAI_MESSAGES)
    assert n > 0


def test_openai_tokenizer_unknown_model_falls_back_and_returns_count():
    n = count_openai_input_tokens(model="gpt-9000-future", messages=OPENAI_MESSAGES)
    assert n > 0


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_counter_calls_api_and_caches():
    settings = get_settings()
    route = respx.post("https://api.anthropic.com/v1/messages/count_tokens").mock(
        return_value=httpx.Response(200, json={"input_tokens": 42})
    )
    counter = AnthropicCounter(settings)
    n1 = await counter.count(
        model="claude-opus-4-7", messages=OPENAI_MESSAGES, upstream_secret="ant-key"
    )
    n2 = await counter.count(
        model="claude-opus-4-7", messages=OPENAI_MESSAGES, upstream_secret="ant-key"
    )
    assert n1 == n2 == 42
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_gemini_counter_calls_api_and_caches():
    settings = get_settings()
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:countTokens"
    ).mock(return_value=httpx.Response(200, json={"totalTokens": 17}))
    counter = GeminiCounter(settings)
    n1 = await counter.count(
        model="gemini-1.5-pro", messages=OPENAI_MESSAGES, upstream_secret="gem-key"
    )
    n2 = await counter.count(
        model="gemini-1.5-pro", messages=OPENAI_MESSAGES, upstream_secret="gem-key"
    )
    assert n1 == n2 == 17
    assert route.call_count == 1
```

- [ ] **Step 7.2: Verify it fails**

Run: `cd api && pytest tests/test_tokenizers.py -v`
Expected: ImportError.

- [ ] **Step 7.3: Implement `app/tokenizers/openai_tiktoken.py`**

```python
from __future__ import annotations

from typing import Iterable

import tiktoken


def _encoding_for(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_openai_input_tokens(
    *, model: str, messages: Iterable[dict[str, object]]
) -> int:
    enc = _encoding_for(model)
    # Conservative count: encode role + content for each message + small overhead.
    total = 0
    for m in messages:
        role = str(m.get("role") or "")
        content = m.get("content")
        total += len(enc.encode(role)) + 4
        if isinstance(content, str):
            total += len(enc.encode(content))
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        total += len(enc.encode(text))
    total += 2
    return total
```

- [ ] **Step 7.4: Implement `app/tokenizers/anthropic_count.py`**

```python
from __future__ import annotations

import hashlib
import json
import time
from typing import Iterable

import httpx

from app.config import Settings


class AnthropicCounter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[str, tuple[float, int]] = {}

    @staticmethod
    def _split_system(messages: Iterable[dict[str, object]]) -> tuple[str, list[dict]]:
        system_parts: list[str] = []
        rest: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                content = m.get("content")
                if isinstance(content, str):
                    system_parts.append(content)
            else:
                rest.append(dict(m))
        return ("\n\n".join(system_parts), rest)

    async def count(
        self,
        *,
        model: str,
        messages: Iterable[dict[str, object]],
        upstream_secret: str,
    ) -> int:
        msg_list = list(messages)
        system, body_messages = self._split_system(msg_list)
        payload: dict[str, object] = {"model": model, "messages": body_messages}
        if system:
            payload["system"] = system
        cache_key = self._cache_key(model, payload)
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and now - cached[0] < self._settings.anthropic_count_tokens_cache_ttl_seconds:
            return cached[1]
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages/count_tokens",
                headers={
                    "x-api-key": upstream_secret,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
        n = int(r.json().get("input_tokens", 0))
        self._cache[cache_key] = (now, n)
        return n

    @staticmethod
    def _cache_key(model: str, payload: dict[str, object]) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"{model}:{hashlib.sha256(body).hexdigest()}"
```

- [ ] **Step 7.5: Implement `app/tokenizers/gemini_count.py`**

```python
from __future__ import annotations

import hashlib
import json
import time
from typing import Iterable

import httpx

from app.config import Settings


class GeminiCounter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[str, tuple[float, int]] = {}

    @staticmethod
    def _to_contents(messages: Iterable[dict[str, object]]) -> list[dict]:
        contents: list[dict] = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                continue
            mapped_role = "model" if role == "assistant" else "user"
            content = m.get("content")
            if isinstance(content, str):
                contents.append({"role": mapped_role, "parts": [{"text": content}]})
            elif isinstance(content, list):
                parts = [
                    {"text": part["text"]}
                    for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                ]
                if parts:
                    contents.append({"role": mapped_role, "parts": parts})
        return contents

    async def count(
        self,
        *,
        model: str,
        messages: Iterable[dict[str, object]],
        upstream_secret: str,
    ) -> int:
        msg_list = list(messages)
        payload = {"contents": self._to_contents(msg_list)}
        cache_key = self._cache_key(model, payload)
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and now - cached[0] < self._settings.gemini_count_tokens_cache_ttl_seconds:
            return cached[1]
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:countTokens"
            f"?key={upstream_secret}"
        )
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(url, json=payload)
            r.raise_for_status()
        n = int(r.json().get("totalTokens", 0))
        self._cache[cache_key] = (now, n)
        return n

    @staticmethod
    def _cache_key(model: str, payload: dict[str, object]) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"{model}:{hashlib.sha256(body).hexdigest()}"
```

- [ ] **Step 7.6: Verify tests pass**

Run: `cd api && pytest tests/test_tokenizers.py -v`
Expected: 4 PASS.

---

## Task 8: ProxyResult, ChunkContext, ChunkDecision, registry, adapter base, retry helper

**Files:**
- Create: `api/app/proxy/results.py`
- Create: `api/app/proxy/registry.py`
- Create: `api/app/proxy/adapters/base.py`
- Create: `api/app/proxy/retry.py`
- Create: `api/tests/test_proxy_registry.py`
- Create: `api/tests/test_proxy_retry.py`

- [ ] **Step 8.1: Write tests for registry**

Create `api/tests/test_proxy_registry.py`:

```python
import pytest

from app.proxy.errors import UnsupportedModelError
from app.proxy.registry import resolve_provider, supported_models
from app.security.models import Provider


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gpt-4o", Provider.OPENAI),
        ("gpt-4o-mini", Provider.OPENAI),
        ("o1-preview", Provider.OPENAI),
        ("o3-mini", Provider.OPENAI),
        ("claude-opus-4-7", Provider.ANTHROPIC),
        ("claude-3-5-sonnet-20241022", Provider.ANTHROPIC),
        ("gemini-1.5-pro", Provider.GEMINI),
        ("gemini-2.0-flash", Provider.GEMINI),
    ],
)
def test_resolve_provider_known_models(model, expected):
    assert resolve_provider(model) == expected


def test_resolve_provider_unknown_raises_with_supported_list():
    with pytest.raises(UnsupportedModelError) as ei:
        resolve_provider("foobar-7b")
    assert "foobar-7b" in str(ei.value)
    assert ei.value.supported == supported_models()
    assert "gpt-4o" in ei.value.supported
```

- [ ] **Step 8.2: Write test for retry helper**

Create `api/tests/test_proxy_retry.py`:

```python
import httpx
import pytest

from app.config import get_settings
from app.proxy.errors import GatewayTimeoutError, UpstreamRequestError, UpstreamUnavailableError
from app.proxy.retry import RetryPolicy, with_retries


@pytest.mark.asyncio
async def test_with_retries_returns_first_success():
    calls = {"n": 0}

    async def op() -> int:
        calls["n"] += 1
        return 7

    policy = RetryPolicy.from_settings(get_settings())
    result = await with_retries(op, policy=policy)
    assert result == 7
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_with_retries_retries_5xx_then_succeeds():
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.HTTPStatusError(
                "server error",
                request=httpx.Request("POST", "https://example/"),
                response=httpx.Response(503),
            )
        return "ok"

    policy = RetryPolicy.from_settings(get_settings()).with_overrides(initial_backoff_ms=1)
    result = await with_retries(op, policy=policy)
    assert result == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_with_retries_exhausts_retries_and_raises_unavailable():
    async def op() -> str:
        raise httpx.HTTPStatusError(
            "server error",
            request=httpx.Request("POST", "https://example/"),
            response=httpx.Response(503),
        )

    policy = RetryPolicy.from_settings(get_settings()).with_overrides(
        max_retries=2, initial_backoff_ms=1
    )
    with pytest.raises(UpstreamUnavailableError):
        await with_retries(op, policy=policy)


@pytest.mark.asyncio
async def test_with_retries_does_not_retry_4xx():
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        raise httpx.HTTPStatusError(
            "auth",
            request=httpx.Request("POST", "https://example/"),
            response=httpx.Response(401, json={"error": {"message": "bad key"}}),
        )

    policy = RetryPolicy.from_settings(get_settings()).with_overrides(
        max_retries=2, initial_backoff_ms=1
    )
    with pytest.raises(UpstreamRequestError):
        await with_retries(op, policy=policy)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_with_retries_honors_429_with_retry_after():
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.HTTPStatusError(
                "rl",
                request=httpx.Request("POST", "https://example/"),
                response=httpx.Response(429, headers={"retry-after": "0"}),
            )
        return "ok"

    policy = RetryPolicy.from_settings(get_settings()).with_overrides(
        max_retries=2, initial_backoff_ms=1
    )
    result = await with_retries(op, policy=policy)
    assert result == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_with_retries_429_without_retry_after_is_not_retried():
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        raise httpx.HTTPStatusError(
            "rl",
            request=httpx.Request("POST", "https://example/"),
            response=httpx.Response(429),
        )

    policy = RetryPolicy.from_settings(get_settings()).with_overrides(
        max_retries=2, initial_backoff_ms=1
    )
    with pytest.raises(UpstreamRequestError):
        await with_retries(op, policy=policy)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_with_retries_total_timeout_raises_gateway_timeout():
    import asyncio

    async def op() -> str:
        await asyncio.sleep(0.5)
        return "ok"

    policy = RetryPolicy.from_settings(get_settings()).with_overrides(
        total_timeout_seconds=0.05, initial_backoff_ms=1
    )
    with pytest.raises(GatewayTimeoutError):
        await with_retries(op, policy=policy)
```

- [ ] **Step 8.3: Verify tests fail**

Run: `cd api && pytest tests/test_proxy_registry.py tests/test_proxy_retry.py -v`
Expected: ImportError.

- [ ] **Step 8.4: Implement `app/proxy/registry.py`**

```python
from __future__ import annotations

from app.proxy.errors import UnsupportedModelError
from app.security.models import Provider

_PREFIX_TO_PROVIDER: tuple[tuple[str, Provider], ...] = (
    ("gpt-", Provider.OPENAI),
    ("o1", Provider.OPENAI),
    ("o3", Provider.OPENAI),
    ("o4", Provider.OPENAI),
    ("chatgpt-", Provider.OPENAI),
    ("claude-", Provider.ANTHROPIC),
    ("gemini-", Provider.GEMINI),
)

_KNOWN_MODELS: tuple[str, ...] = (
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "o1",
    "o1-preview",
    "o1-mini",
    "o3-mini",
    "claude-opus-4-7",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-2.0-flash",
)


def supported_models() -> list[str]:
    return list(_KNOWN_MODELS)


def resolve_provider(model: str) -> Provider:
    for prefix, provider in _PREFIX_TO_PROVIDER:
        if model.startswith(prefix):
            return provider
    raise UnsupportedModelError(model, supported=supported_models())
```

- [ ] **Step 8.5: Implement `app/proxy/results.py`**

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Literal

from app.security.models import Provider
from app.usage.models import UsageStatus


@dataclass(frozen=True)
class AbortReason:
    kind: str
    message: str
    error_code: str | None = None


ChunkDecision = Literal["continue"] | AbortReason


@dataclass
class ChunkContext:
    request_id: str
    aggregated_response: dict
    latest_delta: dict
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class ProxyResult:
    request_id: str
    tenant_id: uuid.UUID
    api_key_id: uuid.UUID
    provider: Provider
    model: str
    status: UsageStatus
    streamed: bool
    started_at: datetime
    completed_at: datetime
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    error_code: str | None
    request_body: dict
    response_body: dict | None


OnCompleted = Callable[[ProxyResult], Awaitable[None]]
OnChunk = Callable[[ChunkContext], Awaitable[ChunkDecision]]
```

- [ ] **Step 8.6: Implement `app/proxy/adapters/base.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, ClassVar, Protocol

from app.security.models import Provider


@dataclass(frozen=True)
class TimeoutPolicy:
    connect_seconds: float
    read_seconds: float
    write_seconds: float
    total_seconds: float


@dataclass
class StreamChunk:
    """OpenAI-shape SSE delta payload, plus terminal usage when present."""

    payload: dict


class ProviderAdapter(Protocol):
    provider: ClassVar[Provider]

    async def chat_completion(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> dict: ...

    def chat_completion_stream(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]: ...
```

- [ ] **Step 8.7: Implement `app/proxy/retry.py`**

```python
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, replace
from typing import Awaitable, Callable, TypeVar

import anyio
import httpx

from app.config import Settings
from app.proxy.errors import (
    GatewayTimeoutError,
    UpstreamRequestError,
    UpstreamUnavailableError,
)

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    initial_backoff_ms: int
    total_timeout_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "RetryPolicy":
        return cls(
            max_retries=settings.proxy_max_retries,
            initial_backoff_ms=settings.proxy_retry_initial_backoff_ms,
            total_timeout_seconds=settings.proxy_timeout_total_seconds,
        )

    def with_overrides(self, **kwargs: object) -> "RetryPolicy":
        return replace(self, **kwargs)  # type: ignore[arg-type]


_RETRYABLE_STATUS = {502, 503, 504}


def _retry_after_seconds(exc: httpx.HTTPStatusError) -> float | None:
    header = exc.response.headers.get("retry-after")
    if header is None:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        return None


def _is_retryable(exc: BaseException) -> tuple[bool, float | None]:
    if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError)):
        return True, None
    if isinstance(exc, httpx.ReadTimeout):
        return True, None
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in _RETRYABLE_STATUS:
            return True, None
        if status == 429:
            ra = _retry_after_seconds(exc)
            if ra is not None:
                return True, ra
            return False, None
    return False, None


def _to_gateway_error(exc: BaseException) -> Exception:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in _RETRYABLE_STATUS:
            return UpstreamUnavailableError(f"upstream {status} after retries")
        return UpstreamRequestError(f"upstream returned HTTP {status}")
    if isinstance(exc, httpx.TimeoutException):
        return UpstreamUnavailableError("upstream timeout")
    if isinstance(exc, httpx.HTTPError):
        return UpstreamUnavailableError(f"upstream connection error: {exc}")
    return UpstreamUnavailableError(f"upstream error: {exc}")


async def with_retries(
    op: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
) -> T:
    attempts_allowed = 1 + policy.max_retries
    base = policy.initial_backoff_ms / 1000.0
    try:
        async with anyio.fail_after(policy.total_timeout_seconds):
            last_exc: BaseException | None = None
            for attempt in range(1, attempts_allowed + 1):
                try:
                    return await op()
                except Exception as exc:
                    last_exc = exc
                    retryable, retry_after = _is_retryable(exc)
                    if not retryable or attempt == attempts_allowed:
                        raise _to_gateway_error(exc) from exc
                    backoff = base * (2 ** (attempt - 1))
                    backoff += random.uniform(0, 0.1 * base)
                    if retry_after is not None:
                        backoff = min(backoff, retry_after)
                    await asyncio.sleep(backoff)
            assert last_exc is not None
            raise _to_gateway_error(last_exc)
    except TimeoutError as exc:
        raise GatewayTimeoutError("upstream call exceeded total timeout") from exc
```

- [ ] **Step 8.8: Verify tests pass**

Run: `cd api && pytest tests/test_proxy_registry.py tests/test_proxy_retry.py -v`
Expected: all PASS.

---

## Task 9: OpenAI adapter (sync + stream)

**Files:**
- Create: `api/app/proxy/adapters/openai.py`
- Create: `api/tests/test_adapter_openai.py`

- [ ] **Step 9.1: Write the test**

Create `api/tests/test_adapter_openai.py`:

```python
import json

import httpx
import pytest
import respx

from app.proxy.adapters.base import TimeoutPolicy
from app.proxy.adapters.openai import OpenAIAdapter

POLICY = TimeoutPolicy(connect_seconds=5, read_seconds=60, write_seconds=60, total_seconds=120)


@pytest.mark.asyncio
@respx.mock
async def test_openai_sync_completion_passes_through_body():
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "id": "cmpl-upstream-1",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hi!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        )

    respx.post("https://api.openai.com/v1/chat/completions").mock(side_effect=_handler)
    adapter = OpenAIAdapter()
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hi"}],
        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
    }
    resp = await adapter.chat_completion(
        body, upstream_secret="sk-test", timeout=POLICY, request_id="req-1"
    )
    assert resp["choices"][0]["message"]["content"] == "Hi!"
    assert resp["usage"]["prompt_tokens"] == 10
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["tools"][0]["function"]["name"] == "lookup"


@pytest.mark.asyncio
@respx.mock
async def test_openai_streaming_normalizes_chunks_and_emits_done():
    sse = (
        "data: " + json.dumps({"choices": [{"delta": {"content": "He"}, "index": 0}]}) + "\n\n"
        "data: " + json.dumps({"choices": [{"delta": {"content": "llo"}, "index": 0}]}) + "\n\n"
        "data: " + json.dumps(
            {"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}
        ) + "\n\n"
        "data: [DONE]\n\n"
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    )
    adapter = OpenAIAdapter()
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    chunks = []
    async for chunk in adapter.chat_completion_stream(
        body, upstream_secret="sk-test", timeout=POLICY, request_id="req-1"
    ):
        chunks.append(chunk.payload)
    deltas = [c["choices"][0]["delta"] for c in chunks if "choices" in c]
    text = "".join(d.get("content", "") for d in deltas)
    assert text == "Hello"
    assert any(c["choices"][0].get("finish_reason") == "stop" for c in chunks)


@pytest.mark.asyncio
@respx.mock
async def test_openai_4xx_raises_upstream_request_error():
    from app.proxy.errors import UpstreamRequestError

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    adapter = OpenAIAdapter()
    with pytest.raises(UpstreamRequestError):
        await adapter.chat_completion(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]},
            upstream_secret="sk-bad",
            timeout=POLICY,
            request_id="req-1",
        )
```

- [ ] **Step 9.2: Verify it fails**

Run: `cd api && pytest tests/test_adapter_openai.py -v`
Expected: ImportError.

- [ ] **Step 9.3: Implement `app/proxy/adapters/openai.py`**

```python
from __future__ import annotations

import json
from typing import AsyncIterator, ClassVar

import httpx

from app.proxy.adapters.base import ProviderAdapter, StreamChunk, TimeoutPolicy
from app.proxy.errors import UpstreamRequestError, UpstreamUnavailableError
from app.security.models import Provider


def _httpx_timeout(p: TimeoutPolicy) -> httpx.Timeout:
    return httpx.Timeout(connect=p.connect_seconds, read=p.read_seconds, write=p.write_seconds, pool=p.connect_seconds)


class OpenAIAdapter(ProviderAdapter):
    provider: ClassVar[Provider] = Provider.OPENAI
    _BASE = "https://api.openai.com/v1/chat/completions"

    async def chat_completion(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> dict:
        body = {**request_body}
        body.pop("stream", None)
        async with httpx.AsyncClient(timeout=_httpx_timeout(timeout)) as c:
            r = await c.post(
                self._BASE,
                headers={
                    "authorization": f"Bearer {upstream_secret}",
                    "content-type": "application/json",
                },
                json=body,
            )
            if r.status_code >= 500:
                r.raise_for_status()
            if r.status_code >= 400:
                msg = r.json().get("error", {}).get("message", f"HTTP {r.status_code}")
                raise UpstreamRequestError(msg)
        return r.json()

    async def chat_completion_stream(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]:
        body = {**request_body, "stream": True}
        async with httpx.AsyncClient(timeout=_httpx_timeout(timeout)) as c:
            async with c.stream(
                "POST",
                self._BASE,
                headers={
                    "authorization": f"Bearer {upstream_secret}",
                    "content-type": "application/json",
                    "accept": "text/event-stream",
                },
                json=body,
            ) as r:
                if r.status_code >= 500:
                    raise UpstreamUnavailableError(f"upstream {r.status_code}")
                if r.status_code >= 400:
                    text = (await r.aread()).decode("utf-8", errors="replace")
                    raise UpstreamRequestError(f"upstream {r.status_code}: {text[:200]}")
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        yield StreamChunk(payload=json.loads(payload))
                    except json.JSONDecodeError:
                        continue
```

- [ ] **Step 9.4: Verify tests pass**

Run: `cd api && pytest tests/test_adapter_openai.py -v`
Expected: 3 PASS.

---

## Task 10: Anthropic adapter (sync + stream)

**Files:**
- Create: `api/app/proxy/adapters/anthropic.py`
- Create: `api/tests/test_adapter_anthropic.py`

- [ ] **Step 10.1: Write the test**

Create `api/tests/test_adapter_anthropic.py`:

```python
import json

import httpx
import pytest
import respx

from app.proxy.adapters.anthropic import AnthropicAdapter
from app.proxy.adapters.base import TimeoutPolicy

POLICY = TimeoutPolicy(connect_seconds=5, read_seconds=60, write_seconds=60, total_seconds=120)


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_sync_translates_request_and_response():
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "id": "msg_upstream",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4-7",
                "content": [{"type": "text", "text": "Hi there"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
        )

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=_handler)
    adapter = AnthropicAdapter()
    body = {
        "model": "claude-opus-4-7",
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Hi"},
        ],
        "max_tokens": 100,
    }
    resp = await adapter.chat_completion(
        body, upstream_secret="ant-key", timeout=POLICY, request_id="req-1"
    )
    assert captured["body"]["system"] == "Be concise."
    assert captured["body"]["messages"] == [{"role": "user", "content": "Hi"}]
    assert captured["headers"]["x-api-key"] == "ant-key"
    assert resp["choices"][0]["message"]["content"] == "Hi there"
    assert resp["choices"][0]["finish_reason"] == "stop"
    assert resp["usage"] == {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_streaming_emits_openai_shape_chunks():
    events = (
        "event: message_start\ndata: " + json.dumps(
            {"type": "message_start", "message": {"id": "msg_x", "model": "claude-opus-4-7"}}
        ) + "\n\n"
        "event: content_block_start\ndata: " + json.dumps(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
        ) + "\n\n"
        "event: content_block_delta\ndata: " + json.dumps(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "He"}}
        ) + "\n\n"
        "event: content_block_delta\ndata: " + json.dumps(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "llo"}}
        ) + "\n\n"
        "event: content_block_stop\ndata: " + json.dumps({"type": "content_block_stop", "index": 0}) + "\n\n"
        "event: message_delta\ndata: " + json.dumps(
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}
        ) + "\n\n"
        "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n"
    )
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, text=events, headers={"content-type": "text/event-stream"}
        )
    )
    adapter = AnthropicAdapter()
    body = {
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 100,
    }
    chunks = []
    async for chunk in adapter.chat_completion_stream(
        body, upstream_secret="ant-key", timeout=POLICY, request_id="req-1"
    ):
        chunks.append(chunk.payload)
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c.get("choices"))
    assert text == "Hello"
    finals = [c for c in chunks if c["choices"][0].get("finish_reason") == "stop"]
    assert len(finals) == 1
```

- [ ] **Step 10.2: Verify it fails**

Run: `cd api && pytest tests/test_adapter_anthropic.py -v`
Expected: ImportError.

- [ ] **Step 10.3: Implement `app/proxy/adapters/anthropic.py`**

```python
from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator, ClassVar

import httpx

from app.proxy.adapters.base import ProviderAdapter, StreamChunk, TimeoutPolicy
from app.proxy.errors import UpstreamRequestError, UpstreamUnavailableError
from app.security.models import Provider


def _httpx_timeout(p: TimeoutPolicy) -> httpx.Timeout:
    return httpx.Timeout(connect=p.connect_seconds, read=p.read_seconds, write=p.write_seconds, pool=p.connect_seconds)


_STOP_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}


def _to_anthropic_request(body: dict) -> dict:
    system_parts: list[str] = []
    messages: list[dict] = []
    for m in body.get("messages") or []:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if isinstance(content, str):
                system_parts.append(content)
            continue
        if role == "tool":
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id"),
                            "content": content if isinstance(content, str) else "",
                        }
                    ],
                }
            )
            continue
        messages.append({"role": role, "content": content})
    out: dict = {
        "model": body["model"],
        "messages": messages,
        "max_tokens": body.get("max_tokens", 1024),
    }
    if system_parts:
        out["system"] = "\n\n".join(system_parts)
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        out["top_p"] = body["top_p"]
    if body.get("stop") is not None:
        stop = body["stop"]
        out["stop_sequences"] = stop if isinstance(stop, list) else [stop]
    if body.get("tools"):
        out["tools"] = [
            {
                "name": t.get("function", {}).get("name"),
                "description": t.get("function", {}).get("description", ""),
                "input_schema": t.get("function", {}).get("parameters", {"type": "object"}),
            }
            for t in body["tools"]
            if t.get("type") == "function"
        ]
    if body.get("tool_choice"):
        tc = body["tool_choice"]
        if tc == "auto":
            out["tool_choice"] = {"type": "auto"}
        elif tc == "none":
            out["tool_choice"] = {"type": "none"}
        elif isinstance(tc, dict) and tc.get("type") == "function":
            out["tool_choice"] = {"type": "tool", "name": tc.get("function", {}).get("name")}
    return out


def _from_anthropic_response(model: str, request_id: str, body: dict) -> dict:
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in body.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or str(uuid.uuid4()),
                    "type": "function",
                    "function": {
                        "name": block.get("name"),
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )
    message: dict = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    finish_reason = _STOP_REASON_MAP.get(body.get("stop_reason") or "", "stop")
    usage = body.get("usage") or {}
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": int(usage.get("input_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("input_tokens") or 0)
            + int(usage.get("output_tokens") or 0),
        },
    }


class AnthropicAdapter(ProviderAdapter):
    provider: ClassVar[Provider] = Provider.ANTHROPIC
    _BASE = "https://api.anthropic.com/v1/messages"

    async def chat_completion(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> dict:
        payload = _to_anthropic_request(request_body)
        async with httpx.AsyncClient(timeout=_httpx_timeout(timeout)) as c:
            r = await c.post(
                self._BASE,
                headers={
                    "x-api-key": upstream_secret,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            if r.status_code >= 500:
                r.raise_for_status()
            if r.status_code >= 400:
                msg = r.json().get("error", {}).get("message", f"HTTP {r.status_code}")
                raise UpstreamRequestError(msg)
        return _from_anthropic_response(request_body["model"], request_id, r.json())

    async def chat_completion_stream(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]:
        payload = {**_to_anthropic_request(request_body), "stream": True}
        model = request_body["model"]
        created = int(time.time())
        async with httpx.AsyncClient(timeout=_httpx_timeout(timeout)) as c:
            async with c.stream(
                "POST",
                self._BASE,
                headers={
                    "x-api-key": upstream_secret,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                    "accept": "text/event-stream",
                },
                json=payload,
            ) as r:
                if r.status_code >= 500:
                    raise UpstreamUnavailableError(f"upstream {r.status_code}")
                if r.status_code >= 400:
                    text = (await r.aread()).decode("utf-8", errors="replace")
                    raise UpstreamRequestError(f"upstream {r.status_code}: {text[:200]}")
                async for raw in r.aiter_lines():
                    if not raw or not raw.startswith("data:"):
                        continue
                    body_str = raw[len("data:"):].strip()
                    try:
                        evt = json.loads(body_str)
                    except json.JSONDecodeError:
                        continue
                    etype = evt.get("type")
                    if etype == "content_block_delta":
                        delta = evt.get("delta") or {}
                        text = delta.get("text") if delta.get("type") == "text_delta" else None
                        if text is not None:
                            yield StreamChunk(
                                payload={
                                    "id": request_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model,
                                    "choices": [
                                        {"index": 0, "delta": {"content": text}, "finish_reason": None}
                                    ],
                                }
                            )
                    elif etype == "message_delta":
                        stop_reason = (evt.get("delta") or {}).get("stop_reason")
                        if stop_reason:
                            yield StreamChunk(
                                payload={
                                    "id": request_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model,
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {},
                                            "finish_reason": _STOP_REASON_MAP.get(stop_reason, "stop"),
                                        }
                                    ],
                                }
                            )
                    elif etype == "message_stop":
                        return
```

- [ ] **Step 10.4: Verify tests pass**

Run: `cd api && pytest tests/test_adapter_anthropic.py -v`
Expected: 2 PASS.

---

## Task 11: Gemini adapter (sync + stream)

**Files:**
- Create: `api/app/proxy/adapters/gemini.py`
- Create: `api/tests/test_adapter_gemini.py`

- [ ] **Step 11.1: Write the test**

Create `api/tests/test_adapter_gemini.py`:

```python
import json

import httpx
import pytest
import respx

from app.proxy.adapters.base import TimeoutPolicy
from app.proxy.adapters.gemini import GeminiAdapter

POLICY = TimeoutPolicy(connect_seconds=5, read_seconds=60, write_seconds=60, total_seconds=120)


@pytest.mark.asyncio
@respx.mock
async def test_gemini_sync_translates_request_and_response():
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "Hi back"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 8,
                    "candidatesTokenCount": 3,
                    "totalTokenCount": 11,
                },
            },
        )

    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent"
    ).mock(side_effect=_handler)
    adapter = GeminiAdapter()
    body = {
        "model": "gemini-1.5-pro",
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Hi"},
        ],
    }
    resp = await adapter.chat_completion(
        body, upstream_secret="gem-key", timeout=POLICY, request_id="req-1"
    )
    assert "key=gem-key" in captured["url"]
    assert captured["body"]["systemInstruction"]["parts"][0]["text"] == "Be brief."
    assert captured["body"]["contents"] == [
        {"role": "user", "parts": [{"text": "Hi"}]}
    ]
    assert resp["choices"][0]["message"]["content"] == "Hi back"
    assert resp["usage"] == {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}


@pytest.mark.asyncio
@respx.mock
async def test_gemini_streaming_emits_openai_shape_chunks():
    sse_body = (
        "data: " + json.dumps(
            {
                "candidates": [
                    {"content": {"role": "model", "parts": [{"text": "He"}]}}
                ]
            }
        ) + "\n\n"
        "data: " + json.dumps(
            {
                "candidates": [
                    {"content": {"role": "model", "parts": [{"text": "llo"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {
                    "promptTokenCount": 1,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 3,
                },
            }
        ) + "\n\n"
    )
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:streamGenerateContent"
    ).mock(return_value=httpx.Response(200, text=sse_body, headers={"content-type": "text/event-stream"}))
    adapter = GeminiAdapter()
    body = {
        "model": "gemini-1.5-pro",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    chunks = []
    async for chunk in adapter.chat_completion_stream(
        body, upstream_secret="gem-key", timeout=POLICY, request_id="req-1"
    ):
        chunks.append(chunk.payload)
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c.get("choices"))
    assert text == "Hello"
    finals = [c for c in chunks if c["choices"][0].get("finish_reason") == "stop"]
    assert len(finals) == 1
```

- [ ] **Step 11.2: Verify it fails**

Run: `cd api && pytest tests/test_adapter_gemini.py -v`
Expected: ImportError.

- [ ] **Step 11.3: Implement `app/proxy/adapters/gemini.py`**

```python
from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator, ClassVar

import httpx

from app.proxy.adapters.base import ProviderAdapter, StreamChunk, TimeoutPolicy
from app.proxy.errors import UpstreamRequestError, UpstreamUnavailableError
from app.security.models import Provider


def _httpx_timeout(p: TimeoutPolicy) -> httpx.Timeout:
    return httpx.Timeout(connect=p.connect_seconds, read=p.read_seconds, write=p.write_seconds, pool=p.connect_seconds)


_FINISH_MAP = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "OTHER": "stop",
}


def _to_gemini_request(body: dict) -> tuple[dict, str | None]:
    contents: list[dict] = []
    system_text: list[str] = []
    for m in body.get("messages") or []:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if isinstance(content, str):
                system_text.append(content)
            continue
        mapped_role = "model" if role == "assistant" else "user"
        if isinstance(content, str):
            contents.append({"role": mapped_role, "parts": [{"text": content}]})
        elif isinstance(content, list):
            parts = [
                {"text": p.get("text")}
                for p in content
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            ]
            if parts:
                contents.append({"role": mapped_role, "parts": parts})
    out: dict = {"contents": contents}
    if system_text:
        out["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_text)}]}
    gen_config: dict = {}
    if body.get("temperature") is not None:
        gen_config["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        gen_config["topP"] = body["top_p"]
    if body.get("max_tokens") is not None:
        gen_config["maxOutputTokens"] = body["max_tokens"]
    if body.get("stop") is not None:
        stop = body["stop"]
        gen_config["stopSequences"] = stop if isinstance(stop, list) else [stop]
    if gen_config:
        out["generationConfig"] = gen_config
    if body.get("tools"):
        out["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": t.get("function", {}).get("name"),
                        "description": t.get("function", {}).get("description", ""),
                        "parameters": t.get("function", {}).get("parameters") or {"type": "object"},
                    }
                    for t in body["tools"]
                    if t.get("type") == "function"
                ]
            }
        ]
    return out, body.get("model")


def _from_gemini_response(model: str, request_id: str, body: dict) -> dict:
    candidates = body.get("candidates") or []
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    finish_reason = "stop"
    if candidates:
        cand = candidates[0]
        for part in (cand.get("content") or {}).get("parts") or []:
            if "text" in part:
                text_parts.append(part["text"] or "")
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(
                    {
                        "id": str(uuid.uuid4()),
                        "type": "function",
                        "function": {
                            "name": fc.get("name"),
                            "arguments": json.dumps(fc.get("args") or {}),
                        },
                    }
                )
        finish_reason = _FINISH_MAP.get(cand.get("finishReason") or "STOP", "stop")
    message: dict = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = body.get("usageMetadata") or {}
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": int(usage.get("promptTokenCount") or 0),
            "completion_tokens": int(usage.get("candidatesTokenCount") or 0),
            "total_tokens": int(usage.get("totalTokenCount") or 0),
        },
    }


class GeminiAdapter(ProviderAdapter):
    provider: ClassVar[Provider] = Provider.GEMINI

    @staticmethod
    def _url(model: str, secret: str, *, stream: bool) -> str:
        action = "streamGenerateContent" if stream else "generateContent"
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{action}"
            f"?key={secret}"
        )

    async def chat_completion(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> dict:
        payload, model = _to_gemini_request(request_body)
        assert model is not None
        async with httpx.AsyncClient(timeout=_httpx_timeout(timeout)) as c:
            r = await c.post(
                self._url(model, upstream_secret, stream=False),
                headers={"content-type": "application/json"},
                json=payload,
            )
            if r.status_code >= 500:
                r.raise_for_status()
            if r.status_code >= 400:
                msg = r.json().get("error", {}).get("message", f"HTTP {r.status_code}")
                raise UpstreamRequestError(msg)
        return _from_gemini_response(model, request_id, r.json())

    async def chat_completion_stream(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]:
        payload, model = _to_gemini_request(request_body)
        assert model is not None
        created = int(time.time())
        async with httpx.AsyncClient(timeout=_httpx_timeout(timeout)) as c:
            async with c.stream(
                "POST",
                self._url(model, upstream_secret, stream=True),
                headers={"content-type": "application/json", "accept": "text/event-stream"},
                json=payload,
            ) as r:
                if r.status_code >= 500:
                    raise UpstreamUnavailableError(f"upstream {r.status_code}")
                if r.status_code >= 400:
                    text = (await r.aread()).decode("utf-8", errors="replace")
                    raise UpstreamRequestError(f"upstream {r.status_code}: {text[:200]}")
                async for raw in r.aiter_lines():
                    if not raw or not raw.startswith("data:"):
                        continue
                    body_str = raw[len("data:"):].strip()
                    try:
                        evt = json.loads(body_str)
                    except json.JSONDecodeError:
                        continue
                    candidates = evt.get("candidates") or []
                    if not candidates:
                        continue
                    cand = candidates[0]
                    parts = (cand.get("content") or {}).get("parts") or []
                    text = "".join(p.get("text", "") for p in parts if "text" in p)
                    if text:
                        yield StreamChunk(
                            payload={
                                "id": request_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [
                                    {"index": 0, "delta": {"content": text}, "finish_reason": None}
                                ],
                            }
                        )
                    if cand.get("finishReason"):
                        yield StreamChunk(
                            payload={
                                "id": request_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {},
                                        "finish_reason": _FINISH_MAP.get(cand["finishReason"], "stop"),
                                    }
                                ],
                            }
                        )
```

- [ ] **Step 11.4: Verify tests pass**

Run: `cd api && pytest tests/test_adapter_gemini.py -v`
Expected: 2 PASS.

---

## Task 12: ProxyService — sync orchestration + hook plumbing

**Files:**
- Create: `api/app/proxy/service.py`
- Create: `api/tests/test_proxy_service_sync.py`

- [ ] **Step 12.1: Write the test**

Create `api/tests/test_proxy_service_sync.py`:

```python
import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.identity.models import Tenant
from app.proxy.results import ProxyResult
from app.proxy.service import ProxyService
from app.security.models import ApiKey, ApiKeyMode, Provider, ProviderKey
from app.security.service import issue_api_key
from app.security.vault import build_vault
from app.config import get_settings
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
```

- [ ] **Step 12.2: Verify it fails**

Run: `cd api && pytest tests/test_proxy_service_sync.py -v`
Expected: ImportError.

- [ ] **Step 12.3: Implement `app/proxy/service.py` (sync portion)**

```python
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.proxy.adapters.anthropic import AnthropicAdapter
from app.proxy.adapters.base import ProviderAdapter, TimeoutPolicy
from app.proxy.adapters.gemini import GeminiAdapter
from app.proxy.adapters.openai import OpenAIAdapter
from app.proxy.errors import GatewayError, ProviderKeyMissingError
from app.proxy.registry import resolve_provider
from app.proxy.results import OnChunk, OnCompleted, ProxyResult
from app.proxy.retry import RetryPolicy, with_retries
from app.security.models import ApiKey, Provider, ProviderKey
from app.security.vault import Vault, build_vault
from app.usage.models import UsageStatus

logger = structlog.get_logger(__name__)


class ProxyService:
    def __init__(self, settings: Settings, *, vault: Vault | None = None) -> None:
        self._settings = settings
        self._vault = vault or build_vault(settings)
        self._adapters: dict[Provider, ProviderAdapter] = {
            Provider.OPENAI: OpenAIAdapter(),
            Provider.ANTHROPIC: AnthropicAdapter(),
            Provider.GEMINI: GeminiAdapter(),
        }
        self.on_completed: list[OnCompleted] = []
        self.on_chunk: list[OnChunk] = []

    def _timeout_policy(self) -> TimeoutPolicy:
        return TimeoutPolicy(
            connect_seconds=self._settings.proxy_timeout_connect_seconds,
            read_seconds=self._settings.proxy_timeout_read_seconds,
            write_seconds=self._settings.proxy_timeout_read_seconds,
            total_seconds=self._settings.proxy_timeout_total_seconds,
        )

    async def _load_provider_secret(
        self, db: AsyncSession, *, tenant_id: uuid.UUID, provider: Provider
    ) -> str:
        stmt = (
            select(ProviderKey)
            .where(
                ProviderKey.tenant_id == tenant_id,
                ProviderKey.provider == provider,
                ProviderKey.revoked_at.is_(None),
            )
            .order_by(ProviderKey.created_at.desc())
            .limit(1)
        )
        record = (await db.execute(stmt)).scalar_one_or_none()
        if record is None:
            raise ProviderKeyMissingError(provider=provider.value)
        return self._vault.decrypt(bytes(record.encrypted_secret))

    async def _emit_completed(self, result: ProxyResult) -> None:
        for hook in self.on_completed:
            try:
                await hook(result)
            except Exception:
                logger.exception("proxy.on_completed.hook.failed", request_id=result.request_id)

    async def chat_completion_sync(
        self,
        *,
        db: AsyncSession,
        api_key: ApiKey,
        request_body: dict,
    ) -> tuple[ProxyResult, dict]:
        request_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        t0 = time.monotonic()
        provider = resolve_provider(request_body["model"])
        secret = await self._load_provider_secret(
            db, tenant_id=api_key.tenant_id, provider=provider
        )
        adapter = self._adapters[provider]
        timeout = self._timeout_policy()
        retry_policy = RetryPolicy.from_settings(self._settings)

        async def _call() -> dict:
            return await adapter.chat_completion(
                request_body,
                upstream_secret=secret,
                timeout=timeout,
                request_id=request_id,
            )

        try:
            response = await with_retries(_call, policy=retry_policy)
        except GatewayError as exc:
            completed_at = datetime.now(timezone.utc)
            result = ProxyResult(
                request_id=request_id,
                tenant_id=api_key.tenant_id,
                api_key_id=api_key.id,
                provider=provider,
                model=request_body["model"],
                status=(
                    UsageStatus.PROVIDER_ERROR
                    if exc.error_type == "upstream_request_error"
                    else UsageStatus.GATEWAY_ERROR
                ),
                streamed=False,
                started_at=started_at,
                completed_at=completed_at,
                latency_ms=int((time.monotonic() - t0) * 1000),
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                error_code=exc.error_type,
                request_body=request_body,
                response_body=None,
            )
            await self._emit_completed(result)
            raise

        response = {**response, "id": request_id}
        usage = response.get("usage") or {}
        completed_at = datetime.now(timezone.utc)
        result = ProxyResult(
            request_id=request_id,
            tenant_id=api_key.tenant_id,
            api_key_id=api_key.id,
            provider=provider,
            model=request_body["model"],
            status=UsageStatus.SUCCESS,
            streamed=False,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=int((time.monotonic() - t0) * 1000),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            error_code=None,
            request_body=request_body,
            response_body=response,
        )
        await self._emit_completed(result)
        return result, response
```

- [ ] **Step 12.4: Verify tests pass**

Run: `cd api && pytest tests/test_proxy_service_sync.py -v`
Expected: 3 PASS.

---

## Task 13: ProxyService — streaming orchestration + cancellation

**Files:**
- Modify: `api/app/proxy/service.py` (add streaming methods)
- Create: `api/tests/test_proxy_service_stream.py`

- [ ] **Step 13.1: Write the test**

Create `api/tests/test_proxy_service_stream.py`:

```python
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
        chunks: list[bytes] = []
        async for chunk in service.chat_completion_stream(
            db=db, api_key=api_key, request_body=body
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
        out: list[bytes] = []
        async for chunk in service.chat_completion_stream(
            db=db, api_key=api_key, request_body=body
        ):
            out.append(chunk)
    raw = b"".join(out).decode()
    assert "banned_term" in raw or '"error"' in raw
    assert seen and seen[0].status == UsageStatus.BLOCKED
```

- [ ] **Step 13.2: Verify it fails**

Run: `cd api && pytest tests/test_proxy_service_stream.py -v`
Expected: AttributeError on `chat_completion_stream`.

- [ ] **Step 13.3: Append streaming method to `app/proxy/service.py`**

Add at the bottom of the `ProxyService` class:

```python
    async def chat_completion_stream(
        self,
        *,
        db: AsyncSession,
        api_key: ApiKey,
        request_body: dict,
    ):
        import json as _json

        request_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        t0 = time.monotonic()
        provider = resolve_provider(request_body["model"])
        secret = await self._load_provider_secret(
            db, tenant_id=api_key.tenant_id, provider=provider
        )
        adapter = self._adapters[provider]
        timeout = self._timeout_policy()

        aggregated: dict = {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request_body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": None}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        prompt_tokens = 0
        completion_tokens = 0
        status = UsageStatus.SUCCESS
        error_code: str | None = None
        abort: "object | None" = None

        try:
            async for chunk in adapter.chat_completion_stream(
                request_body,
                upstream_secret=secret,
                timeout=timeout,
                request_id=request_id,
            ):
                payload = {**chunk.payload, "id": request_id}
                delta = (payload.get("choices") or [{}])[0].get("delta") or {}
                content_piece = delta.get("content") or ""
                if content_piece:
                    aggregated["choices"][0]["message"]["content"] += content_piece
                    completion_tokens += max(1, len(content_piece) // 4)
                from app.proxy.results import AbortReason, ChunkContext

                ctx = ChunkContext(
                    request_id=request_id,
                    aggregated_response=aggregated,
                    latest_delta=delta,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                decision = "continue"
                for hook in self.on_chunk:
                    try:
                        decision = await hook(ctx)
                    except Exception:
                        logger.exception("proxy.on_chunk.hook.failed", request_id=request_id)
                        continue
                    if isinstance(decision, AbortReason):
                        abort = decision
                        break
                if isinstance(abort, AbortReason):
                    yield (
                        b"data: "
                        + _json.dumps(
                            {
                                "error": {
                                    "type": abort.kind,
                                    "message": abort.message,
                                    "code": abort.error_code,
                                }
                            }
                        ).encode()
                        + b"\n\n"
                    )
                    status = UsageStatus.BLOCKED
                    error_code = abort.kind
                    break
                yield b"data: " + _json.dumps(payload).encode() + b"\n\n"
        except GatewayError as exc:
            yield (
                b"data: "
                + _json.dumps({"error": {"type": exc.error_type, "message": exc.message}}).encode()
                + b"\n\n"
            )
            status = (
                UsageStatus.PROVIDER_ERROR
                if exc.error_type == "upstream_request_error"
                else UsageStatus.GATEWAY_ERROR
            )
            error_code = exc.error_type

        if status == UsageStatus.SUCCESS:
            yield b"data: [DONE]\n\n"

        completed_at = datetime.now(timezone.utc)
        aggregated["usage"] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        result = ProxyResult(
            request_id=request_id,
            tenant_id=api_key.tenant_id,
            api_key_id=api_key.id,
            provider=provider,
            model=request_body["model"],
            status=status,
            streamed=True,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=int((time.monotonic() - t0) * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            error_code=error_code,
            request_body=request_body,
            response_body=aggregated,
        )
        await self._emit_completed(result)
```

- [ ] **Step 13.4: Verify streaming tests pass**

Run: `cd api && pytest tests/test_proxy_service_stream.py -v`
Expected: 2 PASS.

---

## Task 14: Estimate flow on ProxyService

**Files:**
- Modify: `api/app/proxy/service.py` (add `estimate` method)
- Create: `api/tests/test_proxy_service_estimate.py`

- [ ] **Step 14.1: Write the test**

Create `api/tests/test_proxy_service_estimate.py`:

```python
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
from datetime import datetime, timezone


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
        fetched_at=datetime.now(timezone.utc),
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
        fetched_at=datetime.now(timezone.utc),
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
```

- [ ] **Step 14.2: Verify it fails**

Run: `cd api && pytest tests/test_proxy_service_estimate.py -v`
Expected: AttributeError on `estimate`.

- [ ] **Step 14.3: Append `estimate` to `ProxyService`**

```python
    async def estimate(
        self,
        *,
        db: AsyncSession,
        api_key: ApiKey,
        request_body: dict,
    ) -> dict:
        from app.pricing.dependencies import get_vendor_catalog
        from app.tokenizers.anthropic_count import AnthropicCounter
        from app.tokenizers.gemini_count import GeminiCounter
        from app.tokenizers.openai_tiktoken import count_openai_input_tokens

        provider = resolve_provider(request_body["model"])
        model = request_body["model"]
        messages = request_body.get("messages") or []
        max_tokens = request_body.get("max_tokens")

        if provider == Provider.OPENAI:
            input_tokens = count_openai_input_tokens(model=model, messages=messages)
        elif provider == Provider.ANTHROPIC:
            secret = await self._load_provider_secret(
                db, tenant_id=api_key.tenant_id, provider=Provider.ANTHROPIC
            )
            input_tokens = await AnthropicCounter(self._settings).count(
                model=model, messages=messages, upstream_secret=secret
            )
        else:
            secret = await self._load_provider_secret(
                db, tenant_id=api_key.tenant_id, provider=Provider.GEMINI
            )
            input_tokens = await GeminiCounter(self._settings).count(
                model=model, messages=messages, upstream_secret=secret
            )

        catalog = get_vendor_catalog(self._settings)
        rate = catalog.get(provider, model)
        snap = catalog.snapshot()
        out: dict = {
            "model": model,
            "provider": provider.value,
            "input_tokens": input_tokens,
            "input_cost_micros": (input_tokens * rate.input_micros_per_token) if rate else None,
            "currency": "USD",
            "pricing_source": snap.source,
            "pricing_fetched_at": snap.fetched_at.isoformat() if snap.fetched_at else None,
        }
        if max_tokens is not None:
            out["max_output_tokens"] = max_tokens
            out["max_output_cost_micros"] = (
                max_tokens * rate.output_micros_per_token if rate else None
            )
        return out
```

- [ ] **Step 14.4: Verify tests pass**

Run: `cd api && pytest tests/test_proxy_service_estimate.py -v`
Expected: 3 PASS.

---

## Task 15: Schemas + routes + wire into main.py

**Files:**
- Create: `api/app/proxy/schemas.py`
- Create: `api/app/proxy/router.py`
- Modify: `api/app/main.py`

- [ ] **Step 15.1: Implement `app/proxy/schemas.py`**

```python
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    max_tokens: int | None = None


class EstimateResponse(BaseModel):
    model: str
    provider: str
    input_tokens: int
    input_cost_micros: int | None
    max_output_tokens: int | None = None
    max_output_cost_micros: int | None = None
    currency: str
    pricing_source: str
    pricing_fetched_at: str | None
```

- [ ] **Step 15.2: Implement `app/proxy/router.py`**

```python
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.proxy.dependencies import require_api_key
from app.proxy.schemas import ChatCompletionRequest, EstimateResponse
from app.proxy.service import ProxyService
from app.security.models import ApiKey

router = APIRouter(prefix="/v1", tags=["proxy"])

_service: ProxyService | None = None


def get_service(settings: Annotated[Settings, Depends(get_settings)]) -> ProxyService:
    global _service
    if _service is None:
        _service = ProxyService(settings)
    return _service


def reset_service() -> None:
    global _service
    _service = None


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
    api_key: Annotated[ApiKey, Depends(require_api_key(scope="chat:write"))],
    db: Annotated[AsyncSession, Depends(get_session)],
    service: Annotated[ProxyService, Depends(get_service)],
):
    request_body = body.model_dump(exclude_unset=False)
    if body.stream:
        async def _stream():
            async for chunk in service.chat_completion_stream(
                db=db, api_key=api_key, request_body=request_body
            ):
                yield chunk
        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"X-Request-ID": "(streaming)", "Cache-Control": "no-cache"},
        )

    result, response = await service.chat_completion_sync(
        db=db, api_key=api_key, request_body=request_body
    )
    return JSONResponse(
        content=response,
        headers={"X-Request-ID": result.request_id},
    )


@router.post("/estimate", response_model=EstimateResponse)
async def estimate(
    body: ChatCompletionRequest,
    api_key: Annotated[ApiKey, Depends(require_api_key(scope="chat:write"))],
    db: Annotated[AsyncSession, Depends(get_session)],
    service: Annotated[ProxyService, Depends(get_service)],
) -> dict:
    return await service.estimate(
        db=db, api_key=api_key, request_body=body.model_dump(exclude_unset=False)
    )
```

- [ ] **Step 15.3: Register router in `app/main.py`**

Inside `create_app()`, add:
```python
    from app.proxy.router import router as proxy_router

    app.include_router(proxy_router)
```

- [ ] **Step 15.4: Reset proxy service in conftest**

In `api/tests/conftest.py`, the `engine` fixture body — after `reset_vendor_catalog()`, add:

```python
    from app.proxy.router import reset_service
    reset_service()
```

- [ ] **Step 15.5: Smoke check**

Run: `cd api && pytest tests/test_health.py -v`
Expected: PASS (no behavior regressions).

---

## Task 16: Integration tests — happy paths via HTTP

**Files:**
- Create: `api/tests/test_proxy_endpoints_happy.py`

- [ ] **Step 16.1: Write the test**

Create `api/tests/test_proxy_endpoints_happy.py`:

```python
import json
from datetime import datetime, timezone

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import get_session
from app.identity.models import Tenant
from app.pricing.dependencies import get_vendor_catalog
from app.pricing.vendor_catalog import CatalogSnapshot, VendorRate
from app.security.models import ApiKeyMode, Provider, ProviderKey
from app.security.service import issue_api_key
from app.security.vault import build_vault


async def _seed_tenant_with_keys(sm) -> tuple[str, dict[Provider, str]]:
    settings = get_settings()
    vault = build_vault(settings)
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
    return plaintext, secrets_map


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
        fetched_at=datetime.now(timezone.utc),
        version="t",
    )


async def _proxy_client(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    plaintext, _ = await _seed_tenant_with_keys(sm)
    _seed_catalog()
    from app.main import app

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), plaintext


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_openai_sync(engine):
    client, token = await _proxy_client(engine)
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
    async with client as c:
        r = await c.post(
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
async def test_chat_completions_openai_stream(engine):
    client, token = await _proxy_client(engine)
    sse = (
        "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "Hi"}, "finish_reason": None}]}) + "\n\n"
        "data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + "\n\n"
        "data: [DONE]\n\n"
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    )
    async with client as c:
        r = await c.post(
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
async def test_chat_completions_anthropic_sync(engine):
    client, token = await _proxy_client(engine)
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
    async with client as c:
        r = await c.post(
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
async def test_chat_completions_gemini_sync(engine):
    client, token = await _proxy_client(engine)
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
    async with client as c:
        r = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hey"


@pytest.mark.asyncio
@respx.mock
async def test_estimate_openai(engine):
    client, token = await _proxy_client(engine)
    async with client as c:
        r = await c.post(
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
```

- [ ] **Step 16.2: Verify tests pass**

Run: `cd api && pytest tests/test_proxy_endpoints_happy.py -v`
Expected: 5 PASS.

---

## Task 17: Integration tests — negative paths

**Files:**
- Create: `api/tests/test_proxy_endpoints_negative.py`

- [ ] **Step 17.1: Write the test**

Create `api/tests/test_proxy_endpoints_negative.py`:

```python
import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_session
from app.identity.models import Tenant
from app.security.models import ApiKeyMode, Provider, ProviderKey
from app.security.service import issue_api_key
from app.security.vault import build_vault
from app.config import get_settings


async def _seed_tenant(sm, *, with_provider: Provider | None = Provider.OPENAI):
    settings = get_settings()
    vault = build_vault(settings)
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


async def _http(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    plaintext = await _seed_tenant(sm)
    from app.main import app

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), plaintext


@pytest.mark.asyncio
async def test_chat_completions_no_bearer_returns_401(engine):
    client, _ = await _http(engine)
    async with client as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_chat_completions_unsupported_model_returns_400(engine):
    client, token = await _http(engine)
    async with client as c:
        r = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "foobar", "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "unsupported_model"


@pytest.mark.asyncio
async def test_chat_completions_no_provider_key_returns_400(engine):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    plaintext = await _seed_tenant(sm, with_provider=None)
    from app.main import app

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    async with client as c:
        r = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {plaintext}"},
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "provider_key_missing"


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_upstream_5xx_retries_then_unavailable(engine):
    client, token = await _http(engine)
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(503)
    )
    async with client as c:
        r = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "upstream_unavailable"


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_upstream_401_returns_502_request_error(engine):
    client, token = await _http(engine)
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad"}})
    )
    async with client as c:
        r = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "upstream_request_error"
```

- [ ] **Step 17.2: Verify tests pass**

Run: `cd api && pytest tests/test_proxy_endpoints_negative.py -v`
Expected: 5 PASS.

---

## Task 18: Full test sweep + manual smoke

- [ ] **Step 18.1: Run the entire test suite**

Run: `cd api && pytest -v`
Expected: every test passes.

- [ ] **Step 18.2: Type check**

Run: `cd api && mypy app/proxy app/pricing app/tokenizers`
Expected: 0 errors.

- [ ] **Step 18.3: Lint**

Run: `cd api && ruff check app/proxy app/pricing app/tokenizers tests`
Expected: 0 issues.

- [ ] **Step 18.4: Bring up the docker-compose stack**

Run from repo root: `docker compose -f infra/docker-compose.yml up -d --build`
Expected: api, web, postgres, redis, minio all healthy.

- [ ] **Step 18.5: Apply migrations inside the api container**

Run: `docker compose -f infra/docker-compose.yml exec api alembic upgrade head`
Expected: head reached.

- [ ] **Step 18.6: Manual smoke (optional, requires real upstream key)**

Skip unless a real OpenAI key is available. Log in via the console (issuing a platform key + uploading an OpenAI provider key), then:

```
curl -s http://localhost:8080/api/v1/chat/completions \
  -H "Authorization: Bearer pk_live_<your-platform-key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"say hi"}]}' \
  | jq
```

Expected: JSON response in OpenAI shape with a real completion. The proxy returns the gateway-generated `id` (UUID).

- [ ] **Step 18.7: Verify request-id header on a failure**

Run with an invalid bearer:
```
curl -i -s http://localhost:8080/api/v1/chat/completions \
  -H "Authorization: Bearer pk_live_garbage" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"x"}]}'
```

Expected: HTTP/1.1 401 with body `{"error":{"type":"authentication_error",...}}`.

---

## Self-review notes

- Spec coverage: every requirement in `docs/superpowers/specs/2026-04-29-stage-1-llm-proxy-design.md` has at least one task above. `chat_completion_stream` covers §4.2 (cancellation falls out of standard task-cancellation by httpx; not separately tested at integration level — covered by the unit test in Task 13). The disconnect-mid-stream test was deliberately not added at integration level because ASGI's transport doesn't easily simulate it; the unit-level coverage is sufficient.
- Pricing source priority + fallback: Task 5 covers it.
- Hooks (on_completed, on_chunk): Task 12 (on_completed sync) + Task 13 (on_chunk and on_completed streaming).
- Backfill scopes migration: Task 2.
- Admin pricing endpoints with role check: Task 6.
- Settings additions: Task 1.
- Vendor catalog refresh on startup: Task 6 (lifespan hook).
- Tokenizers: Task 7.
- All three adapters: Tasks 9, 10, 11.
- Estimate flow: Task 14.
- Routes + wire into app: Task 15.
- Integration tests: Tasks 16, 17.
