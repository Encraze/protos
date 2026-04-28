import enum
import uuid

from sqlalchemy import BigInteger, Enum, ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity


class QuotaKind(str, enum.Enum):
    REQUESTS = "requests"
    TOKENS = "tokens"
    SPEND_MICROS = "spend_micros"


class QuotaPeriod(str, enum.Enum):
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"


class Quota(Entity):
    __tablename__ = "quotas"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "api_key_id", "kind", "period", name="uq_quotas_scope_kind_period"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("api_keys.id", ondelete="CASCADE")
    )
    kind: Mapped[QuotaKind] = mapped_column(
        Enum(QuotaKind, name="quota_kind", native_enum=False, length=24), nullable=False
    )
    period: Mapped[QuotaPeriod] = mapped_column(
        Enum(QuotaPeriod, name="quota_period", native_enum=False, length=16), nullable=False
    )
    limit_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
