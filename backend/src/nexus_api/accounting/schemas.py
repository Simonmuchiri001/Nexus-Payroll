"""Accounting API schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AccountCreate(BaseModel):
    company_id: uuid.UUID
    code: str = Field(min_length=2, max_length=20, pattern=r"^[0-9A-Za-z-]+$")
    name: str = Field(min_length=2, max_length=200)
    account_type: str = Field(pattern="^(asset|liability|equity|revenue|expense)$")
    normal_balance: str = Field(pattern="^(debit|credit)$")


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    code: str
    name: str
    account_type: str
    normal_balance: str
    is_system: bool
    is_active: bool


class FiscalPeriodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    start_date: date
    end_date: date
    status: str


class JournalLineCreate(BaseModel):
    account_id: uuid.UUID
    description: str = Field(min_length=1, max_length=300)
    debit: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=20, decimal_places=2)
    credit: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=20, decimal_places=2)
    cost_centre_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def exactly_one_side(self) -> JournalLineCreate:
        if (self.debit == 0) == (self.credit == 0):
            raise ValueError("A journal line must contain exactly one non-zero debit or credit")
        return self


class JournalCreate(BaseModel):
    company_id: uuid.UUID
    journal_date: date
    description: str = Field(min_length=3, max_length=500)
    reference: str | None = Field(default=None, max_length=100)
    lines: list[JournalLineCreate] = Field(min_length=2, max_length=200)

    @model_validator(mode="after")
    def must_balance(self) -> JournalCreate:
        debit = sum((line.debit for line in self.lines), Decimal("0.00"))
        credit = sum((line.credit for line in self.lines), Decimal("0.00"))
        if debit != credit:
            raise ValueError("Journal debits and credits must be equal")
        return self


class JournalLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    line_number: int
    account_id: uuid.UUID
    description: str
    debit: Decimal
    credit: Decimal
    cost_centre_id: uuid.UUID | None


class JournalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    journal_number: str
    journal_date: date
    description: str
    reference: str | None
    status: str
    currency: str
    total_debit: Decimal
    total_credit: Decimal
    source_type: str | None
    source_id: uuid.UUID | None
    created_by: uuid.UUID
    posted_by: uuid.UUID | None
    posted_at: datetime | None
    created_at: datetime


class JournalDetail(BaseModel):
    journal: JournalResponse
    lines: list[JournalLineResponse]


class JournalPage(BaseModel):
    items: list[JournalResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


class TrialBalanceRow(BaseModel):
    account_id: uuid.UUID
    code: str
    name: str
    account_type: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


class TrialBalanceResponse(BaseModel):
    company_id: uuid.UUID
    as_of: date
    currency: str
    rows: list[TrialBalanceRow]
    total_debit: Decimal
    total_credit: Decimal
    balanced: bool
