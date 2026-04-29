from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable

import httpx

from app.config import Settings


class AnthropicCounter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[str, tuple[float, int]] = {}

    @staticmethod
    def _split_system(messages: Iterable[dict[str, object]]) -> tuple[str, list[dict]]:
        system_parts: list[str] = []
        rest: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                content = m.get("content")
                if isinstance(content, str):
                    system_parts.append(content)
            else:
                rest.append(dict(m))
        return ("\n\n".join(system_parts), rest)

    async def count(
        self,
        *,
        model: str,
        messages: Iterable[dict[str, object]],
        upstream_secret: str,
    ) -> int:
        msg_list = list(messages)
        system, body_messages = self._split_system(msg_list)
        payload: dict[str, object] = {"model": model, "messages": body_messages}
        if system:
            payload["system"] = system
        cache_key = self._cache_key(model, payload)
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if (
            cached is not None
            and now - cached[0] < self._settings.anthropic_count_tokens_cache_ttl_seconds
        ):
            return cached[1]
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages/count_tokens",
                headers={
                    "x-api-key": upstream_secret,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
        n = int(r.json().get("input_tokens", 0))
        self._cache[cache_key] = (now, n)
        return n

    @staticmethod
    def _cache_key(model: str, payload: dict[str, object]) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"{model}:{hashlib.sha256(body).hexdigest()}"
