from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable

import httpx

from app.config import Settings


class GeminiCounter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[str, tuple[float, int]] = {}

    @staticmethod
    def _to_contents(messages: Iterable[dict[str, object]]) -> list[dict]:
        contents: list[dict] = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                continue
            mapped_role = "model" if role == "assistant" else "user"
            content = m.get("content")
            if isinstance(content, str):
                contents.append({"role": mapped_role, "parts": [{"text": content}]})
            elif isinstance(content, list):
                parts = [
                    {"text": part["text"]}
                    for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                ]
                if parts:
                    contents.append({"role": mapped_role, "parts": parts})
        return contents

    async def count(
        self,
        *,
        model: str,
        messages: Iterable[dict[str, object]],
        upstream_secret: str,
    ) -> int:
        msg_list = list(messages)
        payload = {"contents": self._to_contents(msg_list)}
        cache_key = self._cache_key(model, payload)
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if (
            cached is not None
            and now - cached[0] < self._settings.gemini_count_tokens_cache_ttl_seconds
        ):
            return cached[1]
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:countTokens"
            f"?key={upstream_secret}"
        )
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(url, json=payload)
            r.raise_for_status()
        n = int(r.json().get("totalTokens", 0))
        self._cache[cache_key] = (now, n)
        return n

    @staticmethod
    def _cache_key(model: str, payload: dict[str, object]) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"{model}:{hashlib.sha256(body).hexdigest()}"
