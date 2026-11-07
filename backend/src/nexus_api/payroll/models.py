"""Payroll configuration, reproducible runs, results and statutory rule sets."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from nexus_api.database.base import (
    Base,
    OptimisticLockMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class StatutoryRuleSet(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Versioned, effective-dated statutory rules used by the calculation engine."""

    __tablename__ = "statutory_rule_sets"
    __table_args__ = (
        UniqueConstraint(
            "country_code", "jurisdiction_code", "version", name="uq_rule_set_version"
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name="status_valid",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_range_valid",
        ),
        Index(
            "ix_rule_sets_effective",
            "country_code",
            "jurisdiction_code",
            "effective_from",
        ),
    )

    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    jurisdiction_code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    version: Mapped[str] = mapped_column(String(30), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_urls: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PayGroup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "pay_groups"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_pay_group_company_code"),
        UniqueConstraint("id", "company_id", name="uq_pay_group_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_pay_group_company_org",
        ),
        CheckConstraint("frequency = 'monthly'", name="frequency_supported"),
        CheckConstraint("status IN ('active', 'archived')", name="status_valid"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False, default="monthly")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KES")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class PayrollPeriod(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_periods"
    __table_args__ = (
        UniqueConstraint("pay_group_id", "period_start", name="uq_period_group_start"),
        CheckConstraint("period_end >= period_start", name="period_range_valid"),
        CheckConstraint("payment_date >= period_end", name="payment_after_period"),
        CheckConstraint("status IN ('open', 'closed')", name="status_valid"),
    )

    pay_group_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pay_groups.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")


class PayrollRun(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    __tablename__ = "payroll_runs"
    __table_args__ = (
        UniqueConstraint("company_id", "run_number", name="uq_payroll_run_company_number"),
        UniqueConstraint("id", "company_id", name="uq_payroll_run_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_payroll_run_company_org",
        ),
        ForeignKeyConstraint(
            ["pay_group_id", "company_id"],
            ["pay_groups.id", "pay_groups.company_id"],
            ondelete="RESTRICT",
            name="fk_payroll_run_pay_group_company",
        ),
        CheckConstraint(
            "status IN ('draft', 'calculated', 'approved', 'finalized', 'posted', 'reversed')",
            name="status_valid",
        ),
        CheckConstraint("gross_total >= 0", name="gross_non_negative"),
        CheckConstraint("net_total >= 0", name="net_non_negative"),
        CheckConstraint("loan_deduction_total >= 0", name="loan_deduction_non_negative"),
        CheckConstraint(
            "benefit_employee_total >= 0 AND benefit_employer_total >= 0",
            name="benefit_totals_non_negative",
        ),
        CheckConstraint(
            "approved_by IS NULL OR approved_by <> prepared_by",
            name="approver_separation_of_duties",
        ),
        Index("ix_payroll_runs_company_status", "company_id", "status"),
        Index("ix_payroll_runs_period", "pay_group_id", "period_start"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    pay_group_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    run_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("statutory_rule_sets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rules_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(30), nullable=False)
    employee_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gross_total: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00")
    )
    taxable_total: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00")
    )
    paye_total: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00")
    )
    employee_statutory_total: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00")
    )
    employer_statutory_total: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00")
    )
    deductions_total: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00")
    )
    loan_deduction_total: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    benefit_employee_total: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    benefit_employer_total: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    unpaid_deduction_total: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    net_total: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00")
    )
    employer_cost_total: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00")
    )
    prepared_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    journal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("journals.id", ondelete="RESTRICT")
    )


class PayrollResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_results"
    __table_args__ = (
        UniqueConstraint("payroll_run_id", "employee_id", name="uq_result_run_employee"),
        ForeignKeyConstraint(
            ["payroll_run_id", "company_id"],
            ["payroll_runs.id", "payroll_runs.company_id"],
            ondelete="CASCADE",
            name="fk_result_run_company",
        ),
        CheckConstraint("gross >= 0", name="gross_non_negative"),
        CheckConstraint("net_pay >= 0", name="net_non_negative"),
        CheckConstraint("net_pay <= gross", name="net_not_above_gross"),
        CheckConstraint("loan_deduction >= 0", name="loan_deduction_non_negative"),
        CheckConstraint(
            "loan_principal >= 0 AND loan_interest >= 0",
            name="loan_split_non_negative",
        ),
        CheckConstraint(
            "loan_deduction = loan_principal + loan_interest",
            name="loan_split_reconciles",
        ),
        CheckConstraint(
            "benefit_employee_deduction >= 0 AND benefit_employer_contribution >= 0",
            name="benefit_amounts_non_negative",
        ),
        CheckConstraint(
            "working_days >= 0 AND paid_days >= 0 AND unpaid_days >= 0 AND unpaid_deduction >= 0",
            name="attendance_non_negative",
        ),
        Index("ix_results_run", "payroll_run_id"),
        Index("ix_results_employee", "employee_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    payroll_run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    days_in_period: Mapped[int] = mapped_column(Integer, nullable=False)
    payable_days: Mapped[int] = mapped_column(Integer, nullable=False)
    working_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    paid_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    unpaid_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    unpaid_deduction: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    base_salary: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    housing_allowance: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    transport_allowance: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    other_allowance: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    gross: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    taxable_income: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    paye: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    nssf_employee: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    shif_employee: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    housing_levy_employee: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    other_deductions: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00")
    )
    loan_deduction: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    loan_principal: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    loan_interest: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    benefit_employee_deduction: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    benefit_employer_contribution: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    total_deductions: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    net_pay: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    nssf_employer: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    housing_levy_employer: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    nita: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    employer_cost: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    calculation_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class PayslipLine(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "payslip_lines"
    __table_args__ = (
        UniqueConstraint("payroll_result_id", "sequence", name="uq_line_result_sequence"),
        CheckConstraint(
            "line_type IN ('earning', 'deduction', 'employer_contribution', 'statutory')",
            name="line_type_valid",
        ),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        Index("ix_payslip_lines_result", "payroll_result_id"),
    )

    payroll_result_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("payroll_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    line_type: Mapped[str] = mapped_column(String(30), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(150), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    taxable: Mapped[bool] = mapped_column(nullable=False, default=True)
