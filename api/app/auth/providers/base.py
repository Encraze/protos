from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OAuthUserInfo:
    provider: str
    provider_user_id: str
    email: str
    name: str | None


class OAuthProvider(Protocol):
    name: str

    def authorization_url(self, state: str) -> str: ...

    async def exchange_code(self, code: str) -> OAuthUserInfo: ...
