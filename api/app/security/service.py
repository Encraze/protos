import secrets
import uuid
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.security.models import ApiKey, ApiKeyMode, Provider, ProviderKey
from app.security.vault import Vault

_hasher = PasswordHasher()
PREFIX_LEN = 16
TOKEN_BODY_BYTES = 24


def _new_token(mode: ApiKeyMode) -> tuple[str, str]:
    body = secrets.token_urlsafe(TOKEN_BODY_BYTES)
    full = f"pk_{mode.value}_{body}"
    return full, full[:PREFIX_LEN]


async def issue_provider_key(
    db: AsyncSession,
    vault: Vault,
    *,
    tenant_id: uuid.UUID,
    provider: Provider,
    label: str,
    secret: str,
) -> ProviderKey:
    encrypted = vault.encrypt(secret)
    last_four = secret[-4:] if len(secret) >= 4 else secret.rjust(4, "*")
    record = ProviderKey(
        tenant_id=tenant_id,
        provider=provider,
        label=label,
        encrypted_secret=encrypted,
        last_four=last_four,
    )
    db.add(record)
    await db.flush()
    return record


async def list_provider_keys(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    include_revoked: bool = True,
) -> list[ProviderKey]:
    stmt = select(ProviderKey).where(ProviderKey.tenant_id == tenant_id)
    if not include_revoked:
        stmt = stmt.where(ProviderKey.revoked_at.is_(None))
    stmt = stmt.order_by(ProviderKey.created_at.desc())
    return list((await db.execute(stmt)).scalars())


async def revoke_provider_key(
    db: AsyncSession,
    *,
    key_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> ProviderKey | None:
    stmt = select(ProviderKey).where(
        ProviderKey.id == key_id,
        ProviderKey.tenant_id == tenant_id,
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        return None
    if record.revoked_at is None:
        record.revoked_at = datetime.now(timezone.utc)
        await db.flush()
    return record


async def reveal_provider_secret(
    db: AsyncSession,
    vault: Vault,
    *,
    key_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> str | None:
    stmt = select(ProviderKey).where(
        ProviderKey.id == key_id,
        ProviderKey.tenant_id == tenant_id,
        ProviderKey.revoked_at.is_(None),
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        return None
    return vault.decrypt(bytes(record.encrypted_secret))


async def issue_api_key(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    created_by_user_id: uuid.UUID | None,
    name: str,
    scopes: list[str],
    mode: ApiKeyMode = ApiKeyMode.LIVE,
    expires_at: datetime | None = None,
) -> tuple[ApiKey, str]:
    full, prefix = _new_token(mode)
    record = ApiKey(
        tenant_id=tenant_id,
        created_by_user_id=created_by_user_id,
        name=name,
        prefix=prefix,
        key_hash=_hasher.hash(full),
        scopes=scopes,
        mode=mode,
        expires_at=expires_at,
    )
    db.add(record)
    await db.flush()
    return record, full


async def list_api_keys(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    include_revoked: bool = True,
) -> list[ApiKey]:
    stmt = select(ApiKey).where(ApiKey.tenant_id == tenant_id)
    if not include_revoked:
        stmt = stmt.where(ApiKey.revoked_at.is_(None))
    stmt = stmt.order_by(ApiKey.created_at.desc())
    return list((await db.execute(stmt)).scalars())


async def revoke_api_key(
    db: AsyncSession,
    *,
    key_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> ApiKey | None:
    stmt = select(ApiKey).where(
        ApiKey.id == key_id,
        ApiKey.tenant_id == tenant_id,
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        return None
    if record.revoked_at is None:
        record.revoked_at = datetime.now(timezone.utc)
        await db.flush()
    return record


async def verify_api_key(db: AsyncSession, token: str) -> ApiKey | None:
    if len(token) < PREFIX_LEN:
        return None
    prefix = token[:PREFIX_LEN]
    stmt = select(ApiKey).where(
        ApiKey.prefix == prefix,
        ApiKey.revoked_at.is_(None),
    )
    candidates = list((await db.execute(stmt)).scalars())
    now = datetime.now(timezone.utc)
    for candidate in candidates:
        if candidate.expires_at is not None and candidate.expires_at <= now:
            continue
        try:
            _hasher.verify(candidate.key_hash, token)
        except VerifyMismatchError:
            continue
        return candidate
    return None
