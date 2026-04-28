from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_user
from app.config import Settings, get_settings
from app.db import get_session
from app.identity.models import Membership, User
from app.security.vault import Vault, build_vault

_vault: Vault | None = None


def get_vault(settings: Annotated[Settings, Depends(get_settings)]) -> Vault:
    global _vault
    if _vault is None:
        _vault = build_vault(settings)
    return _vault


def reset_vault() -> None:
    global _vault
    _vault = None


async def get_current_membership(
    user: Annotated[User, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Membership:
    stmt = (
        select(Membership)
        .where(Membership.user_id == user.id)
        .order_by(Membership.created_at)
        .limit(1)
    )
    membership = (await db.execute(stmt)).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user has no tenant membership",
        )
    return membership
