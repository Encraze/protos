import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Entity
from app.security.models import ApiKeyMode, Provider


class UsageStatus(str, enum.Enum):
    SUCCESS = "success"
    BLOCKED = "blocked"
    PROVIDER_ERROR = "provider_error"
    QUOTA_EXCEEDED = "quota_exceeded"
    GATEWAY_ERROR = "gateway_error"


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("api_keys.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[Provider] = mapped_column(
        Enum(Provider, name="provider", native_enum=False, length=32), nullable=False
    )
    model: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[UsageStatus] = mapped_column(
        Enum(UsageStatus, name="usage_status", native_enum=False, length=24),
        nullable=False,
        index=True,
    )
    mode: Mapped[ApiKeyMode] = mapped_column(
        Enum(ApiKeyMode, name="api_key_mode", native_enum=False, length=16),
        nullable=False,
        default=ApiKeyMode.LIVE,
    )
    streamed: Mapped[bool] = mapped_column(nullable=False, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class ModelPricing(Entity):
    __tablename__ = "model_pricing"
    __table_args__ = (
        UniqueConstraint(
            "provider", "model", "effective_from", name="uq_pricing_provider_model_effective"
        ),
    )

    provider: Mapped[Provider] = mapped_column(
        Enum(Provider, name="provider", native_enum=False, length=32), nullable=False
    )
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_micros_per_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_micros_per_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
