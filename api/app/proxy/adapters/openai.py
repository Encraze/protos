from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import ClassVar

import httpx

from app.proxy.adapters.base import ProviderAdapter, StreamChunk, TimeoutPolicy
from app.proxy.errors import UpstreamRequestError, UpstreamUnavailableError
from app.security.models import Provider


def _httpx_timeout(p: TimeoutPolicy) -> httpx.Timeout:
    return httpx.Timeout(
        connect=p.connect_seconds,
        read=p.read_seconds,
        write=p.write_seconds,
        pool=p.connect_seconds,
    )


class OpenAIAdapter(ProviderAdapter):
    provider: ClassVar[Provider] = Provider.OPENAI
    _BASE = "https://api.openai.com/v1/chat/completions"

    async def chat_completion(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> dict:
        body = {**request_body}
        body.pop("stream", None)
        async with httpx.AsyncClient(timeout=_httpx_timeout(timeout)) as c:
            r = await c.post(
                self._BASE,
                headers={
                    "authorization": f"Bearer {upstream_secret}",
                    "content-type": "application/json",
                },
                json=body,
            )
            if r.status_code >= 500:
                r.raise_for_status()
            if r.status_code >= 400:
                msg = (r.json().get("error") or {}).get("message", f"HTTP {r.status_code}")
                raise UpstreamRequestError(msg)
        return r.json()

    async def chat_completion_stream(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]:
        body = {**request_body, "stream": True}
        async with httpx.AsyncClient(timeout=_httpx_timeout(timeout)) as c:
            async with c.stream(
                "POST",
                self._BASE,
                headers={
                    "authorization": f"Bearer {upstream_secret}",
                    "content-type": "application/json",
                    "accept": "text/event-stream",
                },
                json=body,
            ) as r:
                if r.status_code >= 500:
                    raise UpstreamUnavailableError(f"upstream {r.status_code}")
                if r.status_code >= 400:
                    text = (await r.aread()).decode("utf-8", errors="replace")
                    raise UpstreamRequestError(f"upstream {r.status_code}: {text[:200]}")
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        yield StreamChunk(payload=json.loads(payload))
                    except json.JSONDecodeError:
                        continue
