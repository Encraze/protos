import pytest

from app.proxy.errors import UnsupportedModelError
from app.proxy.registry import resolve_provider, supported_models
from app.security.models import Provider


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gpt-4o", Provider.OPENAI),
        ("gpt-4o-mini", Provider.OPENAI),
        ("o1-preview", Provider.OPENAI),
        ("o3-mini", Provider.OPENAI),
        ("claude-opus-4-7", Provider.ANTHROPIC),
        ("claude-3-5-sonnet-20241022", Provider.ANTHROPIC),
        ("gemini-1.5-pro", Provider.GEMINI),
        ("gemini-2.0-flash", Provider.GEMINI),
    ],
)
def test_resolve_provider_known_models(model, expected):
    assert resolve_provider(model) == expected


def test_resolve_provider_unknown_raises_with_supported_list():
    with pytest.raises(UnsupportedModelError) as ei:
        resolve_provider("foobar-7b")
    assert "foobar-7b" in str(ei.value)
    assert ei.value.supported == supported_models()
    assert "gpt-4o" in ei.value.supported
