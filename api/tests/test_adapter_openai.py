import json

import httpx
import pytest
import respx

from app.proxy.adapters.base import TimeoutPolicy
from app.proxy.adapters.openai import OpenAIAdapter

POLICY = TimeoutPolicy(connect_seconds=5, read_seconds=60, write_seconds=60, total_seconds=120)


@pytest.mark.asyncio
@respx.mock
async def test_openai_sync_completion_passes_through_body():
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "id": "cmpl-upstream-1",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hi!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        )

    respx.post("https://api.openai.com/v1/chat/completions").mock(side_effect=_handler)
    adapter = OpenAIAdapter()
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hi"}],
        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
    }
    resp = await adapter.chat_completion(
        body, upstream_secret="sk-test", timeout=POLICY, request_id="req-1"
    )
    assert resp["choices"][0]["message"]["content"] == "Hi!"
    assert resp["usage"]["prompt_tokens"] == 10
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["tools"][0]["function"]["name"] == "lookup"


@pytest.mark.asyncio
@respx.mock
async def test_openai_streaming_normalizes_chunks_and_emits_done():
    sse = (
        "data: " + json.dumps({"choices": [{"delta": {"content": "He"}, "index": 0}]}) + "\n\n"
        "data: " + json.dumps({"choices": [{"delta": {"content": "llo"}, "index": 0}]}) + "\n\n"
        "data: " + json.dumps(
            {"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}
        ) + "\n\n"
        "data: [DONE]\n\n"
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    )
    adapter = OpenAIAdapter()
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    chunks = []
    async for chunk in adapter.chat_completion_stream(
        body, upstream_secret="sk-test", timeout=POLICY, request_id="req-1"
    ):
        chunks.append(chunk.payload)
    deltas = [c["choices"][0]["delta"] for c in chunks if "choices" in c]
    text = "".join(d.get("content", "") for d in deltas)
    assert text == "Hello"
    assert any(c["choices"][0].get("finish_reason") == "stop" for c in chunks)


@pytest.mark.asyncio
@respx.mock
async def test_openai_4xx_raises_upstream_request_error():
    from app.proxy.errors import UpstreamRequestError

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    adapter = OpenAIAdapter()
    with pytest.raises(UpstreamRequestError):
        await adapter.chat_completion(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]},
            upstream_secret="sk-bad",
            timeout=POLICY,
            request_id="req-1",
        )
