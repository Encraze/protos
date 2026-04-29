from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.db import get_session
from app.proxy.errors import (
    AuthenticationError,
    GatewayError,
    InsufficientScopeError,
    error_envelope,
)
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
            row.last_used_at = datetime.now(UTC)
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
