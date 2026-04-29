from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.proxy.dependencies import require_api_key
from app.proxy.schemas import ChatCompletionRequest, EstimateResponse
from app.proxy.service import ProxyService
from app.security.models import ApiKey

router = APIRouter(prefix="/v1", tags=["proxy"])

_service: ProxyService | None = None


def get_service(settings: Annotated[Settings, Depends(get_settings)]) -> ProxyService:
    global _service
    if _service is None:
        _service = ProxyService(settings)
    return _service


def reset_service() -> None:
    global _service
    _service = None


@router.post("/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    api_key: Annotated[ApiKey, Depends(require_api_key(scope="chat:write"))],
    db: Annotated[AsyncSession, Depends(get_session)],
    service: Annotated[ProxyService, Depends(get_service)],
):
    request_body = body.model_dump(exclude_unset=False)
    if body.stream:
        provider, secret = await service.prepare_stream(
            db=db, api_key=api_key, request_body=request_body
        )

        async def _stream():
            async for chunk in service.chat_completion_stream(
                api_key=api_key,
                request_body=request_body,
                provider=provider,
                upstream_secret=secret,
            ):
                yield chunk

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    result, response = await service.chat_completion_sync(
        db=db, api_key=api_key, request_body=request_body
    )
    return JSONResponse(
        content=response,
        headers={"X-Request-ID": result.request_id},
    )


@router.post("/estimate", response_model=EstimateResponse)
async def estimate(
    body: ChatCompletionRequest,
    api_key: Annotated[ApiKey, Depends(require_api_key(scope="chat:write"))],
    db: Annotated[AsyncSession, Depends(get_session)],
    service: Annotated[ProxyService, Depends(get_service)],
) -> dict:
    return await service.estimate(
        db=db, api_key=api_key, request_body=body.model_dump(exclude_unset=False)
    )
