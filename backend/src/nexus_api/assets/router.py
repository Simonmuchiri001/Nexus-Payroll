"""Fixed asset category, register, depreciation and disposal API routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from nexus_api.api.deps import AuthDep, PaginationDep, SessionDep
from nexus_api.assets import service
from nexus_api.assets.schemas import (
    AssetCapitalizationRequest,
    AssetCategoryCreate,
    AssetCategoryResponse,
    AssetCategoryUpdate,
    AssetDecision,
    AssetDepreciationCreate,
    AssetDepreciationResponse,
    AssetDisposalCreate,
    AssetDisposalDecision,
    AssetDisposalResponse,
    AssetPage,
    AssetStatus,
    FixedAssetCreate,
    FixedAssetResponse,
    FixedAssetUpdate,
)

router = APIRouter(prefix="/assets", tags=["Assets"])


@router.get("/categories", response_model=list[AssetCategoryResponse])
async def categories(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    include_inactive: bool = False,
) -> list[AssetCategoryResponse]:
    return await service.list_categories(
        session, context, company_id=company_id, include_inactive=include_inactive
    )


@router.post("/categories", response_model=AssetCategoryResponse, status_code=201)
async def create_category(
    payload: AssetCategoryCreate,
    response: Response,
    session: SessionDep,
    context: AuthDep,
) -> AssetCategoryResponse:
    category = await service.create_category(session, context, payload)
    response.headers["Location"] = f"/api/v1/assets/categories/{category.id}"
    return category


@router.patch("/categories/{category_id}", response_model=AssetCategoryResponse)
async def update_category(
    category_id: uuid.UUID,
    payload: AssetCategoryUpdate,
    session: SessionDep,
    context: AuthDep,
) -> AssetCategoryResponse:
    return await service.update_category(session, context, category_id, payload)


@router.get("", response_model=AssetPage)
async def assets(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    pagination: PaginationDep,
    status: Annotated[AssetStatus | None, Query()] = None,
    category_id: uuid.UUID | None = None,
) -> AssetPage:
    return await service.list_assets(
        session,
        context,
        company_id=company_id,
        pagination=pagination,
        status=status,
        category_id=category_id,
    )


@router.post("", response_model=FixedAssetResponse, status_code=201)
async def create_asset(
    payload: FixedAssetCreate,
    response: Response,
    session: SessionDep,
    context: AuthDep,
) -> FixedAssetResponse:
    asset = await service.create_asset(session, context, payload)
    response.headers["Location"] = f"/api/v1/assets/{asset.id}"
    return asset


@router.get("/{asset_id}", response_model=FixedAssetResponse)
async def asset_detail(
    asset_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> FixedAssetResponse:
    return await service.get_asset(session, context, asset_id)


@router.patch("/{asset_id}", response_model=FixedAssetResponse)
async def update_asset(
    asset_id: uuid.UUID,
    payload: FixedAssetUpdate,
    session: SessionDep,
    context: AuthDep,
) -> FixedAssetResponse:
    return await service.update_asset(session, context, asset_id, payload)


@router.post("/{asset_id}/submit", response_model=FixedAssetResponse)
async def submit_asset(
    asset_id: uuid.UUID,
    payload: AssetDecision,
    session: SessionDep,
    context: AuthDep,
) -> FixedAssetResponse:
    return await service.submit_asset(session, context, asset_id, payload)


@router.post("/{asset_id}/approve", response_model=FixedAssetResponse)
async def approve_asset(
    asset_id: uuid.UUID,
    payload: AssetCapitalizationRequest,
    session: SessionDep,
    context: AuthDep,
) -> FixedAssetResponse:
    return await service.approve_asset(session, context, asset_id, payload)


@router.post("/{asset_id}/reject", response_model=FixedAssetResponse)
async def reject_asset(
    asset_id: uuid.UUID,
    payload: AssetDecision,
    session: SessionDep,
    context: AuthDep,
) -> FixedAssetResponse:
    return await service.reject_asset(session, context, asset_id, payload)


@router.post("/{asset_id}/cancel", response_model=FixedAssetResponse)
async def cancel_asset(
    asset_id: uuid.UUID,
    payload: AssetDecision,
    session: SessionDep,
    context: AuthDep,
) -> FixedAssetResponse:
    return await service.cancel_asset(session, context, asset_id, payload)


@router.get("/{asset_id}/depreciation", response_model=list[AssetDepreciationResponse])
async def depreciation(
    asset_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> list[AssetDepreciationResponse]:
    return await service.list_depreciation(session, context, asset_id)


@router.post("/{asset_id}/depreciate", response_model=AssetDepreciationResponse, status_code=201)
async def depreciate(
    asset_id: uuid.UUID,
    payload: AssetDepreciationCreate,
    session: SessionDep,
    context: AuthDep,
) -> AssetDepreciationResponse:
    return await service.create_depreciation(session, context, asset_id, payload)


@router.post("/{asset_id}/dispose", response_model=AssetDisposalResponse, status_code=201)
async def dispose(
    asset_id: uuid.UUID,
    payload: AssetDisposalCreate,
    session: SessionDep,
    context: AuthDep,
) -> AssetDisposalResponse:
    return await service.create_disposal(session, context, asset_id, payload)


@router.post("/{asset_id}/dispose/approve", response_model=AssetDisposalResponse)
async def approve_disposal(
    asset_id: uuid.UUID,
    payload: AssetDisposalDecision,
    session: SessionDep,
    context: AuthDep,
) -> AssetDisposalResponse:
    return await service.approve_disposal(session, context, asset_id, payload)


@router.post("/{asset_id}/dispose/reject", response_model=AssetDisposalResponse)
async def reject_disposal(
    asset_id: uuid.UUID,
    payload: AssetDisposalDecision,
    session: SessionDep,
    context: AuthDep,
) -> AssetDisposalResponse:
    return await service.reject_disposal(session, context, asset_id, payload)
