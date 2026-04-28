from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import load_session_from_cookie
from app.config import Settings, get_settings
from app.db import get_session
from app.identity.models import Session, User


async def get_current_session(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Session | None:
    cookie = request.cookies.get(settings.session_cookie_name)
    return await load_session_from_cookie(db, settings, cookie)


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_session)],
    session: Annotated[Session | None, Depends(get_current_session)],
) -> User | None:
    if session is None:
        return None
    return await db.get(User, session.user_id)


async def require_user(
    user: Annotated[User | None, Depends(get_current_user)],
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
        )
    return user
