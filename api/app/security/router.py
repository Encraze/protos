import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_user
from app.db import get_session
from app.identity.models import Membership, User
from app.security import service
from app.security.dependencies import get_current_membership, get_vault
from app.security.schemas import (
    ApiKeyCreate,
    ApiKeyIssueResponse,
    ApiKeyResponse,
    ProviderKeyCreate,
    ProviderKeyResponse,
)
from app.security.vault import Vault

router = APIRouter(prefix="/v1", tags=["security"])


@router.post(
    "/provider-keys",
    response_model=ProviderKeyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider_key(
    payload: ProviderKeyCreate,
    _user: Annotated[User, Depends(require_user)],
    membership: Annotated[Membership, Depends(get_current_membership)],
    vault: Annotated[Vault, Depends(get_vault)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ProviderKeyResponse:
    record = await service.issue_provider_key(
        db,
        vault,
        tenant_id=membership.tenant_id,
        provider=payload.provider,
        label=payload.label,
        secret=payload.secret,
    )
    await db.commit()
    return ProviderKeyResponse.model_validate(record)


@router.get("/provider-keys", response_model=list[ProviderKeyResponse])
async def list_provider_keys(
    _user: Annotated[User, Depends(require_user)],
    membership: Annotated[Membership, Depends(get_current_membership)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProviderKeyResponse]:
    items = await service.list_provider_keys(db, tenant_id=membership.tenant_id)
    return [ProviderKeyResponse.model_validate(item) for item in items]


@router.post(
    "/provider-keys/{key_id}/revoke",
    response_model=ProviderKeyResponse,
)
async def revoke_provider_key(
    key_id: uuid.UUID,
    _user: Annotated[User, Depends(require_user)],
    membership: Annotated[Membership, Depends(get_current_membership)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ProviderKeyResponse:
    record = await service.revoke_provider_key(
        db, key_id=key_id, tenant_id=membership.tenant_id
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provider key not found")
    await db.commit()
    return ProviderKeyResponse.model_validate(record)


@router.post(
    "/api-keys",
    response_model=ApiKeyIssueResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    payload: ApiKeyCreate,
    user: Annotated[User, Depends(require_user)],
    membership: Annotated[Membership, Depends(get_current_membership)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ApiKeyIssueResponse:
    record, plaintext = await service.issue_api_key(
        db,
        tenant_id=membership.tenant_id,
        created_by_user_id=user.id,
        name=payload.name,
        scopes=payload.scopes,
        mode=payload.mode,
        expires_at=payload.expires_at,
    )
    await db.commit()
    response = ApiKeyResponse.model_validate(record).model_dump()
    response["token"] = plaintext
    return ApiKeyIssueResponse(**response)


@router.get("/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    _user: Annotated[User, Depends(require_user)],
    membership: Annotated[Membership, Depends(get_current_membership)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[ApiKeyResponse]:
    items = await service.list_api_keys(db, tenant_id=membership.tenant_id)
    return [ApiKeyResponse.model_validate(item) for item in items]


@router.post(
    "/api-keys/{key_id}/revoke",
    response_model=ApiKeyResponse,
)
async def revoke_api_key(
    key_id: uuid.UUID,
    _user: Annotated[User, Depends(require_user)],
    membership: Annotated[Membership, Depends(get_current_membership)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ApiKeyResponse:
    record = await service.revoke_api_key(
        db, key_id=key_id, tenant_id=membership.tenant_id
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="api key not found")
    await db.commit()
    return ApiKeyResponse.model_validate(record)
