from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TypeVar

import anyio
import httpx

from app.config import Settings
from app.proxy.errors import (
    GatewayError,
    GatewayTimeoutError,
    UpstreamRequestError,
    UpstreamUnavailableError,
)

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    initial_backoff_ms: int
    total_timeout_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> RetryPolicy:
        return cls(
            max_retries=settings.proxy_max_retries,
            initial_backoff_ms=settings.proxy_retry_initial_backoff_ms,
            total_timeout_seconds=settings.proxy_timeout_total_seconds,
        )

    def with_overrides(self, **kwargs: object) -> RetryPolicy:
        return replace(self, **kwargs)  # type: ignore[arg-type]


_RETRYABLE_STATUS = {502, 503, 504}


def _retry_after_seconds(exc: httpx.HTTPStatusError) -> float | None:
    header = exc.response.headers.get("retry-after")
    if header is None:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        return None


def _is_retryable(exc: BaseException) -> tuple[bool, float | None]:
    if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError)):
        return True, None
    if isinstance(exc, httpx.ReadTimeout):
        return True, None
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in _RETRYABLE_STATUS:
            return True, None
        if status == 429:
            ra = _retry_after_seconds(exc)
            if ra is not None:
                return True, ra
            return False, None
    return False, None


def _to_gateway_error(exc: BaseException) -> Exception:
    if isinstance(exc, GatewayError):
        return exc
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in _RETRYABLE_STATUS:
            return UpstreamUnavailableError(f"upstream {status} after retries")
        return UpstreamRequestError(f"upstream returned HTTP {status}")
    if isinstance(exc, httpx.TimeoutException):
        return UpstreamUnavailableError("upstream timeout")
    if isinstance(exc, httpx.HTTPError):
        return UpstreamUnavailableError(f"upstream connection error: {exc}")
    return UpstreamUnavailableError(f"upstream error: {exc}")


async def with_retries(
    op: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
) -> T:
    attempts_allowed = 1 + policy.max_retries
    base = policy.initial_backoff_ms / 1000.0
    try:
        with anyio.fail_after(policy.total_timeout_seconds):
            last_exc: BaseException | None = None
            for attempt in range(1, attempts_allowed + 1):
                try:
                    return await op()
                except Exception as exc:
                    last_exc = exc
                    retryable, retry_after = _is_retryable(exc)
                    if not retryable or attempt == attempts_allowed:
                        raise _to_gateway_error(exc) from exc
                    backoff = base * (2 ** (attempt - 1))
                    backoff += random.uniform(0, 0.1 * base)
                    if retry_after is not None:
                        backoff = min(backoff, retry_after)
                    await asyncio.sleep(backoff)
            assert last_exc is not None
            raise _to_gateway_error(last_exc)
    except TimeoutError as exc:
        raise GatewayTimeoutError("upstream call exceeded total timeout") from exc
