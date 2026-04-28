import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.providers.base import OAuthUserInfo
from app.config import Settings
from app.identity.models import Membership, OAuthAccount, Role, Session, Tenant, User


def _session_signer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="protos.session")


def _state_signer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="protos.oauth-state")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_state_token(settings: Settings) -> tuple[str, str]:
    raw = secrets.token_urlsafe(24)
    signed = _state_signer(settings).dumps(raw)
    return raw, signed


def verify_state_token(settings: Settings, signed_cookie: str, expected_raw: str) -> bool:
    try:
        raw = _state_signer(settings).loads(
            signed_cookie, max_age=settings.oauth_state_ttl_seconds
        )
    except (BadSignature, SignatureExpired):
        return False
    return secrets.compare_digest(str(raw), expected_raw)


_SLUG_NON_ALPHANUM = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    cleaned = _SLUG_NON_ALPHANUM.sub("-", value.lower()).strip("-")
    return cleaned or "tenant"


async def find_or_create_user_from_oauth(
    db: AsyncSession, info: OAuthUserInfo
) -> User:
    oauth_stmt = select(OAuthAccount).where(
        OAuthAccount.provider == info.provider,
        OAuthAccount.provider_user_id == info.provider_user_id,
    )
    oauth_account = (await db.execute(oauth_stmt)).scalar_one_or_none()
    if oauth_account is not None:
        user = await db.get(User, oauth_account.user_id)
        if user is None:
            raise RuntimeError("oauth_account references missing user")
        return user

    user_stmt = select(User).where(User.email == info.email)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    if user is None:
        user = User(email=info.email, name=info.name)
        db.add(user)
        await db.flush()

    db.add(
        OAuthAccount(
            user_id=user.id,
            provider=info.provider,
            provider_user_id=info.provider_user_id,
            email=info.email,
        )
    )
    await db.flush()
    return user


async def ensure_membership(db: AsyncSession, user: User) -> Membership:
    existing_stmt = select(Membership).where(Membership.user_id == user.id).limit(1)
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    email_local = user.email.split("@", 1)[0]
    base_slug = slugify(email_local)
    slug = f"{base_slug}-{secrets.token_hex(3)}"
    tenant = Tenant(slug=slug, name=f"{email_local}'s workspace")
    db.add(tenant)
    await db.flush()

    membership = Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER)
    db.add(membership)
    await db.flush()
    return membership


async def create_session(
    db: AsyncSession, settings: Settings, user_id: uuid.UUID
) -> tuple[Session, str]:
    raw_token = secrets.token_urlsafe(32)
    signed_cookie = _session_signer(settings).dumps(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    session = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=_hash_token(raw_token),
        expires_at=expires_at,
    )
    db.add(session)
    await db.flush()
    return session, signed_cookie


async def load_session_from_cookie(
    db: AsyncSession, settings: Settings, cookie_value: str | None
) -> Session | None:
    if not cookie_value:
        return None
    try:
        raw_token = _session_signer(settings).loads(
            cookie_value, max_age=settings.session_ttl_hours * 3600
        )
    except (BadSignature, SignatureExpired):
        return None
    stmt = select(Session).where(Session.token_hash == _hash_token(str(raw_token)))
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None:
        return None
    if session.revoked_at is not None:
        return None
    if session.expires_at <= datetime.now(timezone.utc):
        return None
    return session


async def revoke_session(db: AsyncSession, session: Session) -> None:
    session.revoked_at = datetime.now(timezone.utc)
    await db.flush()
