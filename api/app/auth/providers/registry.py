from app.auth.providers.base import OAuthProvider
from app.auth.providers.stub import StubProvider
from app.config import Settings

SUPPORTED_PROVIDERS: tuple[str, ...] = ("google", "github")


def get_provider(settings: Settings, name: str) -> OAuthProvider:
    if name not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported provider: {name}")
    if settings.auth_mode == "stub":
        return StubProvider(name, public_url_base=settings.api_public_url)
    raise NotImplementedError(f"real provider {name} not implemented yet")
