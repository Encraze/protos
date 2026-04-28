import enum
import uuid

from sqlalchemy import Boolean, Enum, Float, ForeignKey, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity


class GuardrailKind(str, enum.Enum):
    BANNED_TERM = "banned_term"
    PROMPT_INJECTION = "prompt_injection"
    UNSAFE_OUTPUT = "unsafe_output"


class GuardrailMode(str, enum.Enum):
    IGNORE = "ignore"
    WARN = "warn"
    BLOCK = "block"


class PIIMode(str, enum.Enum):
    DETECT = "detect"
    MASK = "mask"
    PSEUDONYMIZE = "pseudonymize"


class GuardrailRule(Entity):
    __tablename__ = "guardrail_rules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[GuardrailKind] = mapped_column(
        Enum(GuardrailKind, name="guardrail_kind", native_enum=False, length=24), nullable=False
    )
    pattern: Mapped[str | None] = mapped_column(Text)
    threshold: Mapped[float | None] = mapped_column(Float)
    mode: Mapped[GuardrailMode] = mapped_column(
        Enum(GuardrailMode, name="guardrail_mode", native_enum=False, length=16),
        nullable=False,
        default=GuardrailMode.WARN,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    label: Mapped[str | None] = mapped_column(String(120))


class GuardrailEvent(Entity):
    __tablename__ = "guardrail_events"

    usage_event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usage_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("guardrail_rules.id", ondelete="CASCADE"), nullable=False
    )
    matched: Mapped[str | None] = mapped_column(Text)
    action_taken: Mapped[GuardrailMode] = mapped_column(
        Enum(GuardrailMode, name="guardrail_mode", native_enum=False, length=16), nullable=False
    )


class PIIEvent(Entity):
    __tablename__ = "pii_events"

    usage_event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usage_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mode: Mapped[PIIMode] = mapped_column(
        Enum(PIIMode, name="pii_mode", native_enum=False, length=16), nullable=False
    )
    entities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
