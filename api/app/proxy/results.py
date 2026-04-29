from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Union

from app.security.models import Provider
from app.usage.models import UsageStatus


@dataclass(frozen=True)
class AbortReason:
    kind: str
    message: str
    error_code: str | None = None


ChunkDecision = Union[str, AbortReason]


@dataclass
class ChunkContext:
    request_id: str
    aggregated_response: dict
    latest_delta: dict
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class ProxyResult:
    request_id: str
    tenant_id: uuid.UUID
    api_key_id: uuid.UUID
    provider: Provider
    model: str
    status: UsageStatus
    streamed: bool
    started_at: datetime
    completed_at: datetime
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    error_code: str | None
    request_body: dict
    response_body: dict | None


OnCompleted = Callable[[ProxyResult], Awaitable[None]]
OnChunk = Callable[[ChunkContext], Awaitable[ChunkDecision]]
