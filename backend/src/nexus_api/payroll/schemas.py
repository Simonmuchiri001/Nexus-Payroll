"""Payroll API schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PayGroupCreate(BaseModel):
    company_id: uuid.UUID
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=150)
    currency: str = Field(min_length=3, max_length=3)


class PayGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    code: str
    name: str
    frequency: str
    currency: str
    status: str


class PayrollPeriodCreate(BaseModel):
    pay_group_id: uuid.UUID
    period_start: date
    period_end: date
    payment_date: date

    @model_validator(mode="after")
    def validate_dates(self) -> PayrollPeriodCreate:
        if self.period_end < self.period_start:
            raise ValueError("Period end must not precede period start")
        if self.payment_date < self.period_end:
            raise ValueError("Payment date must not precede period end")
        return self


class PayrollPeriodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pay_group_id: uuid.UUID
    period_start: date
    period_end: date
    payment_date: date
    status: str


class RuleSetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    country_code: str
    jurisdiction_code: str
    name: str
    version: str
    effective_from: date
    effective_to: date | None
    status: str
    source_urls: list[str]
    checksum: str
    verified_at: datetime | None
    approved_at: datetime | None


class PayrollRunCreate(BaseModel):
    company_id: uuid.UUID
    pay_group_id: uuid.UUID
    payroll_period_id: uuid.UUID


class PayrollRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    pay_group_id: uuid.UUID
    period_start: date
    period_end: date
    payment_date: date
    run_number: str
    status: str
    currency: str
    rule_set_id: uuid.UUID
    engine_version: str
    employee_count: int
    gross_total: Decimal
    taxable_total: Decimal
    paye_total: Decimal
    employee_statutory_total: Decimal
    employer_statutory_total: Decimal
    deductions_total: Decimal
    loan_deduction_total: Decimal
    benefit_employee_total: Decimal
    benefit_employer_total: Decimal
    net_total: Decimal
    employer_cost_total: Decimal
    prepared_by: uuid.UUID
    approved_by: uuid.UUID | None
    calculated_at: datetime | None
    approved_at: datetime | None
    finalized_at: datetime | None
    posted_at: datetime | None
    journal_id: uuid.UUID | None
    version: int
    created_at: datetime


class PayrollResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    employee_number: str | None = None
    employee_name: str | None = None
    currency: str
    days_in_period: int
    payable_days: int
    base_salary: Decimal
    housing_allowance: Decimal
    transport_allowance: Decimal
    other_allowance: Decimal
    gross: Decimal
    taxable_income: Decimal
    paye: Decimal
    nssf_employee: Decimal
    shif_employee: Decimal
    housing_levy_employee: Decimal
    other_deductions: Decimal
    loan_deduction: Decimal
    loan_principal: Decimal
    loan_interest: Decimal
    benefit_employee_deduction: Decimal
    benefit_employer_contribution: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    nssf_employer: Decimal
    housing_levy_employer: Decimal
    nita: Decimal
    employer_cost: Decimal


class PayrollRunDetail(BaseModel):
    run: PayrollRunResponse
    results: list[PayrollResultResponse]
