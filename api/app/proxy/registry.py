from __future__ import annotations

from app.proxy.errors import UnsupportedModelError
from app.security.models import Provider

_PREFIX_TO_PROVIDER: tuple[tuple[str, Provider], ...] = (
    ("gpt-", Provider.OPENAI),
    ("o1", Provider.OPENAI),
    ("o3", Provider.OPENAI),
    ("o4", Provider.OPENAI),
    ("chatgpt-", Provider.OPENAI),
    ("claude-", Provider.ANTHROPIC),
    ("gemini-", Provider.GEMINI),
)

_KNOWN_MODELS: tuple[str, ...] = (
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "o1",
    "o1-preview",
    "o1-mini",
    "o3-mini",
    "claude-opus-4-7",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-2.0-flash",
)


def supported_models() -> list[str]:
    return list(_KNOWN_MODELS)


def resolve_provider(model: str) -> Provider:
    for prefix, provider in _PREFIX_TO_PROVIDER:
        if model.startswith(prefix):
            return provider
    raise UnsupportedModelError(model, supported=supported_models())
