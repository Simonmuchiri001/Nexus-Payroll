"""Typed representations of the authoritative API responses."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type label
    expires_in: int
    refresh_expires_in: int


class UserSummary(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_platform_admin: bool


class CompanySummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    base_currency: str
    country_code: str
    status: str


class MeResponse(BaseModel):
    user: UserSummary
    organization_id: uuid.UUID
    organization_slug: str
    organization_name: str
    membership_id: uuid.UUID
    permissions: list[str]
    companies: list[CompanySummary]

    def can(self, permission: str) -> bool:
        return permission in self.permissions


class Employee(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    employee_number: str
    first_name: str
    middle_name: str | None
    last_name: str
    work_email: str | None
    job_title: str
    hire_date: date
    termination_date: date | None
    status: str
    is_payroll_eligible: bool
    version: int

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(part for part in parts if part)


class EmployeePage(BaseModel):
    items: list[Employee]
    total: int
    page: int
    page_size: int


class Compensation(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
    base_salary: Decimal
    housing_allowance: Decimal
    transport_allowance: Decimal
    other_allowance: Decimal
    currency: str
    effective_from: date
    effective_to: date | None
    status: str


class PayGroup(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    code: str
    name: str
    frequency: str
    currency: str
    status: str


class PayrollPeriod(BaseModel):
    id: uuid.UUID
    pay_group_id: uuid.UUID
    period_start: date
    period_end: date
    payment_date: date
    status: str


class PayrollRun(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    pay_group_id: uuid.UUID
    period_start: date
    period_end: date
    payment_date: date
    run_number: str
    status: str
    currency: str
    employee_count: int
    gross_total: Decimal
    taxable_total: Decimal
    paye_total: Decimal
    employee_statutory_total: Decimal
    employer_statutory_total: Decimal
    deductions_total: Decimal
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


class PayrollResult(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
    employee_number: str | None = None
    employee_name: str | None = None
    currency: str
    days_in_period: int
    payable_days: int
    base_salary: Decimal
    gross: Decimal
    taxable_income: Decimal
    paye: Decimal
    nssf_employee: Decimal
    shif_employee: Decimal
    housing_levy_employee: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    nssf_employer: Decimal
    housing_levy_employer: Decimal
    nita: Decimal
    employer_cost: Decimal


class LeaveType(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_paid: bool
    requires_balance: bool
    annual_entitlement_days: Decimal
    max_carryover_days: Decimal
    status: str
    version: int


class Holiday(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    holiday_date: date
    name: str


class LeaveBalance(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    leave_type_code: str
    leave_type_name: str
    leave_year: int
    entitled_days: Decimal
    carried_over_days: Decimal
    used_days: Decimal
    pending_days: Decimal
    available_days: Decimal
    version: int


class LeaveRequest(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    employee_number: str
    employee_name: str
    leave_type_id: uuid.UUID
    leave_type_code: str
    leave_type_name: str
    leave_year: int
    start_date: date
    end_date: date
    requested_days: Decimal
    balance_tracked: bool
    reason: str
    status: str
    requested_by: uuid.UUID | None
    requested_by_name: str | None
    decided_by: uuid.UUID | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str | None
    cancelled_by: uuid.UUID | None
    cancelled_at: datetime | None
    created_at: datetime
    version: int


class LeaveRequestPage(BaseModel):
    items: list[LeaveRequest]
    total: int
    page: int
    page_size: int


class AttendanceRecord(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    employee_number: str
    employee_name: str
    work_date: date
    status: str
    source: str
    leave_request_id: uuid.UUID | None
    note: str | None
    created_at: datetime
    version: int


class AttendancePage(BaseModel):
    items: list[AttendanceRecord]
    total: int
    page: int
    page_size: int


class LoanInstallment(BaseModel):
    id: uuid.UUID
    loan_id: uuid.UUID
    sequence: int
    due_date: date
    principal_due: Decimal
    interest_due: Decimal
    amount_due: Decimal
    status: str
    payroll_run_id: uuid.UUID | None
    payroll_result_id: uuid.UUID | None
    deducted_at: datetime | None


class Loan(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    employee_number: str
    employee_name: str
    loan_number: str
    kind: str
    principal_amount: Decimal
    currency: str
    interest_rate_annual: Decimal
    term_months: int
    purpose: str
    status: str
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
    items: list[Loan]
    total: int
    page: int
    page_size: int


class LoanDetail(BaseModel):
    loan: Loan
    installments: list[LoanInstallment]


class Account(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    code: str
    name: str
    account_type: str
    normal_balance: str
    is_system: bool
    is_active: bool


class TrialBalanceRow(BaseModel):
    account_id: uuid.UUID
    code: str
    name: str
    account_type: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


class TrialBalance(BaseModel):
    company_id: uuid.UUID
    as_of: date
    currency: str
    rows: list[TrialBalanceRow]
    total_debit: Decimal
    total_credit: Decimal
    balanced: bool


class Journal(BaseModel):
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
    created_at: datetime


class JournalPage(BaseModel):
    items: list[Journal]
    total: int
    page: int
    page_size: int


class RoleAssignment(BaseModel):
    id: uuid.UUID
    role_code: str
    role_name: str
    company_id: uuid.UUID | None
    branch_id: uuid.UUID | None


class Membership(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    user_active: bool
    status: str
    default_company_id: uuid.UUID | None
    role_assignments: list[RoleAssignment]
    created_at: datetime


class MembershipPage(BaseModel):
    items: list[Membership]
    total: int
    page: int
    page_size: int


class Role(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str
    is_system: bool
