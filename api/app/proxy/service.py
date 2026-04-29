from __future__ import annotations

import json as _json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.proxy.adapters.anthropic import AnthropicAdapter
from app.proxy.adapters.base import ProviderAdapter, TimeoutPolicy
from app.proxy.adapters.gemini import GeminiAdapter
from app.proxy.adapters.openai import OpenAIAdapter
from app.proxy.errors import GatewayError, ProviderKeyMissingError
from app.proxy.registry import resolve_provider
from app.proxy.results import (
    AbortReason,
    ChunkContext,
    OnChunk,
    OnCompleted,
    ProxyResult,
)
from app.proxy.retry import RetryPolicy, with_retries
from app.security.models import ApiKey, Provider, ProviderKey
from app.security.vault import Vault, build_vault
from app.usage.models import UsageStatus

logger = structlog.get_logger(__name__)


class ProxyService:
    def __init__(self, settings: Settings, *, vault: Vault | None = None) -> None:
        self._settings = settings
        self._vault = vault or build_vault(settings)
        self._adapters: dict[Provider, ProviderAdapter] = {
            Provider.OPENAI: OpenAIAdapter(),
            Provider.ANTHROPIC: AnthropicAdapter(),
            Provider.GEMINI: GeminiAdapter(),
        }
        self.on_completed: list[OnCompleted] = []
        self.on_chunk: list[OnChunk] = []

    def _timeout_policy(self) -> TimeoutPolicy:
        return TimeoutPolicy(
            connect_seconds=self._settings.proxy_timeout_connect_seconds,
            read_seconds=self._settings.proxy_timeout_read_seconds,
            write_seconds=self._settings.proxy_timeout_read_seconds,
            total_seconds=self._settings.proxy_timeout_total_seconds,
        )

    async def _load_provider_secret(
        self, db: AsyncSession, *, tenant_id: uuid.UUID, provider: Provider
    ) -> str:
        stmt = (
            select(ProviderKey)
            .where(
                ProviderKey.tenant_id == tenant_id,
                ProviderKey.provider == provider,
                ProviderKey.revoked_at.is_(None),
            )
            .order_by(ProviderKey.created_at.desc())
            .limit(1)
        )
        record = (await db.execute(stmt)).scalar_one_or_none()
        if record is None:
            raise ProviderKeyMissingError(provider=provider.value)
        return self._vault.decrypt(bytes(record.encrypted_secret))

    async def _emit_completed(self, result: ProxyResult) -> None:
        for hook in self.on_completed:
            try:
                await hook(result)
            except Exception:
                logger.exception(
                    "proxy.on_completed.hook.failed", request_id=result.request_id
                )

    async def chat_completion_sync(
        self,
        *,
        db: AsyncSession,
        api_key: ApiKey,
        request_body: dict,
    ) -> tuple[ProxyResult, dict]:
        request_id = str(uuid.uuid4())
        started_at = datetime.now(UTC)
        t0 = time.monotonic()
        provider = resolve_provider(request_body["model"])
        secret = await self._load_provider_secret(
            db, tenant_id=api_key.tenant_id, provider=provider
        )
        adapter = self._adapters[provider]
        timeout = self._timeout_policy()
        retry_policy = RetryPolicy.from_settings(self._settings)

        async def _call() -> dict:
            return await adapter.chat_completion(
                request_body,
                upstream_secret=secret,
                timeout=timeout,
                request_id=request_id,
            )

        try:
            response = await with_retries(_call, policy=retry_policy)
        except GatewayError as exc:
            completed_at = datetime.now(UTC)
            result = ProxyResult(
                request_id=request_id,
                tenant_id=api_key.tenant_id,
                api_key_id=api_key.id,
                provider=provider,
                model=request_body["model"],
                status=(
                    UsageStatus.PROVIDER_ERROR
                    if exc.error_type == "upstream_request_error"
                    else UsageStatus.GATEWAY_ERROR
                ),
                streamed=False,
                started_at=started_at,
                completed_at=completed_at,
                latency_ms=int((time.monotonic() - t0) * 1000),
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                error_code=exc.error_type,
                request_body=request_body,
                response_body=None,
            )
            await self._emit_completed(result)
            raise

        response = {**response, "id": request_id}
        usage = response.get("usage") or {}
        completed_at = datetime.now(UTC)
        result = ProxyResult(
            request_id=request_id,
            tenant_id=api_key.tenant_id,
            api_key_id=api_key.id,
            provider=provider,
            model=request_body["model"],
            status=UsageStatus.SUCCESS,
            streamed=False,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=int((time.monotonic() - t0) * 1000),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            error_code=None,
            request_body=request_body,
            response_body=response,
        )
        await self._emit_completed(result)
        return result, response

    async def prepare_stream(
        self,
        *,
        db: AsyncSession,
        api_key: ApiKey,
        request_body: dict,
    ) -> tuple[Provider, str]:
        provider = resolve_provider(request_body["model"])
        secret = await self._load_provider_secret(
            db, tenant_id=api_key.tenant_id, provider=provider
        )
        return provider, secret

    async def chat_completion_stream(
        self,
        *,
        api_key: ApiKey,
        request_body: dict,
        provider: Provider,
        upstream_secret: str,
    ) -> AsyncIterator[bytes]:
        request_id = str(uuid.uuid4())
        started_at = datetime.now(UTC)
        t0 = time.monotonic()
        secret = upstream_secret
        adapter = self._adapters[provider]
        timeout = self._timeout_policy()

        aggregated: dict = {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request_body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": None,
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        prompt_tokens = 0
        completion_tokens = 0
        status = UsageStatus.SUCCESS
        error_code: str | None = None
        abort: AbortReason | None = None

        try:
            async for chunk in adapter.chat_completion_stream(
                request_body,
                upstream_secret=secret,
                timeout=timeout,
                request_id=request_id,
            ):
                payload = {**chunk.payload, "id": request_id}
                delta = (payload.get("choices") or [{}])[0].get("delta") or {}
                content_piece = delta.get("content") or ""
                if content_piece:
                    aggregated["choices"][0]["message"]["content"] += content_piece
                    completion_tokens += max(1, len(content_piece) // 4)

                ctx = ChunkContext(
                    request_id=request_id,
                    aggregated_response=aggregated,
                    latest_delta=delta,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                for hook in self.on_chunk:
                    try:
                        decision = await hook(ctx)
                    except Exception:
                        logger.exception(
                            "proxy.on_chunk.hook.failed", request_id=request_id
                        )
                        continue
                    if isinstance(decision, AbortReason):
                        abort = decision
                        break
                if abort is not None:
                    yield (
                        b"data: "
                        + _json.dumps(
                            {
                                "error": {
                                    "type": abort.kind,
                                    "message": abort.message,
                                    "code": abort.error_code,
                                }
                            }
                        ).encode()
                        + b"\n\n"
                    )
                    status = UsageStatus.BLOCKED
                    error_code = abort.kind
                    break
                yield b"data: " + _json.dumps(payload).encode() + b"\n\n"
        except GatewayError as exc:
            yield (
                b"data: "
                + _json.dumps(
                    {"error": {"type": exc.error_type, "message": exc.message}}
                ).encode()
                + b"\n\n"
            )
            status = (
                UsageStatus.PROVIDER_ERROR
                if exc.error_type == "upstream_request_error"
                else UsageStatus.GATEWAY_ERROR
            )
            error_code = exc.error_type

        if status == UsageStatus.SUCCESS:
            yield b"data: [DONE]\n\n"

        completed_at = datetime.now(UTC)
        aggregated["usage"] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        result = ProxyResult(
            request_id=request_id,
            tenant_id=api_key.tenant_id,
            api_key_id=api_key.id,
            provider=provider,
            model=request_body["model"],
            status=status,
            streamed=True,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=int((time.monotonic() - t0) * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            error_code=error_code,
            request_body=request_body,
            response_body=aggregated,
        )
        await self._emit_completed(result)

    async def estimate(
        self,
        *,
        db: AsyncSession,
        api_key: ApiKey,
        request_body: dict,
    ) -> dict:
        from app.pricing.dependencies import get_vendor_catalog
        from app.tokenizers.anthropic_count import AnthropicCounter
        from app.tokenizers.gemini_count import GeminiCounter
        from app.tokenizers.openai_tiktoken import count_openai_input_tokens

        provider = resolve_provider(request_body["model"])
        model = request_body["model"]
        messages = request_body.get("messages") or []
        max_tokens = request_body.get("max_tokens")

        if provider == Provider.OPENAI:
            input_tokens = count_openai_input_tokens(model=model, messages=messages)
        elif provider == Provider.ANTHROPIC:
            secret = await self._load_provider_secret(
                db, tenant_id=api_key.tenant_id, provider=Provider.ANTHROPIC
            )
            input_tokens = await AnthropicCounter(self._settings).count(
                model=model, messages=messages, upstream_secret=secret
            )
        else:
            secret = await self._load_provider_secret(
                db, tenant_id=api_key.tenant_id, provider=Provider.GEMINI
            )
            input_tokens = await GeminiCounter(self._settings).count(
                model=model, messages=messages, upstream_secret=secret
            )

        catalog = get_vendor_catalog(self._settings)
        rate = catalog.get(provider, model)
        snap = catalog.snapshot()
        out: dict = {
            "model": model,
            "provider": provider.value,
            "input_tokens": input_tokens,
            "input_cost_micros": (
                input_tokens * rate.input_micros_per_token if rate else None
            ),
            "currency": "USD",
            "pricing_source": snap.source,
            "pricing_fetched_at": snap.fetched_at.isoformat() if snap.fetched_at else None,
        }
        if max_tokens is not None:
            out["max_output_tokens"] = max_tokens
            out["max_output_cost_micros"] = (
                max_tokens * rate.output_micros_per_token if rate else None
            )
        return out
