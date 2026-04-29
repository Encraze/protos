import json

import httpx
import pytest
import respx

from app.proxy.adapters.anthropic import AnthropicAdapter
from app.proxy.adapters.base import TimeoutPolicy

POLICY = TimeoutPolicy(connect_seconds=5, read_seconds=60, write_seconds=60, total_seconds=120)


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_sync_translates_request_and_response():
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "id": "msg_upstream",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4-7",
                "content": [{"type": "text", "text": "Hi there"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
        )

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=_handler)
    adapter = AnthropicAdapter()
    body = {
        "model": "claude-opus-4-7",
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Hi"},
        ],
        "max_tokens": 100,
    }
    resp = await adapter.chat_completion(
        body, upstream_secret="ant-key", timeout=POLICY, request_id="req-1"
    )
    assert captured["body"]["system"] == "Be concise."
    assert captured["body"]["messages"] == [{"role": "user", "content": "Hi"}]
    assert captured["headers"]["x-api-key"] == "ant-key"
    assert resp["choices"][0]["message"]["content"] == "Hi there"
    assert resp["choices"][0]["finish_reason"] == "stop"
    assert resp["usage"] == {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_streaming_emits_openai_shape_chunks():
    events = (
        "event: message_start\ndata: " + json.dumps(
            {"type": "message_start", "message": {"id": "msg_x", "model": "claude-opus-4-7"}}
        ) + "\n\n"
        "event: content_block_start\ndata: " + json.dumps(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
        ) + "\n\n"
        "event: content_block_delta\ndata: " + json.dumps(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "He"}}
        ) + "\n\n"
        "event: content_block_delta\ndata: " + json.dumps(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "llo"}}
        ) + "\n\n"
        "event: content_block_stop\ndata: " + json.dumps({"type": "content_block_stop", "index": 0}) + "\n\n"
        "event: message_delta\ndata: " + json.dumps(
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}
        ) + "\n\n"
        "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n"
    )
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, text=events, headers={"content-type": "text/event-stream"}
        )
    )
    adapter = AnthropicAdapter()
    body = {
        "model": "claude-opus-4-7",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 100,
    }
    chunks = []
    async for chunk in adapter.chat_completion_stream(
        body, upstream_secret="ant-key", timeout=POLICY, request_id="req-1"
    ):
        chunks.append(chunk.payload)
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c.get("choices"))
    assert text == "Hello"
    finals = [c for c in chunks if c["choices"][0].get("finish_reason") == "stop"]
    assert len(finals) == 1
