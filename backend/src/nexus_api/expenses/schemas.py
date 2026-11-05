"""Expense category and claim API schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ExpenseCategoryStatus = Literal["active", "archived"]
ExpenseClaimStatus = Literal[
    "draft", "submitted", "approved", "rejected", "reimbursed", "cancelled"
]


class ExpenseCategoryCreate(BaseModel):
    company_id: uuid.UUID
    code: str = Field(min_length=2, max_length=24, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=150)
    expense_account_id: uuid.UUID
    description: str | None = Field(default=None, max_length=500)


class ExpenseCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    expense_account_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=500)
    status: ExpenseCategoryStatus | None = None
    version: int | None = Field(default=None, ge=1)


class ExpenseCategoryResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    company_id: uuid.UUID
    code: str
    name: str
    description: str | None
    expense_account_id: uuid.UUID
    expense_account_code: str
    expense_account_name: str
    status: ExpenseCategoryStatus
    created_at: datetime
    version: int


class ExpenseClaimCreate(BaseModel):
    company_id: uuid.UUID
    employee_id: uuid.UUID
    category_id: uuid.UUID
    expense_date: date
    description: str = Field(min_length=3, max_length=500)
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=2)
    receipt_number: str | None = Field(default=None, max_length=100)
    receipt_date: date | None = None
    receipt_merchant: str | None = Field(default=None, max_length=150)

    @model_validator(mode="after")
    def validate_receipt_date(self) -> ExpenseClaimCreate:
        if self.receipt_date is not None and self.receipt_date < self.expense_date:
            raise ValueError("The receipt date must not precede the expense date")
        return self


class ExpenseClaimUpdate(BaseModel):
    employee_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    expense_date: date | None = None
    description: str | None = Field(default=None, min_length=3, max_length=500)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=20, decimal_places=2)
    receipt_number: str | None = Field(default=None, max_length=100)
    receipt_date: date | None = None
    receipt_merchant: str | None = Field(default=None, max_length=150)
    version: int | None = Field(default=None, ge=1)


class ExpenseDecision(BaseModel):
    version: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=1000)


class ExpenseReimbursement(BaseModel):
    version: int | None = Field(default=None, ge=1)
    paid_on: date
    reference: str = Field(min_length=2, max_length=100)


class ExpenseClaimResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    employee_number: str
    employee_name: str
    category_id: uuid.UUID
    category_code: str
    category_name: str
    expense_account_id: uuid.UUID
    expense_account_code: str
    expense_account_name: str
    claim_number: str
    expense_date: date
    description: str
    amount: Decimal
    currency: str
    receipt_number: str | None
    receipt_date: date | None
    receipt_merchant: str | None
    status: ExpenseClaimStatus
    requested_by: uuid.UUID
    requested_by_name: str
    submitted_at: datetime | None
    decided_by: uuid.UUID | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str | None
    paid_by: uuid.UUID | None
    paid_by_name: str | None
    paid_on: date | None
    paid_at: datetime | None
    payment_reference: str | None
    journal_id: uuid.UUID | None
    cancelled_by: uuid.UUID | None
    cancelled_by_name: str | None
    cancelled_at: datetime | None
    created_at: datetime
    version: int


class ExpenseClaimPage(BaseModel):
    items: list[ExpenseClaimResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
