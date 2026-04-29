import httpx
import pytest
import respx

from app.config import get_settings
from app.tokenizers.anthropic_count import AnthropicCounter
from app.tokenizers.gemini_count import GeminiCounter
from app.tokenizers.openai_tiktoken import count_openai_input_tokens

OPENAI_MESSAGES = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hello, world!"},
]


def test_openai_tokenizer_known_model_returns_positive_count():
    n = count_openai_input_tokens(model="gpt-4o", messages=OPENAI_MESSAGES)
    assert n > 0


def test_openai_tokenizer_unknown_model_falls_back_and_returns_count():
    n = count_openai_input_tokens(model="gpt-9000-future", messages=OPENAI_MESSAGES)
    assert n > 0


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_counter_calls_api_and_caches():
    settings = get_settings()
    route = respx.post("https://api.anthropic.com/v1/messages/count_tokens").mock(
        return_value=httpx.Response(200, json={"input_tokens": 42})
    )
    counter = AnthropicCounter(settings)
    n1 = await counter.count(
        model="claude-opus-4-7", messages=OPENAI_MESSAGES, upstream_secret="ant-key"
    )
    n2 = await counter.count(
        model="claude-opus-4-7", messages=OPENAI_MESSAGES, upstream_secret="ant-key"
    )
    assert n1 == n2 == 42
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_gemini_counter_calls_api_and_caches():
    settings = get_settings()
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:countTokens"
    ).mock(return_value=httpx.Response(200, json={"totalTokens": 17}))
    counter = GeminiCounter(settings)
    n1 = await counter.count(
        model="gemini-1.5-pro", messages=OPENAI_MESSAGES, upstream_secret="gem-key"
    )
    n2 = await counter.count(
        model="gemini-1.5-pro", messages=OPENAI_MESSAGES, upstream_secret="gem-key"
    )
    assert n1 == n2 == 17
    assert route.call_count == 1
