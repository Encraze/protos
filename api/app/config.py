from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "stage", "prod"]
AuthMode = Literal["stub", "real"]


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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
