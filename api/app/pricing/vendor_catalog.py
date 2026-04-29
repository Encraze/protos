from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import structlog

from app.config import Settings
from app.security.models import Provider

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class VendorRate:
    input_micros_per_token: int
    output_micros_per_token: int


PricingSource = Literal["openrouter", "litellm", "unavailable"]


@dataclass
class CatalogSnapshot:
    rates: dict[tuple[Provider, str], VendorRate] = field(default_factory=dict)
    source: PricingSource = "unavailable"
    fetched_at: datetime | None = None
    version: str | None = None


class VendorCatalog:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._snapshot = CatalogSnapshot()
        self._lock = asyncio.Lock()

    def get(self, provider: Provider, model: str) -> VendorRate | None:
        return self._snapshot.rates.get((provider, model))

    def snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    async def refresh(self) -> CatalogSnapshot:
        from app.pricing.sources import LiteLLMJsonSource, OpenRouterSource

        sources_by_name = {
            "openrouter": OpenRouterSource(self._settings),
            "litellm": LiteLLMJsonSource(self._settings),
        }
        async with self._lock:
            for name in self._settings.pricing_source_priority:
                src = sources_by_name.get(name)
                if src is None:
                    continue
                try:
                    rates = await src.fetch()
                except Exception as exc:
                    logger.warning("pricing.source.failed", source=name, error=str(exc))
                    continue
                if not rates:
                    logger.warning("pricing.source.empty", source=name)
                    continue
                snap = CatalogSnapshot(
                    rates=rates,
                    source=name,  # type: ignore[arg-type]
                    fetched_at=datetime.now(UTC),
                    version=src.version(),
                )
                self._snapshot = snap
                logger.info(
                    "pricing.refreshed",
                    source=name,
                    rate_count=len(rates),
                    version=snap.version,
                )
                return snap
            if self._snapshot.rates:
                logger.warning(
                    "pricing.refresh.all_failed_keeping_prior",
                    prior_source=self._snapshot.source,
                )
                return self._snapshot
            self._snapshot = CatalogSnapshot()
            logger.error("pricing.refresh.all_failed_no_prior")
            return self._snapshot
