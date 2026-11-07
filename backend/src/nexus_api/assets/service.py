"""Asset category, register, depreciation and disposal services."""

from __future__ import annotations

import calendar
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.accounting.models import Account, FiscalPeriod
from nexus_api.accounting.service import (
    post_asset_capitalization,
    post_asset_depreciation,
    post_asset_disposal,
)
from nexus_api.api.deps import PaginationParams
from nexus_api.assets.models import AssetCategory, AssetDepreciation, AssetDisposal, FixedAsset
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
from nexus_api.audit.service import record_audit
from nexus_api.core.errors import ConflictError, NotFoundError, ValidationFailureError
from nexus_api.identity.permissions import PermissionCode
from nexus_api.identity.service import AuthContext, validate_company_access

CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def _month_end(period_start: date) -> date:
    return date(
        period_start.year,
        period_start.month,
        calendar.monthrange(period_start.year, period_start.month)[1],
    )


def _check_version(record: FixedAsset | AssetCategory | AssetDisposal, version: int | None) -> None:
    if version is not None and version != record.version:
        raise ConflictError("This record was changed by another user. Reload it and try again.")


async def _account(
    session: AsyncSession,
    company_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    label: str,
    allowed_types: tuple[str, ...],
    normal_balance: str | None = None,
) -> Account:
    account = await session.get(Account, account_id)
    if account is None or account.company_id != company_id:
        raise ValidationFailureError(f"{label} does not belong to this company.")
    if not account.is_active:
        raise ValidationFailureError(f"{label} must be active.")
    if account.account_type not in allowed_types:
        raise ValidationFailureError(
            f"{label} must be one of the following account types: {', '.join(allowed_types)}."
        )
    if normal_balance is not None and account.normal_balance != normal_balance:
        raise ValidationFailureError(f"{label} must have a {normal_balance} normal balance.")
    return account


async def _validate_category_accounts(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    cost_account_id: uuid.UUID,
    accumulated_depreciation_account_id: uuid.UUID,
    depreciation_expense_account_id: uuid.UUID,
    gain_loss_account_id: uuid.UUID,
) -> None:
    account_ids = {
        cost_account_id,
        accumulated_depreciation_account_id,
        depreciation_expense_account_id,
        gain_loss_account_id,
    }
    if len(account_ids) != 4:
        raise ValidationFailureError(
            "Each category mapping must use a different ledger account."
        )
    await _account(
        session,
        company_id,
        cost_account_id,
        label="The asset cost account",
        allowed_types=("asset",),
        normal_balance="debit",
    )
    await _account(
        session,
        company_id,
        accumulated_depreciation_account_id,
        label="The accumulated depreciation account",
        allowed_types=("asset",),
        normal_balance="credit",
    )
    await _account(
        session,
        company_id,
        depreciation_expense_account_id,
        label="The depreciation expense account",
        allowed_types=("expense",),
        normal_balance="debit",
    )
    await _account(
        session,
        company_id,
        gain_loss_account_id,
        label="The disposal gain/loss account",
        allowed_types=("expense", "revenue"),
    )


async def list_categories(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    include_inactive: bool = False,
) -> list[AssetCategoryResponse]:
    await validate_company_access(session, context, company_id, PermissionCode.ASSET_VIEW)
    filters = [AssetCategory.company_id == company_id]
    if not include_inactive:
        filters.append(AssetCategory.is_active.is_(True))
    categories = (
        await session.scalars(
            select(AssetCategory).where(*filters).order_by(AssetCategory.code)
        )
    ).all()
    return [AssetCategoryResponse.model_validate(category) for category in categories]


async def create_category(
    session: AsyncSession, context: AuthContext, payload: AssetCategoryCreate
) -> AssetCategoryResponse:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.ASSET_MANAGE
    )
    code = payload.code.upper()
    existing = await session.scalar(
        select(AssetCategory.id).where(
            AssetCategory.company_id == company.id,
            AssetCategory.code == code,
        )
    )
    if existing is not None:
        raise ConflictError("An asset category with that code already exists.")
    await _validate_category_accounts(
        session,
        company.id,
        cost_account_id=payload.cost_account_id,
        accumulated_depreciation_account_id=payload.accumulated_depreciation_account_id,
        depreciation_expense_account_id=payload.depreciation_expense_account_id,
        gain_loss_account_id=payload.gain_loss_account_id,
    )
    category = AssetCategory(
        organization_id=context.organization.id,
        company_id=company.id,
        code=code,
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        cost_account_id=payload.cost_account_id,
        accumulated_depreciation_account_id=payload.accumulated_depreciation_account_id,
        depreciation_expense_account_id=payload.depreciation_expense_account_id,
        gain_loss_account_id=payload.gain_loss_account_id,
        is_active=True,
    )
    session.add(category)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="asset_category.created",
        entity_type="asset_category",
        entity_id=category.id,
        metadata={"company_id": str(company.id), "code": category.code},
    )
    return AssetCategoryResponse.model_validate(category)


async def _locked_category(
    session: AsyncSession, context: AuthContext, category_id: uuid.UUID
) -> AssetCategory:
    category = await session.scalar(
        select(AssetCategory)
        .where(
            AssetCategory.id == category_id,
            AssetCategory.organization_id == context.organization.id,
        )
        .with_for_update()
    )
    if category is None:
        raise NotFoundError("Asset category not found.")
    await validate_company_access(
        session, context, category.company_id, PermissionCode.ASSET_MANAGE
    )
    return category


async def update_category(
    session: AsyncSession,
    context: AuthContext,
    category_id: uuid.UUID,
    payload: AssetCategoryUpdate,
) -> AssetCategoryResponse:
    category = await _locked_category(session, context, category_id)
    _check_version(category, payload.version)

    account_fields = {
        field
        for field in (
            "cost_account_id",
            "accumulated_depreciation_account_id",
            "depreciation_expense_account_id",
            "gain_loss_account_id",
        )
        if field in payload.model_fields_set
    }
    if account_fields:
        asset_count = await session.scalar(
            select(func.count(FixedAsset.id)).where(FixedAsset.category_id == category.id)
        )
        if asset_count:
            raise ConflictError(
                "Category account mappings cannot change after assets have been recorded."
            )
        resolved = {
            "cost_account_id": payload.cost_account_id or category.cost_account_id,
            "accumulated_depreciation_account_id": (
                payload.accumulated_depreciation_account_id
                or category.accumulated_depreciation_account_id
            ),
            "depreciation_expense_account_id": (
                payload.depreciation_expense_account_id
                or category.depreciation_expense_account_id
            ),
            "gain_loss_account_id": payload.gain_loss_account_id or category.gain_loss_account_id,
        }
        await _validate_category_accounts(session, category.company_id, **resolved)
        for field, value in resolved.items():
            setattr(category, field, value)

    if payload.is_active is not None and payload.is_active != category.is_active:
        if not payload.is_active:
            pending_assets = await session.scalar(
                select(func.count(FixedAsset.id)).where(
                    FixedAsset.category_id == category.id,
                    FixedAsset.status.in_(("submitted", "active", "disposal_pending")),
                )
            )
            if pending_assets:
                raise ConflictError(
                    "Archive blocked: this category still has submitted, active or "
                    "disposal-pending assets."
                )
        category.is_active = payload.is_active

    if payload.name is not None:
        category.name = payload.name.strip()
    if "description" in payload.model_fields_set:
        category.description = payload.description.strip() if payload.description else None

    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="asset_category.updated",
        entity_type="asset_category",
        entity_id=category.id,
        metadata={"code": category.code, "is_active": category.is_active},
    )
    return AssetCategoryResponse.model_validate(category)


async def _category_map(
    session: AsyncSession, category_ids: set[uuid.UUID]
) -> dict[uuid.UUID, AssetCategory]:
    if not category_ids:
        return {}
    categories = (
        await session.scalars(select(AssetCategory).where(AssetCategory.id.in_(category_ids)))
    ).all()
    return {category.id: category for category in categories}


async def _accumulated_map(
    session: AsyncSession, asset_ids: set[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    if not asset_ids:
        return {}
    rows = (
        await session.execute(
            select(AssetDepreciation.asset_id, func.coalesce(func.sum(AssetDepreciation.amount), 0))
            .where(AssetDepreciation.asset_id.in_(asset_ids))
            .group_by(AssetDepreciation.asset_id)
        )
    ).all()
    return {asset_id: _money(Decimal(str(total))) for asset_id, total in rows}


def _asset_response(
    asset: FixedAsset,
    category: AssetCategory,
    accumulated_depreciation: Decimal,
) -> FixedAssetResponse:
    return FixedAssetResponse(
        id=asset.id,
        organization_id=asset.organization_id,
        company_id=asset.company_id,
        category_id=asset.category_id,
        category_code=category.code,
        category_name=category.name,
        asset_number=asset.asset_number,
        name=asset.name,
        description=asset.description,
        serial_reference=asset.serial_reference,
        acquisition_date=asset.acquisition_date,
        in_service_date=asset.in_service_date,
        cost=asset.cost,
        residual_value=asset.residual_value,
        useful_life_months=asset.useful_life_months,
        currency=asset.currency,
        status=cast(AssetStatus, asset.status),
        cost_account_id=asset.cost_account_id,
        accumulated_depreciation_account_id=asset.accumulated_depreciation_account_id,
        depreciation_expense_account_id=asset.depreciation_expense_account_id,
        gain_loss_account_id=asset.gain_loss_account_id,
        accumulated_depreciation=accumulated_depreciation,
        net_book_value=_money(asset.cost - accumulated_depreciation),
        requested_by=asset.requested_by,
        submitted_by=asset.submitted_by,
        submitted_at=asset.submitted_at,
        decided_by=asset.decided_by,
        decided_at=asset.decided_at,
        decision_note=asset.decision_note,
        cancelled_by=asset.cancelled_by,
        cancelled_at=asset.cancelled_at,
        capitalization_journal_id=asset.capitalization_journal_id,
        disposal_journal_id=asset.disposal_journal_id,
        created_at=asset.created_at,
        version=asset.version,
    )


async def _asset_response_for(session: AsyncSession, asset: FixedAsset) -> FixedAssetResponse:
    category = await session.get(AssetCategory, asset.category_id)
    if category is None:
        raise NotFoundError("The category linked to this asset is missing.")
    accumulated = await _accumulated_map(session, {asset.id})
    return _asset_response(asset, category, accumulated.get(asset.id, Decimal("0.00")))


async def list_assets(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    pagination: PaginationParams,
    status: str | None = None,
    category_id: uuid.UUID | None = None,
) -> AssetPage:
    await validate_company_access(session, context, company_id, PermissionCode.ASSET_VIEW)
    filters = [FixedAsset.company_id == company_id]
    if status:
        filters.append(FixedAsset.status == status)
    if category_id is not None:
        filters.append(FixedAsset.category_id == category_id)
    total = await session.scalar(select(func.count(FixedAsset.id)).where(*filters))
    assets = (
        await session.scalars(
            select(FixedAsset)
            .where(*filters)
            .order_by(FixedAsset.asset_number)
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()
    categories = await _category_map(session, {asset.category_id for asset in assets})
    accumulated = await _accumulated_map(session, {asset.id for asset in assets})
    items: list[FixedAssetResponse] = []
    for asset in assets:
        category = categories.get(asset.category_id)
        if category is None:
            raise NotFoundError("A category linked to a listed asset is missing.")
        items.append(
            _asset_response(
                asset, category, accumulated.get(asset.id, Decimal("0.00"))
            )
        )
    return AssetPage(
        items=items,
        total=total or 0,
        page=pagination.page,
        page_size=pagination.page_size,
    )


async def create_asset(
    session: AsyncSession, context: AuthContext, payload: FixedAssetCreate
) -> FixedAssetResponse:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.ASSET_MANAGE
    )
    category = await session.scalar(
        select(AssetCategory).where(
            AssetCategory.id == payload.category_id,
            AssetCategory.company_id == company.id,
            AssetCategory.organization_id == context.organization.id,
        )
    )
    if category is None:
        raise NotFoundError("Asset category not found for this company.")
    if not category.is_active:
        raise ConflictError("Assets cannot be created in an archived category.")
    duplicate = await session.scalar(
        select(FixedAsset.id).where(
            FixedAsset.company_id == company.id,
            FixedAsset.asset_number == payload.asset_number.strip(),
        )
    )
    if duplicate is not None:
        raise ConflictError("An asset with that number already exists.")

    asset = FixedAsset(
        organization_id=context.organization.id,
        company_id=company.id,
        category_id=category.id,
        asset_number=payload.asset_number.strip(),
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        serial_reference=payload.serial_reference.strip() if payload.serial_reference else None,
        acquisition_date=payload.acquisition_date,
        in_service_date=payload.in_service_date,
        cost=_money(payload.cost),
        residual_value=_money(payload.residual_value),
        useful_life_months=payload.useful_life_months,
        currency=company.base_currency,
        status="draft",
        cost_account_id=category.cost_account_id,
        accumulated_depreciation_account_id=category.accumulated_depreciation_account_id,
        depreciation_expense_account_id=category.depreciation_expense_account_id,
        gain_loss_account_id=category.gain_loss_account_id,
        requested_by=context.user.id,
    )
    session.add(asset)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="asset.created",
        entity_type="fixed_asset",
        entity_id=asset.id,
        metadata={
            "asset_number": asset.asset_number,
            "category_id": str(category.id),
            "cost": str(asset.cost),
        },
    )
    return _asset_response(asset, category, Decimal("0.00"))


async def _locked_asset(
    session: AsyncSession,
    context: AuthContext,
    asset_id: uuid.UUID,
    permission: PermissionCode,
) -> FixedAsset:
    asset = await session.scalar(
        select(FixedAsset)
        .where(
            FixedAsset.id == asset_id,
            FixedAsset.organization_id == context.organization.id,
        )
        .with_for_update()
    )
    if asset is None:
        raise NotFoundError("Asset not found.")
    await validate_company_access(session, context, asset.company_id, permission)
    return asset


async def get_asset(
    session: AsyncSession, context: AuthContext, asset_id: uuid.UUID
) -> FixedAssetResponse:
    asset = await session.scalar(
        select(FixedAsset).where(
            FixedAsset.id == asset_id,
            FixedAsset.organization_id == context.organization.id,
        )
    )
    if asset is None:
        raise NotFoundError("Asset not found.")
    await validate_company_access(session, context, asset.company_id, PermissionCode.ASSET_VIEW)
    return await _asset_response_for(session, asset)


async def update_asset(
    session: AsyncSession,
    context: AuthContext,
    asset_id: uuid.UUID,
    payload: FixedAssetUpdate,
) -> FixedAssetResponse:
    asset = await _locked_asset(session, context, asset_id, PermissionCode.ASSET_MANAGE)
    _check_version(asset, payload.version)
    if asset.status not in ("draft", "rejected"):
        raise ConflictError("Only draft or rejected assets can be edited.")

    cost = payload.cost if payload.cost is not None else asset.cost
    residual = (
        payload.residual_value if payload.residual_value is not None else asset.residual_value
    )
    acquisition = (
        payload.acquisition_date if payload.acquisition_date is not None else asset.acquisition_date
    )
    in_service = (
        payload.in_service_date if payload.in_service_date is not None else asset.in_service_date
    )
    if residual > cost:
        raise ValidationFailureError("The residual value must not exceed the cost.")
    if in_service < acquisition:
        raise ValidationFailureError("The in-service date must not precede the acquisition date.")

    if payload.name is not None:
        asset.name = payload.name.strip()
    if "description" in payload.model_fields_set:
        asset.description = payload.description.strip() if payload.description else None
    if "serial_reference" in payload.model_fields_set:
        asset.serial_reference = (
            payload.serial_reference.strip() if payload.serial_reference else None
        )
    asset.acquisition_date = acquisition
    asset.in_service_date = in_service
    asset.cost = _money(cost)
    asset.residual_value = _money(residual)
    if payload.useful_life_months is not None:
        asset.useful_life_months = payload.useful_life_months

    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="asset.updated",
        entity_type="fixed_asset",
        entity_id=asset.id,
        metadata={"asset_number": asset.asset_number},
    )
    return await _asset_response_for(session, asset)


async def submit_asset(
    session: AsyncSession,
    context: AuthContext,
    asset_id: uuid.UUID,
    payload: AssetDecision,
) -> FixedAssetResponse:
    asset = await _locked_asset(session, context, asset_id, PermissionCode.ASSET_MANAGE)
    _check_version(asset, payload.version)
    if asset.status not in ("draft", "rejected"):
        raise ConflictError(f"An asset in '{asset.status}' status cannot be submitted.")
    asset.status = "submitted"
    asset.submitted_by = context.user.id
    asset.submitted_at = datetime.now(UTC)
    asset.decision_note = None
    asset.decided_by = None
    asset.decided_at = None
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="asset.submitted",
        entity_type="fixed_asset",
        entity_id=asset.id,
        metadata={"asset_number": asset.asset_number},
    )
    return await _asset_response_for(session, asset)


async def approve_asset(
    session: AsyncSession,
    context: AuthContext,
    asset_id: uuid.UUID,
    payload: AssetCapitalizationRequest,
) -> FixedAssetResponse:
    asset = await _locked_asset(session, context, asset_id, PermissionCode.ASSET_APPROVE)
    _check_version(asset, payload.version)
    if asset.status == "active" and asset.capitalization_journal_id is not None:
        return await _asset_response_for(session, asset)
    if asset.status != "submitted":
        raise ConflictError(f"An asset in '{asset.status}' status cannot be approved.")
    if asset.requested_by == context.user.id:
        raise ConflictError(
            "The requester cannot approve their own asset. Separation of duties is required."
        )
    journal = await post_asset_capitalization(
        session,
        context,
        asset,
        funding_account_id=payload.funding_account_id,
        reference=payload.reference,
    )
    asset.status = "active"
    asset.decided_by = context.user.id
    asset.decided_at = datetime.now(UTC)
    asset.decision_note = payload.note.strip() if payload.note else None
    asset.capitalization_journal_id = journal.id
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="asset.approved",
        entity_type="fixed_asset",
        entity_id=asset.id,
        metadata={
            "asset_number": asset.asset_number,
            "journal_id": str(journal.id),
            "journal_number": journal.journal_number,
            "cost": str(asset.cost),
        },
    )
    return await _asset_response_for(session, asset)


async def reject_asset(
    session: AsyncSession,
    context: AuthContext,
    asset_id: uuid.UUID,
    payload: AssetDecision,
) -> FixedAssetResponse:
    asset = await _locked_asset(session, context, asset_id, PermissionCode.ASSET_APPROVE)
    _check_version(asset, payload.version)
    if asset.status != "submitted":
        raise ConflictError(f"An asset in '{asset.status}' status cannot be rejected.")
    if asset.requested_by == context.user.id:
        raise ConflictError(
            "The requester cannot reject their own submission. Separation of duties is required."
        )
    asset.status = "rejected"
    asset.decided_by = context.user.id
    asset.decided_at = datetime.now(UTC)
    asset.decision_note = payload.note.strip() if payload.note else None
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="asset.rejected",
        entity_type="fixed_asset",
        entity_id=asset.id,
        metadata={"asset_number": asset.asset_number},
    )
    return await _asset_response_for(session, asset)


async def cancel_asset(
    session: AsyncSession,
    context: AuthContext,
    asset_id: uuid.UUID,
    payload: AssetDecision,
) -> FixedAssetResponse:
    asset = await _locked_asset(session, context, asset_id, PermissionCode.ASSET_MANAGE)
    _check_version(asset, payload.version)
    if asset.status not in ("draft", "submitted"):
        raise ConflictError(f"An asset in '{asset.status}' status cannot be cancelled.")
    asset.status = "cancelled"
    asset.cancelled_by = context.user.id
    asset.cancelled_at = datetime.now(UTC)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="asset.cancelled",
        entity_type="fixed_asset",
        entity_id=asset.id,
        metadata={"asset_number": asset.asset_number},
    )
    return await _asset_response_for(session, asset)


async def list_depreciation(
    session: AsyncSession, context: AuthContext, asset_id: uuid.UUID
) -> list[AssetDepreciationResponse]:
    asset = await session.scalar(
        select(FixedAsset).where(
            FixedAsset.id == asset_id,
            FixedAsset.organization_id == context.organization.id,
        )
    )
    if asset is None:
        raise NotFoundError("Asset not found.")
    await validate_company_access(session, context, asset.company_id, PermissionCode.ASSET_VIEW)
    rows = (
        await session.scalars(
            select(AssetDepreciation)
            .where(AssetDepreciation.asset_id == asset.id)
            .order_by(AssetDepreciation.period_start)
        )
    ).all()
    return [AssetDepreciationResponse.model_validate(row) for row in rows]


async def create_depreciation(
    session: AsyncSession,
    context: AuthContext,
    asset_id: uuid.UUID,
    payload: AssetDepreciationCreate,
) -> AssetDepreciationResponse:
    asset = await _locked_asset(session, context, asset_id, PermissionCode.ASSET_DEPRECIATE)
    if asset.status != "active":
        raise ConflictError(
            f"Depreciation can only be posted for active assets; this asset is '{asset.status}'."
        )
    period_start = payload.period_start
    if period_start.day != 1 or payload.period_end != _month_end(period_start):
        raise ValidationFailureError(
            "The depreciation period must cover exactly one calendar month."
        )
    service_month = asset.in_service_date.replace(day=1)
    if period_start < service_month:
        raise ValidationFailureError(
            "The depreciation period cannot precede the asset's in-service month."
        )
    existing = await session.scalar(
        select(AssetDepreciation).where(
            AssetDepreciation.asset_id == asset.id,
            AssetDepreciation.period_start == period_start,
        )
    )
    if existing is not None:
        return AssetDepreciationResponse.model_validate(existing)

    depreciable_base = _money(asset.cost - asset.residual_value)
    posted_rows = await session.scalar(
        select(func.count(AssetDepreciation.id)).where(AssetDepreciation.asset_id == asset.id)
    )
    accumulated = await session.scalar(
        select(func.coalesce(func.sum(AssetDepreciation.amount), 0)).where(
            AssetDepreciation.asset_id == asset.id
        )
    )
    remaining = _money(depreciable_base - _money(Decimal(str(accumulated or 0))))
    if remaining <= 0:
        raise ConflictError("This asset is fully depreciated.")
    monthly = _money(depreciable_base / Decimal(asset.useful_life_months))
    is_final_period = (posted_rows or 0) + 1 >= asset.useful_life_months
    amount = remaining if is_final_period else _money(min(monthly, remaining))
    if amount <= 0:
        raise ConflictError("The calculated depreciation amount is not positive.")

    depreciation = AssetDepreciation(
        id=uuid.uuid4(),
        organization_id=asset.organization_id,
        company_id=asset.company_id,
        asset_id=asset.id,
        period_start=period_start,
        period_end=payload.period_end,
        amount=amount,
        posted_by=context.user.id,
        posted_at=datetime.now(UTC),
        reference=payload.reference.strip() if payload.reference else None,
    )
    journal = await post_asset_depreciation(session, context, asset, depreciation)
    depreciation.journal_id = journal.id
    session.add(depreciation)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="asset.depreciated",
        entity_type="asset_depreciation",
        entity_id=depreciation.id,
        metadata={
            "asset_number": asset.asset_number,
            "period_start": period_start.isoformat(),
            "period_end": payload.period_end.isoformat(),
            "amount": str(amount),
            "journal_id": str(journal.id),
        },
    )
    return AssetDepreciationResponse.model_validate(depreciation)


async def create_disposal(
    session: AsyncSession,
    context: AuthContext,
    asset_id: uuid.UUID,
    payload: AssetDisposalCreate,
) -> AssetDisposalResponse:
    asset = await _locked_asset(session, context, asset_id, PermissionCode.ASSET_DISPOSE)
    _check_version(asset, payload.version)
    if asset.status != "active":
        raise ConflictError(
            f"A disposal can only be requested for an active asset; this asset is '{asset.status}'."
        )
    if payload.disposal_date < asset.acquisition_date or (
        payload.disposal_date < asset.in_service_date
    ):
        raise ValidationFailureError(
            "The disposal date cannot precede the acquisition or in-service date."
        )
    proceeds_account = await _account(
        session,
        asset.company_id,
        payload.proceeds_account_id,
        label="The proceeds account",
        allowed_types=("asset", "revenue"),
    )
    await _ensure_open_period(session, asset.company_id, payload.disposal_date, "disposal")

    disposal = await session.scalar(
        select(AssetDisposal).where(AssetDisposal.asset_id == asset.id).with_for_update()
    )
    if disposal is not None and disposal.status != "rejected":
        raise ConflictError("A disposal request already exists for this asset.")
    if disposal is None:
        disposal = AssetDisposal(
            organization_id=asset.organization_id,
            company_id=asset.company_id,
            asset_id=asset.id,
            disposal_date=payload.disposal_date,
            proceeds_amount=_money(payload.proceeds_amount),
            proceeds_account_id=proceeds_account.id,
            reason=payload.reason.strip(),
            status="pending",
            requested_by=context.user.id,
        )
        session.add(disposal)
    else:
        disposal.disposal_date = payload.disposal_date
        disposal.proceeds_amount = _money(payload.proceeds_amount)
        disposal.proceeds_account_id = proceeds_account.id
        disposal.reason = payload.reason.strip()
        disposal.status = "pending"
        disposal.requested_by = context.user.id
        disposal.decided_by = None
        disposal.decided_at = None
        disposal.decision_note = None
    asset.status = "disposal_pending"
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="asset_disposal.requested",
        entity_type="asset_disposal",
        entity_id=disposal.id,
        metadata={
            "asset_number": asset.asset_number,
            "disposal_date": payload.disposal_date.isoformat(),
            "proceeds_amount": str(disposal.proceeds_amount),
        },
    )
    return AssetDisposalResponse.model_validate(disposal)


async def _disposal_for_asset(
    session: AsyncSession, asset_id: uuid.UUID, *, for_update: bool
) -> AssetDisposal:
    statement = select(AssetDisposal).where(AssetDisposal.asset_id == asset_id)
    if for_update:
        statement = statement.with_for_update()
    disposal = await session.scalar(statement)
    if disposal is None:
        raise NotFoundError("No disposal request exists for this asset.")
    return disposal


async def _ensure_open_period(
    session: AsyncSession, company_id: uuid.UUID, on_date: date, label: str
) -> None:
    period = await session.scalar(
        select(FiscalPeriod).where(
            FiscalPeriod.company_id == company_id,
            FiscalPeriod.start_date <= on_date,
            FiscalPeriod.end_date >= on_date,
        )
    )
    if period is None:
        raise ValidationFailureError(
            f"No fiscal period covers the {label} date. Create the fiscal calendar first."
        )
    if period.status != "open":
        raise ConflictError(f"The {label} date falls in a locked or closed fiscal period.")


async def approve_disposal(
    session: AsyncSession,
    context: AuthContext,
    asset_id: uuid.UUID,
    payload: AssetDisposalDecision,
) -> AssetDisposalResponse:
    asset = await _locked_asset(session, context, asset_id, PermissionCode.ASSET_DISPOSE)
    disposal = await _disposal_for_asset(session, asset.id, for_update=True)
    _check_version(disposal, payload.version)
    if asset.status == "disposed" and disposal.status == "approved":
        return AssetDisposalResponse.model_validate(disposal)
    if asset.status != "disposal_pending" or disposal.status != "pending":
        raise ConflictError("No pending disposal request exists for this asset.")
    if disposal.requested_by == context.user.id:
        raise ConflictError(
            "The requester cannot approve their own disposal. Separation of duties is required."
        )
    journal = await post_asset_disposal(session, context, asset, disposal)
    disposal.status = "approved"
    disposal.decided_by = context.user.id
    disposal.decided_at = datetime.now(UTC)
    disposal.decision_note = payload.note.strip() if payload.note else None
    disposal.journal_id = journal.id
    asset.status = "disposed"
    asset.decided_by = context.user.id
    asset.decided_at = datetime.now(UTC)
    asset.disposal_journal_id = journal.id
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="asset_disposal.approved",
        entity_type="asset_disposal",
        entity_id=disposal.id,
        metadata={
            "asset_number": asset.asset_number,
            "journal_id": str(journal.id),
            "journal_number": journal.journal_number,
            "proceeds_amount": str(disposal.proceeds_amount),
        },
    )
    return AssetDisposalResponse.model_validate(disposal)


async def reject_disposal(
    session: AsyncSession,
    context: AuthContext,
    asset_id: uuid.UUID,
    payload: AssetDisposalDecision,
) -> AssetDisposalResponse:
    asset = await _locked_asset(session, context, asset_id, PermissionCode.ASSET_DISPOSE)
    disposal = await _disposal_for_asset(session, asset.id, for_update=True)
    _check_version(disposal, payload.version)
    if asset.status != "disposal_pending" or disposal.status != "pending":
        raise ConflictError("No pending disposal request exists for this asset.")
    if disposal.requested_by == context.user.id:
        raise ConflictError(
            "The requester cannot reject their own disposal. Separation of duties is required."
        )
    disposal.status = "rejected"
    disposal.decided_by = context.user.id
    disposal.decided_at = datetime.now(UTC)
    disposal.decision_note = payload.note.strip() if payload.note else None
    asset.status = "active"
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="asset_disposal.rejected",
        entity_type="asset_disposal",
        entity_id=disposal.id,
        metadata={"asset_number": asset.asset_number},
    )
    return AssetDisposalResponse.model_validate(disposal)


__all__ = [
    "approve_asset",
    "approve_disposal",
    "cancel_asset",
    "create_asset",
    "create_category",
    "create_depreciation",
    "create_disposal",
    "get_asset",
    "list_assets",
    "list_categories",
    "list_depreciation",
    "reject_asset",
    "reject_disposal",
    "submit_asset",
    "update_asset",
    "update_category",
]
