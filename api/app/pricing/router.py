from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.identity.models import Membership
from app.pricing.dependencies import get_vendor_catalog, require_admin_membership
from app.pricing.vendor_catalog import CatalogSnapshot, VendorCatalog
from app.security.models import Provider

router = APIRouter(prefix="/v1/admin/pricing-catalog", tags=["pricing-admin"])


class RateOut(BaseModel):
    provider: Provider
    model: str
    input_micros_per_token: int
    output_micros_per_token: int


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    source: str
    fetched_at: str | None
    version: str | None
    rates: list[RateOut]


def _serialize(snap: CatalogSnapshot) -> SnapshotOut:
    return SnapshotOut(
        source=snap.source,
        fetched_at=snap.fetched_at.isoformat() if snap.fetched_at else None,
        version=snap.version,
        rates=sorted(
            (
                RateOut(
                    provider=p,
                    model=m,
                    input_micros_per_token=r.input_micros_per_token,
                    output_micros_per_token=r.output_micros_per_token,
                )
                for (p, m), r in snap.rates.items()
            ),
            key=lambda x: (x.provider.value, x.model),
        ),
    )


@router.get("", response_model=SnapshotOut)
async def get_catalog(
    _admin: Annotated[Membership, Depends(require_admin_membership)],
    catalog: Annotated[VendorCatalog, Depends(get_vendor_catalog)],
) -> SnapshotOut:
    return _serialize(catalog.snapshot())


@router.post("/refresh", response_model=SnapshotOut)
async def refresh_catalog(
    _admin: Annotated[Membership, Depends(require_admin_membership)],
    catalog: Annotated[VendorCatalog, Depends(get_vendor_catalog)],
) -> SnapshotOut:
    snap = await catalog.refresh()
    if snap.source == "unavailable" and not snap.rates:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="all pricing sources failed and no prior snapshot exists",
        )
    return _serialize(snap)
