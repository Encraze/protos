from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.providers.stub import SEEDED_USERS
from app.identity.models import Membership, OAuthAccount, Role, Tenant, User

SEEDED_TENANT_SLUG = "acme"
SEEDED_TENANT_NAME = "Acme"

SEEDED_MEMBERSHIPS: dict[str, tuple[str, Role]] = {
    "alice@protos.dev": (SEEDED_TENANT_SLUG, Role.OWNER),
    "bob@protos.dev": (SEEDED_TENANT_SLUG, Role.MEMBER),
}


async def seed_stub_users(db: AsyncSession) -> None:
    tenant = (
        await db.execute(select(Tenant).where(Tenant.slug == SEEDED_TENANT_SLUG))
    ).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(slug=SEEDED_TENANT_SLUG, name=SEEDED_TENANT_NAME)
        db.add(tenant)
        await db.flush()

    for seeded in SEEDED_USERS:
        existing_oauth = (
            await db.execute(
                select(OAuthAccount).where(
                    OAuthAccount.provider == seeded.provider,
                    OAuthAccount.provider_user_id == seeded.provider_user_id,
                )
            )
        ).scalar_one_or_none()
        if existing_oauth is not None:
            continue

        user = (
            await db.execute(select(User).where(User.email == seeded.email))
        ).scalar_one_or_none()
        if user is None:
            user = User(email=seeded.email, name=seeded.name)
            db.add(user)
            await db.flush()

        db.add(
            OAuthAccount(
                user_id=user.id,
                provider=seeded.provider,
                provider_user_id=seeded.provider_user_id,
                email=seeded.email,
            )
        )

        assignment = SEEDED_MEMBERSHIPS.get(seeded.email)
        if assignment is not None:
            _, role = assignment
            existing_membership = (
                await db.execute(
                    select(Membership).where(
                        Membership.user_id == user.id,
                        Membership.tenant_id == tenant.id,
                    )
                )
            ).scalar_one_or_none()
            if existing_membership is None:
                db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))

    await db.commit()
