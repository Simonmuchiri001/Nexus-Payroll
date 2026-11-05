"""Benefit plan and enrolment API schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

BenefitCategory = Literal["medical", "pension", "insurance", "allowance", "other"]
BenefitPlanStatus = Literal["active", "archived"]
EnrollmentStatus = Literal["active", "ended"]


class BenefitPlanCreate(BaseModel):
    company_id: uuid.UUID
    code: str = Field(min_length=2, max_length=24, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=150)
    category: BenefitCategory
    employee_contribution: Decimal = Field(
        default=Decimal("0.00"), ge=0, max_digits=20, decimal_places=2
    )
    employer_contribution: Decimal = Field(
        default=Decimal("0.00"), ge=0, max_digits=20, decimal_places=2
    )
    description: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_contributions(self) -> BenefitPlanCreate:
        if self.employee_contribution + self.employer_contribution <= 0:
            raise ValueError("At least one contribution must be greater than zero")
        return self


class BenefitPlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    category: BenefitCategory | None = None
    employee_contribution: Decimal | None = Field(
        default=None, ge=0, max_digits=20, decimal_places=2
    )
    employer_contribution: Decimal | None = Field(
        default=None, ge=0, max_digits=20, decimal_places=2
    )
    description: str | None = Field(default=None, max_length=500)
    status: BenefitPlanStatus | None = None
    version: int | None = Field(default=None, ge=1)


class BenefitPlanResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    company_id: uuid.UUID
    code: str
    name: str
    category: BenefitCategory
    employee_contribution: Decimal
    employer_contribution: Decimal
    currency: str
    description: str | None
    status: BenefitPlanStatus
    created_at: datetime
    version: int


class BenefitEnrollmentCreate(BaseModel):
    company_id: uuid.UUID
    employee_id: uuid.UUID
    plan_id: uuid.UUID
    effective_from: date
    effective_to: date | None = None
    employee_contribution: Decimal | None = Field(
        default=None, ge=0, max_digits=20, decimal_places=2
    )
    employer_contribution: Decimal | None = Field(
        default=None, ge=0, max_digits=20, decimal_places=2
    )
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_dates(self) -> BenefitEnrollmentCreate:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("The end date must not precede the start date")
        if (
            self.employee_contribution is not None
            and self.employer_contribution is not None
            and self.employee_contribution + self.employer_contribution <= 0
        ):
            raise ValueError("At least one contribution must be greater than zero")
        return self


class BenefitEnrollmentEnd(BaseModel):
    effective_to: date
    version: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=500)


class BenefitEnrollmentResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    employee_number: str
    employee_name: str
    plan_id: uuid.UUID
    plan_code: str
    plan_name: str
    plan_category: BenefitCategory
    effective_from: date
    effective_to: date | None
    employee_contribution: Decimal
    employer_contribution: Decimal
    currency: str
    status: EnrollmentStatus
    notes: str | None
    ended_by: uuid.UUID | None
    ended_at: datetime | None
    end_note: str | None
    created_at: datetime
    version: int


class BenefitEnrollmentPage(BaseModel):
    items: list[BenefitEnrollmentResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
