from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter(prefix="/health", tags=["health"])


async def _check_db(session: AsyncSession) -> None:
    await session.execute(text("SELECT 1"))


@router.get("")
async def get_health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        await _check_db(session)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"status": "error", "reason": str(exc)},
        )
    return {"status": "ok"}
