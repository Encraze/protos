import httpx
import pytest
import respx

from app.config import get_settings
from app.pricing.sources import LiteLLMJsonSource, OpenRouterSource
from app.pricing.vendor_catalog import VendorCatalog, VendorRate
from app.security.models import Provider


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_source_parses_known_models():
    settings = get_settings()
    body = {
        "data": [
            {
                "id": "openai/gpt-4o",
                "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
                "top_provider": {"id": "openai"},
            },
            {
                "id": "anthropic/claude-opus-4-7",
                "pricing": {"prompt": "0.000015", "completion": "0.000075"},
                "top_provider": {"id": "anthropic"},
            },
            {
                "id": "google/gemini-1.5-pro",
                "pricing": {"prompt": "0.00000125", "completion": "0.000005"},
                "top_provider": {"id": "google"},
            },
            {
                "id": "mistralai/something",
                "pricing": {"prompt": "0.0000005", "completion": "0.0000015"},
                "top_provider": {"id": "mistralai"},
            },
        ]
    }
    respx.get(settings.openrouter_models_url).mock(
        return_value=httpx.Response(200, json=body)
    )
    src = OpenRouterSource(settings)
    rates = await src.fetch()
    assert (Provider.OPENAI, "gpt-4o") in rates
    assert rates[(Provider.OPENAI, "gpt-4o")] == VendorRate(2, 10)
    assert rates[(Provider.ANTHROPIC, "claude-opus-4-7")] == VendorRate(15, 75)
    assert rates[(Provider.GEMINI, "gemini-1.5-pro")] == VendorRate(1, 5)
    assert all(p in (Provider.OPENAI, Provider.ANTHROPIC, Provider.GEMINI) for (p, _) in rates)


@pytest.mark.asyncio
@respx.mock
async def test_litellm_source_parses_known_models():
    settings = get_settings()
    body = {
        "gpt-4o": {
            "input_cost_per_token": 0.0000025,
            "output_cost_per_token": 0.00001,
            "litellm_provider": "openai",
        },
        "claude-opus-4-7": {
            "input_cost_per_token": 0.000015,
            "output_cost_per_token": 0.000075,
            "litellm_provider": "anthropic",
        },
        "gemini-1.5-pro": {
            "input_cost_per_token": 0.00000125,
            "output_cost_per_token": 0.000005,
            "litellm_provider": "vertex_ai-language-models",
        },
        "ignored-model": {
            "input_cost_per_token": 0.0,
            "output_cost_per_token": 0.0,
            "litellm_provider": "cohere",
        },
        "sample_spec": {"description": "metadata key, not a model"},
    }
    respx.get(settings.litellm_pricing_url).mock(
        return_value=httpx.Response(200, json=body)
    )
    src = LiteLLMJsonSource(settings)
    rates = await src.fetch()
    assert rates[(Provider.OPENAI, "gpt-4o")] == VendorRate(2, 10)
    assert rates[(Provider.ANTHROPIC, "claude-opus-4-7")] == VendorRate(15, 75)
    assert rates[(Provider.GEMINI, "gemini-1.5-pro")] == VendorRate(1, 5)


@pytest.mark.asyncio
@respx.mock
async def test_catalog_refresh_uses_openrouter_first_then_falls_back():
    settings = get_settings()
    respx.get(settings.openrouter_models_url).mock(side_effect=httpx.ConnectError("boom"))
    respx.get(settings.litellm_pricing_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "gpt-4o": {
                    "input_cost_per_token": 0.0000025,
                    "output_cost_per_token": 0.00001,
                    "litellm_provider": "openai",
                }
            },
        )
    )
    cat = VendorCatalog(settings)
    snap = await cat.refresh()
    assert snap.source == "litellm"
    assert cat.get(Provider.OPENAI, "gpt-4o") == VendorRate(2, 10)


@pytest.mark.asyncio
@respx.mock
async def test_catalog_all_sources_fail_keeps_empty():
    settings = get_settings()
    respx.get(settings.openrouter_models_url).mock(side_effect=httpx.ConnectError("a"))
    respx.get(settings.litellm_pricing_url).mock(side_effect=httpx.ConnectError("b"))
    cat = VendorCatalog(settings)
    snap = await cat.refresh()
    assert snap.source == "unavailable"
    assert cat.get(Provider.OPENAI, "gpt-4o") is None


@pytest.mark.asyncio
@respx.mock
async def test_catalog_keeps_prior_rates_when_refresh_fails():
    settings = get_settings()
    respx.get(settings.openrouter_models_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "openai/gpt-4o",
                        "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
                        "top_provider": {"id": "openai"},
                    }
                ]
            },
        )
    )
    cat = VendorCatalog(settings)
    await cat.refresh()
    assert cat.get(Provider.OPENAI, "gpt-4o") == VendorRate(2, 10)
    respx.get(settings.openrouter_models_url).mock(side_effect=httpx.ConnectError("a"))
    respx.get(settings.litellm_pricing_url).mock(side_effect=httpx.ConnectError("b"))
    snap = await cat.refresh()
    assert snap.source == "openrouter"
    assert cat.get(Provider.OPENAI, "gpt-4o") == VendorRate(2, 10)
