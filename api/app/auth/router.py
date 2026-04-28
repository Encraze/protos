from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_session, require_user
from app.auth.providers.registry import SUPPORTED_PROVIDERS, get_provider
from app.auth.providers.stub import seeded_users_for
from app.auth.service import (
    create_session,
    ensure_membership,
    find_or_create_user_from_oauth,
    issue_state_token,
    revoke_session,
    verify_state_token,
)
from app.config import Settings, get_settings
from app.db import get_session
from app.identity.models import Membership, Tenant, User
from app.identity.models import Session as DbSession

router = APIRouter(prefix="/auth", tags=["auth"])

ProviderName = Annotated[str, Path(pattern="^(google|github)$")]


def _is_secure_cookie_environment(settings: Settings) -> bool:
    return settings.environment in ("stage", "prod")


@router.get("/{provider}/start")
async def start_login(
    provider: ProviderName,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    p = get_provider(settings, provider)
    raw_state, signed_state = issue_state_token(settings)
    target_url = p.authorization_url(raw_state)
    response = RedirectResponse(url=target_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=settings.oauth_state_cookie_name,
        value=signed_state,
        max_age=settings.oauth_state_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=_is_secure_cookie_environment(settings),
        path="/",
    )
    return response


@router.get("/stub/picker", response_class=HTMLResponse)
async def stub_picker(
    settings: Annotated[Settings, Depends(get_settings)],
    provider: str = Query(...),
    state: str = Query(...),
) -> HTMLResponse:
    if settings.auth_mode != "stub":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    public_base = settings.api_public_url.rstrip("/")
    rows = "".join(
        '<li><a href="{base}/auth/{provider}/callback?{qs}">'
        "<strong>{name}</strong><span>{email}</span></a></li>".format(
            base=public_base,
            provider=provider,
            qs=urlencode({"code": user.provider_user_id, "state": state}),
            name=user.name,
            email=user.email,
        )
        for user in seeded_users_for(provider)
    )
    body = (
        "<!doctype html>"
        '<html><head><meta charset="utf-8"><title>Stub login picker</title>'
        "<style>"
        "body{font-family:system-ui;background:#0a0a0b;color:#fafafa;"
        "padding:2rem;max-width:520px;margin:auto}"
        "h1{font-weight:600;margin-bottom:.25rem}"
        "p{color:#a1a1aa;margin-top:0}"
        "ul{list-style:none;padding:0;margin-top:1.5rem}"
        "li{margin:.5rem 0}"
        "a{display:block;padding:1rem;border-radius:8px;"
        "background:#18181b;border:1px solid #27272a;color:#fafafa;"
        "text-decoration:none;transition:border-color .15s ease}"
        "a:hover{border-color:#10b981}"
        "strong{display:block;font-size:1rem}"
        "span{color:#a1a1aa;font-size:.875rem;display:block;margin-top:.125rem}"
        "</style></head><body>"
        f"<h1>Stub login — {provider}</h1>"
        "<p>Choose a seeded user to sign in as.</p>"
        f"<ul>{rows}</ul>"
        "</body></html>"
    )
    return HTMLResponse(content=body)


@router.get("/{provider}/callback")
async def callback(
    provider: ProviderName,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    code: str = Query(...),
    state: str = Query(...),
) -> Response:
    state_cookie = request.cookies.get(settings.oauth_state_cookie_name)
    if not state_cookie:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing state cookie")
    if not verify_state_token(settings, state_cookie, state):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid state")

    p = get_provider(settings, provider)
    try:
        info = await p.exchange_code(code)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    user = await find_or_create_user_from_oauth(db, info)
    await ensure_membership(db, user)
    _, signed_session = await create_session(db, settings, user.id)
    await db.commit()

    response = RedirectResponse(url=settings.frontend_url, status_code=status.HTTP_302_FOUND)
    response.delete_cookie(key=settings.oauth_state_cookie_name, path="/")
    response.set_cookie(
        key=settings.session_cookie_name,
        value=signed_session,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=_is_secure_cookie_environment(settings),
        path="/",
    )
    return response


@router.get("/me")
async def me(
    user: Annotated[User, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    stmt = (
        select(Membership, Tenant)
        .join(Tenant, Membership.tenant_id == Tenant.id)
        .where(Membership.user_id == user.id)
        .order_by(Membership.created_at)
    )
    rows = (await db.execute(stmt)).all()
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "memberships": [
            {
                "tenant_id": str(tenant.id),
                "tenant_slug": tenant.slug,
                "tenant_name": tenant.name,
                "role": membership.role.value,
            }
            for membership, tenant in rows
        ],
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    db: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[DbSession | None, Depends(get_current_session)],
) -> Response:
    if session is not None:
        await revoke_session(db, session)
        await db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return response
