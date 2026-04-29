from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import ClassVar, Protocol

from app.security.models import Provider


@dataclass(frozen=True)
class TimeoutPolicy:
    connect_seconds: float
    read_seconds: float
    write_seconds: float
    total_seconds: float


@dataclass
class StreamChunk:
    payload: dict


class ProviderAdapter(Protocol):
    provider: ClassVar[Provider]

    async def chat_completion(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> dict: ...

    def chat_completion_stream(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]: ...
