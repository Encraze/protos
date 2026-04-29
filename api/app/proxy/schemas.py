from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    max_tokens: int | None = None


class EstimateResponse(BaseModel):
    model: str
    provider: str
    input_tokens: int
    input_cost_micros: int | None
    max_output_tokens: int | None = None
    max_output_cost_micros: int | None = None
    currency: str
    pricing_source: str
    pricing_fetched_at: str | None
