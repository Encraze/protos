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


_FINISH_MAP = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "OTHER": "stop",
}


def _to_gemini_request(body: dict) -> tuple[dict, str | None]:
    contents: list[dict] = []
    system_text: list[str] = []
    for m in body.get("messages") or []:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if isinstance(content, str):
                system_text.append(content)
            continue
        mapped_role = "model" if role == "assistant" else "user"
        if isinstance(content, str):
            contents.append({"role": mapped_role, "parts": [{"text": content}]})
        elif isinstance(content, list):
            parts = [
                {"text": p.get("text")}
                for p in content
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            ]
            if parts:
                contents.append({"role": mapped_role, "parts": parts})
    out: dict = {"contents": contents}
    if system_text:
        out["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_text)}]}
    gen_config: dict = {}
    if body.get("temperature") is not None:
        gen_config["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        gen_config["topP"] = body["top_p"]
    if body.get("max_tokens") is not None:
        gen_config["maxOutputTokens"] = body["max_tokens"]
    if body.get("stop") is not None:
        stop = body["stop"]
        gen_config["stopSequences"] = stop if isinstance(stop, list) else [stop]
    if gen_config:
        out["generationConfig"] = gen_config
    if body.get("tools"):
        out["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": t.get("function", {}).get("name"),
                        "description": t.get("function", {}).get("description", ""),
                        "parameters": t.get("function", {}).get("parameters")
                        or {"type": "object"},
                    }
                    for t in body["tools"]
                    if t.get("type") == "function"
                ]
            }
        ]
    return out, body.get("model")


def _from_gemini_response(model: str, request_id: str, body: dict) -> dict:
    candidates = body.get("candidates") or []
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    finish_reason = "stop"
    if candidates:
        cand = candidates[0]
        for part in (cand.get("content") or {}).get("parts") or []:
            if "text" in part:
                text_parts.append(part["text"] or "")
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(
                    {
                        "id": str(uuid.uuid4()),
                        "type": "function",
                        "function": {
                            "name": fc.get("name"),
                            "arguments": json.dumps(fc.get("args") or {}),
                        },
                    }
                )
        finish_reason = _FINISH_MAP.get(cand.get("finishReason") or "STOP", "stop")
    message: dict = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = body.get("usageMetadata") or {}
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": int(usage.get("promptTokenCount") or 0),
            "completion_tokens": int(usage.get("candidatesTokenCount") or 0),
            "total_tokens": int(usage.get("totalTokenCount") or 0),
        },
    }


class GeminiAdapter(ProviderAdapter):
    provider: ClassVar[Provider] = Provider.GEMINI

    @staticmethod
    def _url(model: str, secret: str, *, stream: bool) -> str:
        action = "streamGenerateContent" if stream else "generateContent"
        sse = "&alt=sse" if stream else ""
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{action}"
            f"?key={secret}{sse}"
        )

    async def chat_completion(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> dict:
        payload, model = _to_gemini_request(request_body)
        assert model is not None
        async with httpx.AsyncClient(timeout=_httpx_timeout(timeout)) as c:
            r = await c.post(
                self._url(model, upstream_secret, stream=False),
                headers={"content-type": "application/json"},
                json=payload,
            )
            if r.status_code >= 500:
                r.raise_for_status()
            if r.status_code >= 400:
                msg = (r.json().get("error") or {}).get("message", f"HTTP {r.status_code}")
                raise UpstreamRequestError(msg)
        return _from_gemini_response(model, request_id, r.json())

    async def chat_completion_stream(
        self,
        request_body: dict,
        upstream_secret: str,
        *,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]:
        payload, model = _to_gemini_request(request_body)
        assert model is not None
        created = int(time.time())
        async with httpx.AsyncClient(timeout=_httpx_timeout(timeout)) as c:
            async with c.stream(
                "POST",
                self._url(model, upstream_secret, stream=True),
                headers={"content-type": "application/json", "accept": "text/event-stream"},
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
                    candidates = evt.get("candidates") or []
                    if not candidates:
                        continue
                    cand = candidates[0]
                    parts = (cand.get("content") or {}).get("parts") or []
                    text = "".join(p.get("text", "") for p in parts if "text" in p)
                    if text:
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
                    if cand.get("finishReason"):
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
                                        "finish_reason": _FINISH_MAP.get(
                                            cand["finishReason"], "stop"
                                        ),
                                    }
                                ],
                            }
                        )
