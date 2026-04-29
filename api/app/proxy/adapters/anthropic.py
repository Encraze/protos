from __future__ import annotations

import json
import time
import uuid
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


_STOP_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}


def _to_anthropic_request(body: dict) -> dict:
    system_parts: list[str] = []
    messages: list[dict] = []
    for m in body.get("messages") or []:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if isinstance(content, str):
                system_parts.append(content)
            continue
        if role == "tool":
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id"),
                            "content": content if isinstance(content, str) else "",
                        }
                    ],
                }
            )
            continue
        messages.append({"role": role, "content": content})
    out: dict = {
        "model": body["model"],
        "messages": messages,
        "max_tokens": body.get("max_tokens", 1024),
    }
    if system_parts:
        out["system"] = "\n\n".join(system_parts)
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        out["top_p"] = body["top_p"]
    if body.get("stop") is not None:
        stop = body["stop"]
        out["stop_sequences"] = stop if isinstance(stop, list) else [stop]
    if body.get("tools"):
        out["tools"] = [
            {
                "name": t.get("function", {}).get("name"),
                "description": t.get("function", {}).get("description", ""),
                "input_schema": t.get("function", {}).get("parameters", {"type": "object"}),
            }
            for t in body["tools"]
            if t.get("type") == "function"
        ]
    if body.get("tool_choice"):
        tc = body["tool_choice"]
        if tc == "auto":
            out["tool_choice"] = {"type": "auto"}
        elif tc == "none":
            out["tool_choice"] = {"type": "none"}
        elif isinstance(tc, dict) and tc.get("type") == "function":
            out["tool_choice"] = {"type": "tool", "name": tc.get("function", {}).get("name")}
    return out


def _from_anthropic_response(model: str, request_id: str, body: dict) -> dict:
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in body.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or str(uuid.uuid4()),
                    "type": "function",
                    "function": {
                        "name": block.get("name"),
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )
    message: dict = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    finish_reason = _STOP_REASON_MAP.get(body.get("stop_reason") or "", "stop")
    usage = body.get("usage") or {}
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": int(usage.get("input_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("input_tokens") or 0)
            + int(usage.get("output_tokens") or 0),
        },
    }


class AnthropicAdapter(ProviderAdapter):
    provider: ClassVar[Provider] = Provider.ANTHROPIC
    _BASE = "https://api.anthropic.com/v1/messages"

    async def chat_completion(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> dict:
        payload = _to_anthropic_request(request_body)
        async with httpx.AsyncClient(timeout=_httpx_timeout(timeout)) as c:
            r = await c.post(
                self._BASE,
                headers={
                    "x-api-key": upstream_secret,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            if r.status_code >= 500:
                r.raise_for_status()
            if r.status_code >= 400:
                msg = (r.json().get("error") or {}).get("message", f"HTTP {r.status_code}")
                raise UpstreamRequestError(msg)
        return _from_anthropic_response(request_body["model"], request_id, r.json())

    async def chat_completion_stream(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]:
        payload = {**_to_anthropic_request(request_body), "stream": True}
        model = request_body["model"]
        created = int(time.time())
        async with httpx.AsyncClient(timeout=_httpx_timeout(timeout)) as c:
            async with c.stream(
                "POST",
                self._BASE,
                headers={
                    "x-api-key": upstream_secret,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                    "accept": "text/event-stream",
                },
                json=payload,
            ) as r:
                if r.status_code >= 500:
                    raise UpstreamUnavailableError(f"upstream {r.status_code}")
                if r.status_code >= 400:
                    text = (await r.aread()).decode("utf-8", errors="replace")
                    raise UpstreamRequestError(f"upstream {r.status_code}: {text[:200]}")
                async for raw in r.aiter_lines():
                    if not raw or not raw.startswith("data:"):
                        continue
                    body_str = raw[len("data:"):].strip()
                    try:
                        evt = json.loads(body_str)
                    except json.JSONDecodeError:
                        continue
                    etype = evt.get("type")
                    if etype == "content_block_delta":
                        delta = evt.get("delta") or {}
                        text = (
                            delta.get("text") if delta.get("type") == "text_delta" else None
                        )
                        if text is not None:
                            yield StreamChunk(
                                payload={
                                    "id": request_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model,
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {"content": text},
                                            "finish_reason": None,
                                        }
                                    ],
                                }
                            )
                    elif etype == "message_delta":
                        stop_reason = (evt.get("delta") or {}).get("stop_reason")
                        if stop_reason:
                            yield StreamChunk(
                                payload={
                                    "id": request_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model,
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {},
                                            "finish_reason": _STOP_REASON_MAP.get(
                                                stop_reason, "stop"
                                            ),
                                        }
                                    ],
                                }
                            )
                    elif etype == "message_stop":
                        return
