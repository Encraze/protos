import json

import httpx
import pytest
import respx

from app.proxy.adapters.base import TimeoutPolicy
from app.proxy.adapters.gemini import GeminiAdapter

POLICY = TimeoutPolicy(connect_seconds=5, read_seconds=60, write_seconds=60, total_seconds=120)


@pytest.mark.asyncio
@respx.mock
async def test_gemini_sync_translates_request_and_response():
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "Hi back"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 8,
                    "candidatesTokenCount": 3,
                    "totalTokenCount": 11,
                },
            },
        )

    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent"
    ).mock(side_effect=_handler)
    adapter = GeminiAdapter()
    body = {
        "model": "gemini-1.5-pro",
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Hi"},
        ],
    }
    resp = await adapter.chat_completion(
        body, upstream_secret="gem-key", timeout=POLICY, request_id="req-1"
    )
    assert "key=gem-key" in captured["url"]
    assert captured["body"]["systemInstruction"]["parts"][0]["text"] == "Be brief."
    assert captured["body"]["contents"] == [
        {"role": "user", "parts": [{"text": "Hi"}]}
    ]
    assert resp["choices"][0]["message"]["content"] == "Hi back"
    assert resp["usage"] == {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}


@pytest.mark.asyncio
@respx.mock
async def test_gemini_streaming_emits_openai_shape_chunks():
    sse_body = (
        "data: " + json.dumps(
            {
                "candidates": [
                    {"content": {"role": "model", "parts": [{"text": "He"}]}}
                ]
            }
        ) + "\n\n"
        "data: " + json.dumps(
            {
                "candidates": [
                    {"content": {"role": "model", "parts": [{"text": "llo"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {
                    "promptTokenCount": 1,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 3,
                },
            }
        ) + "\n\n"
    )
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:streamGenerateContent"
    ).mock(return_value=httpx.Response(200, text=sse_body, headers={"content-type": "text/event-stream"}))
    adapter = GeminiAdapter()
    body = {
        "model": "gemini-1.5-pro",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    chunks = []
    async for chunk in adapter.chat_completion_stream(
        body, upstream_secret="gem-key", timeout=POLICY, request_id="req-1"
    ):
        chunks.append(chunk.payload)
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c.get("choices"))
    assert text == "Hello"
    finals = [c for c in chunks if c["choices"][0].get("finish_reason") == "stop"]
    assert len(finals) == 1
