from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "stage", "prod"]
AuthMode = Literal["stub", "real"]
KekProviderName = Literal["env", "aws-kms"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="APP_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Environment = "dev"
    database_url: str
    log_level: str = "INFO"
    log_json: bool = False

    auth_mode: AuthMode = "stub"
    auth_seed: bool = True
    api_public_url: str = ""
    session_secret: str = "dev-secret-change-me-min-32-chars-aaaaaa"
    session_cookie_name: str = "protos_session"
    session_ttl_hours: int = 720
    oauth_state_cookie_name: str = "protos_oauth_state"
    oauth_state_ttl_seconds: int = 600
    frontend_url: str = "http://localhost:8080"

    kek_provider: KekProviderName = "env"
    secrets_master_key: str = ""
    aws_kms_key_id: str = ""
    aws_region: str = "us-east-1"

    proxy_timeout_connect_seconds: float = 5.0
    proxy_timeout_read_seconds: float = 60.0
    proxy_timeout_total_seconds: float = 120.0
    proxy_max_retries: int = 2
    proxy_retry_initial_backoff_ms: int = 250
    proxy_sse_keepalive_seconds: float = 15.0

    pricing_source_priority: list[str] = ["openrouter", "litellm"]
    pricing_refresh_on_startup: bool = True
    openrouter_models_url: str = "https://openrouter.ai/api/v1/models"
    litellm_pricing_url: str = (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/"
        "model_prices_and_context_window_backup.json"
    )

    anthropic_count_tokens_cache_ttl_seconds: int = 300
    gemini_count_tokens_cache_ttl_seconds: int = 300


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
