from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.security.models import ApiKeyMode, Provider


class ProviderKeyCreate(BaseModel):
    provider: Provider
    label: str = Field(min_length=1, max_length=120)
    secret: str = Field(min_length=8, max_length=4096)


class ProviderKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: Provider
    label: str
    last_four: str
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list)
    mode: ApiKeyMode = ApiKeyMode.LIVE
    expires_at: datetime | None = None


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    prefix: str
    scopes: list[str]
    mode: ApiKeyMode
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyIssueResponse(ApiKeyResponse):
    token: str
