from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from app.config import Settings
from app.pricing.vendor_catalog import VendorRate
from app.security.models import Provider

_OPENROUTER_PROVIDER_MAP = {
    "openai": Provider.OPENAI,
    "anthropic": Provider.ANTHROPIC,
    "google": Provider.GEMINI,
    "google-vertex": Provider.GEMINI,
    "google-ai-studio": Provider.GEMINI,
}

_LITELLM_PROVIDER_MAP = {
    "openai": Provider.OPENAI,
    "anthropic": Provider.ANTHROPIC,
    "gemini": Provider.GEMINI,
    "vertex_ai-language-models": Provider.GEMINI,
    "vertex_ai": Provider.GEMINI,
    "google": Provider.GEMINI,
}


def _dollars_to_micros(price: str | float | int) -> int:
    if isinstance(price, str):
        return int(Decimal(price) * Decimal(1_000_000))
    return int(Decimal(str(price)) * Decimal(1_000_000))


class OpenRouterSource:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._etag: str | None = None

    def version(self) -> str | None:
        return self._etag

    async def fetch(self) -> dict[tuple[Provider, str], VendorRate]:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(self._settings.openrouter_models_url)
            r.raise_for_status()
        self._etag = r.headers.get("etag")
        body: dict[str, Any] = r.json()
        rates: dict[tuple[Provider, str], VendorRate] = {}
        for entry in body.get("data", []):
            full_id = entry.get("id") or ""
            if "/" not in full_id:
                continue
            vendor_str, _, model = full_id.partition("/")
            top_provider = (entry.get("top_provider") or {}).get("id") or vendor_str
            provider = _OPENROUTER_PROVIDER_MAP.get(top_provider.lower())
            if provider is None:
                continue
            pricing = entry.get("pricing") or {}
            try:
                rates[(provider, model)] = VendorRate(
                    input_micros_per_token=_dollars_to_micros(pricing["prompt"]),
                    output_micros_per_token=_dollars_to_micros(pricing["completion"]),
                )
            except (KeyError, ValueError, TypeError):
                continue
        return rates


class LiteLLMJsonSource:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._version: str | None = None

    def version(self) -> str | None:
        return self._version

    async def fetch(self) -> dict[tuple[Provider, str], VendorRate]:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(self._settings.litellm_pricing_url)
            r.raise_for_status()
        self._version = r.headers.get("etag") or r.headers.get("last-modified")
        body: dict[str, Any] = r.json()
        rates: dict[tuple[Provider, str], VendorRate] = {}
        for model, entry in body.items():
            if not isinstance(entry, dict):
                continue
            ll_provider = entry.get("litellm_provider")
            if not isinstance(ll_provider, str):
                continue
            provider = _LITELLM_PROVIDER_MAP.get(ll_provider)
            if provider is None:
                continue
            try:
                rates[(provider, model)] = VendorRate(
                    input_micros_per_token=_dollars_to_micros(entry["input_cost_per_token"]),
                    output_micros_per_token=_dollars_to_micros(entry["output_cost_per_token"]),
                )
            except (KeyError, ValueError, TypeError):
                continue
        return rates
