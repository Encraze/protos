import httpx
import pytest

from app.config import get_settings
from app.proxy.errors import (
    GatewayTimeoutError,
    UpstreamRequestError,
    UpstreamUnavailableError,
)
from app.proxy.retry import RetryPolicy, with_retries


@pytest.mark.asyncio
async def test_with_retries_returns_first_success():
    calls = {"n": 0}

    async def op() -> int:
        calls["n"] += 1
        return 7

    policy = RetryPolicy.from_settings(get_settings())
    result = await with_retries(op, policy=policy)
    assert result == 7
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_with_retries_retries_5xx_then_succeeds():
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.HTTPStatusError(
                "server error",
                request=httpx.Request("POST", "https://example/"),
                response=httpx.Response(503),
            )
        return "ok"

    policy = RetryPolicy.from_settings(get_settings()).with_overrides(initial_backoff_ms=1)
    result = await with_retries(op, policy=policy)
    assert result == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_with_retries_exhausts_retries_and_raises_unavailable():
    async def op() -> str:
        raise httpx.HTTPStatusError(
            "server error",
            request=httpx.Request("POST", "https://example/"),
            response=httpx.Response(503),
        )

    policy = RetryPolicy.from_settings(get_settings()).with_overrides(
        max_retries=2, initial_backoff_ms=1
    )
    with pytest.raises(UpstreamUnavailableError):
        await with_retries(op, policy=policy)


@pytest.mark.asyncio
async def test_with_retries_does_not_retry_4xx():
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        raise httpx.HTTPStatusError(
            "auth",
            request=httpx.Request("POST", "https://example/"),
            response=httpx.Response(401, json={"error": {"message": "bad key"}}),
        )

    policy = RetryPolicy.from_settings(get_settings()).with_overrides(
        max_retries=2, initial_backoff_ms=1
    )
    with pytest.raises(UpstreamRequestError):
        await with_retries(op, policy=policy)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_with_retries_honors_429_with_retry_after():
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.HTTPStatusError(
                "rl",
                request=httpx.Request("POST", "https://example/"),
                response=httpx.Response(429, headers={"retry-after": "0"}),
            )
        return "ok"

    policy = RetryPolicy.from_settings(get_settings()).with_overrides(
        max_retries=2, initial_backoff_ms=1
    )
    result = await with_retries(op, policy=policy)
    assert result == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_with_retries_429_without_retry_after_is_not_retried():
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        raise httpx.HTTPStatusError(
            "rl",
            request=httpx.Request("POST", "https://example/"),
            response=httpx.Response(429),
        )

    policy = RetryPolicy.from_settings(get_settings()).with_overrides(
        max_retries=2, initial_backoff_ms=1
    )
    with pytest.raises(UpstreamRequestError):
        await with_retries(op, policy=policy)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_with_retries_total_timeout_raises_gateway_timeout():
    import asyncio

    async def op() -> str:
        await asyncio.sleep(0.5)
        return "ok"

    policy = RetryPolicy.from_settings(get_settings()).with_overrides(
        total_timeout_seconds=0.05, initial_backoff_ms=1
    )
    with pytest.raises(GatewayTimeoutError):
        await with_retries(op, policy=policy)
