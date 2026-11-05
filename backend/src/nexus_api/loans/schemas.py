"""Employee loan and advance API schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LoanKind = Literal["loan", "advance"]
LoanStatus = Literal["pending", "approved", "rejected", "cancelled", "disbursed", "closed"]
InstallmentStatus = Literal["scheduled", "reserved", "deducted"]


class LoanApplicationCreate(BaseModel):
    company_id: uuid.UUID
    employee_id: uuid.UUID
    kind: LoanKind
    principal_amount: Decimal = Field(gt=0, max_digits=20, decimal_places=2)
    interest_rate_annual: Decimal = Field(
        default=Decimal("0.0000"), ge=0, le=100, max_digits=7, decimal_places=4
    )
    term_months: int = Field(ge=1, le=120)
    purpose: str = Field(min_length=3, max_length=500)


class LoanDecision(BaseModel):
    version: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=1000)


class LoanDisbursement(BaseModel):
    version: int | None = Field(default=None, ge=1)
    disbursed_on: date
    first_due_date: date
    reference: str = Field(min_length=2, max_length=100)

    @model_validator(mode="after")
    def validate_due_date(self) -> LoanDisbursement:
        if self.first_due_date < self.disbursed_on:
            raise ValueError("The first deduction date must not precede the disbursement date")
        return self


class LoanInstallmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    loan_id: uuid.UUID
    sequence: int
    due_date: date
    principal_due: Decimal
    interest_due: Decimal
    amount_due: Decimal
    status: InstallmentStatus
    payroll_run_id: uuid.UUID | None
    payroll_result_id: uuid.UUID | None
    deducted_at: datetime | None


class LoanResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    employee_number: str
    employee_name: str
    loan_number: str
    kind: LoanKind
    principal_amount: Decimal
    currency: str
    interest_rate_annual: Decimal
    term_months: int
    purpose: str
    status: LoanStatus
    requested_by: uuid.UUID | None
    requested_by_name: str | None
    decided_by: uuid.UUID | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str | None
    cancelled_by: uuid.UUID | None
    cancelled_at: datetime | None
    disbursed_by: uuid.UUID | None
    disbursed_on: date | None
    disbursed_at: datetime | None
    disbursement_reference: str | None
    first_due_date: date | None
    journal_id: uuid.UUID | None
    closed_at: datetime | None
    total_repayable: Decimal
    outstanding_principal: Decimal
    installments_paid: int
    installments_total: int
    created_at: datetime
    version: int


class LoanPage(BaseModel):
    items: list[LoanResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


class LoanDetailResponse(BaseModel):
    loan: LoanResponse
    installments: list[LoanInstallmentResponse]
