"""Fixed asset API schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AssetStatus = Literal[
    "draft",
    "submitted",
    "active",
    "disposal_pending",
    "disposed",
    "rejected",
    "cancelled",
]
DisposalStatus = Literal["pending", "approved", "rejected"]


class AssetCategoryCreate(BaseModel):
    company_id: uuid.UUID
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    cost_account_id: uuid.UUID
    accumulated_depreciation_account_id: uuid.UUID
    depreciation_expense_account_id: uuid.UUID
    gain_loss_account_id: uuid.UUID


class AssetCategoryUpdate(BaseModel):
    version: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    cost_account_id: uuid.UUID | None = None
    accumulated_depreciation_account_id: uuid.UUID | None = None
    depreciation_expense_account_id: uuid.UUID | None = None
    gain_loss_account_id: uuid.UUID | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> AssetCategoryUpdate:
        if not self.model_fields_set:
            raise ValueError("At least one category field must be supplied")
        return self


class AssetCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    company_id: uuid.UUID
    code: str
    name: str
    description: str | None
    cost_account_id: uuid.UUID
    accumulated_depreciation_account_id: uuid.UUID
    depreciation_expense_account_id: uuid.UUID
    gain_loss_account_id: uuid.UUID
    is_active: bool
    created_at: datetime
    version: int


class FixedAssetCreate(BaseModel):
    company_id: uuid.UUID
    category_id: uuid.UUID
    asset_number: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    serial_reference: str | None = Field(default=None, max_length=120)
    acquisition_date: date
    in_service_date: date
    cost: Decimal = Field(gt=0, max_digits=20, decimal_places=2)
    residual_value: Decimal = Field(
        default=Decimal("0.00"), ge=0, max_digits=20, decimal_places=2
    )
    useful_life_months: int = Field(ge=1, le=1200)

    @model_validator(mode="after")
    def validate_dates_and_residual(self) -> FixedAssetCreate:
        if self.in_service_date < self.acquisition_date:
            raise ValueError("The in-service date must not precede the acquisition date")
        if self.residual_value > self.cost:
            raise ValueError("The residual value must not exceed the cost")
        return self


class FixedAssetUpdate(BaseModel):
    version: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    serial_reference: str | None = Field(default=None, max_length=120)
    acquisition_date: date | None = None
    in_service_date: date | None = None
    cost: Decimal | None = Field(default=None, gt=0, max_digits=20, decimal_places=2)
    residual_value: Decimal | None = Field(
        default=None, ge=0, max_digits=20, decimal_places=2
    )
    useful_life_months: int | None = Field(default=None, ge=1, le=1200)

    @model_validator(mode="after")
    def require_change(self) -> FixedAssetUpdate:
        if not self.model_fields_set:
            raise ValueError("At least one asset field must be supplied")
        return self


class FixedAssetResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    company_id: uuid.UUID
    category_id: uuid.UUID
    category_code: str
    category_name: str
    asset_number: str
    name: str
    description: str | None
    serial_reference: str | None
    acquisition_date: date
    in_service_date: date
    cost: Decimal
    residual_value: Decimal
    useful_life_months: int
    currency: str
    status: AssetStatus
    cost_account_id: uuid.UUID
    accumulated_depreciation_account_id: uuid.UUID
    depreciation_expense_account_id: uuid.UUID
    gain_loss_account_id: uuid.UUID
    accumulated_depreciation: Decimal
    net_book_value: Decimal
    requested_by: uuid.UUID
    submitted_by: uuid.UUID | None
    submitted_at: datetime | None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None
    cancelled_by: uuid.UUID | None
    cancelled_at: datetime | None
    capitalization_journal_id: uuid.UUID | None
    disposal_journal_id: uuid.UUID | None
    created_at: datetime
    version: int


class AssetPage(BaseModel):
    items: list[FixedAssetResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


class AssetDecision(BaseModel):
    version: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=1000)


class AssetCapitalizationRequest(BaseModel):
    version: int | None = Field(default=None, ge=1)
    funding_account_id: uuid.UUID
    reference: str = Field(min_length=2, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class AssetDepreciationCreate(BaseModel):
    period_start: date
    period_end: date
    reference: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_period(self) -> AssetDepreciationCreate:
        if self.period_end <= self.period_start:
            raise ValueError("The depreciation period end must follow the period start")
        return self


class AssetDepreciationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    company_id: uuid.UUID
    asset_id: uuid.UUID
    period_start: date
    period_end: date
    amount: Decimal
    journal_id: uuid.UUID
    reference: str | None
    posted_by: uuid.UUID
    posted_at: datetime
    created_at: datetime


class AssetDisposalCreate(BaseModel):
    version: int | None = Field(default=None, ge=1)
    disposal_date: date
    proceeds_amount: Decimal = Field(ge=0, max_digits=20, decimal_places=2)
    proceeds_account_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=500)


class AssetDisposalDecision(BaseModel):
    version: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=1000)


class AssetDisposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    company_id: uuid.UUID
    asset_id: uuid.UUID
    disposal_date: date
    proceeds_amount: Decimal
    proceeds_account_id: uuid.UUID
    reason: str
    status: DisposalStatus
    requested_by: uuid.UUID
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None
    journal_id: uuid.UUID | None
    created_at: datetime
    version: int
