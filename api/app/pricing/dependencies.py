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
