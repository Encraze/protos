from dataclasses import dataclass
from urllib.parse import urlencode

from app.auth.providers.base import OAuthUserInfo


@dataclass(frozen=True)
class SeededUser:
    provider: str
    provider_user_id: str
    email: str
    name: str


SEEDED_USERS: list[SeededUser] = [
    SeededUser("google", "alice-g-1", "alice@protos.dev", "Alice"),
    SeededUser("google", "bob-g-1", "bob@protos.dev", "Bob"),
    SeededUser("google", "carol-g-1", "carol@protos.dev", "Carol"),
    SeededUser("github", "dave-gh-1", "dave@protos.dev", "Dave"),
]


class StubProvider:
    def __init__(self, name: str, public_url_base: str = "") -> None:
        self.name = name
        self.public_url_base = public_url_base.rstrip("/")

    def authorization_url(self, state: str) -> str:
        params = urlencode({"provider": self.name, "state": state})
        return f"{self.public_url_base}/auth/stub/picker?{params}"

    async def exchange_code(self, code: str) -> OAuthUserInfo:
        for user in SEEDED_USERS:
            if user.provider == self.name and user.provider_user_id == code:
                return OAuthUserInfo(
                    provider=user.provider,
                    provider_user_id=user.provider_user_id,
                    email=user.email,
                    name=user.name,
                )
        raise ValueError(f"unknown stub code for provider {self.name}: {code}")


def seeded_users_for(provider: str) -> list[SeededUser]:
    return [u for u in SEEDED_USERS if u.provider == provider]
