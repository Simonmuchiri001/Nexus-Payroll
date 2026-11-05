"""Employee loan agreements and their payroll deduction schedules."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

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
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from nexus_api.database.base import (
    Base,
    OptimisticLockMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class EmployeeLoan(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    """A loan or salary advance requested for one employee."""

    __tablename__ = "employee_loans"
    __table_args__ = (
        UniqueConstraint("company_id", "loan_number", name="uq_employee_loan_company_number"),
        UniqueConstraint("id", "company_id", name="uq_employee_loan_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_employee_loan_company_org",
        ),
        ForeignKeyConstraint(
            ["employee_id", "company_id"],
            ["employees.id", "employees.company_id"],
            ondelete="RESTRICT",
            name="fk_employee_loan_employee_company",
        ),
        CheckConstraint("kind IN ('loan', 'advance')", name="kind_valid"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'disbursed', 'closed')",
            name="status_valid",
        ),
        CheckConstraint("principal_amount > 0", name="principal_positive"),
        CheckConstraint(
            "interest_rate_annual >= 0 AND interest_rate_annual <= 100",
            name="interest_rate_valid",
        ),
        CheckConstraint("term_months BETWEEN 1 AND 120", name="term_valid"),
        CheckConstraint(
            "decided_by IS NULL OR decided_by <> requested_by",
            name="decision_separation_of_duties",
        ),
        CheckConstraint(
            "disbursed_on IS NULL OR first_due_date IS NULL OR first_due_date >= disbursed_on",
            name="first_due_after_disbursement",
        ),
        Index("ix_employee_loans_company_status", "company_id", "status"),
        Index("ix_employee_loans_employee_status", "employee_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    loan_number: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    interest_rate_annual: Mapped[Decimal] = mapped_column(
        Numeric(7, 4), nullable=False, default=Decimal("0.0000"), server_default="0"
    )
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(1000))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disbursed_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    disbursed_on: Mapped[date | None] = mapped_column(Date)
    disbursed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disbursement_reference: Mapped[str | None] = mapped_column(String(100))
    first_due_date: Mapped[date | None] = mapped_column(Date)
    journal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("journals.id", ondelete="RESTRICT")
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoanInstallment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One contractual repayment instalment, paid through a payroll run."""

    __tablename__ = "loan_installments"
    __table_args__ = (
        UniqueConstraint("loan_id", "sequence", name="uq_loan_installment_sequence"),
        ForeignKeyConstraint(
            ["loan_id", "company_id"],
            ["employee_loans.id", "employee_loans.company_id"],
            ondelete="CASCADE",
            name="fk_loan_installment_loan_company",
        ),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_loan_installment_company_org",
        ),
        ForeignKeyConstraint(
            ["payroll_run_id", "company_id"],
            ["payroll_runs.id", "payroll_runs.company_id"],
            ondelete="RESTRICT",
            name="fk_loan_installment_run_company",
        ),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint("principal_due >= 0 AND interest_due >= 0", name="amounts_non_negative"),
        CheckConstraint("amount_due = principal_due + interest_due", name="amount_reconciles"),
        CheckConstraint("status IN ('scheduled', 'reserved', 'deducted')", name="status_valid"),
        CheckConstraint(
            "status = 'scheduled' OR payroll_run_id IS NOT NULL",
            name="assignment_requires_run",
        ),
        Index("ix_loan_installments_loan_due", "loan_id", "due_date"),
        Index("ix_loan_installments_run", "payroll_run_id"),
        Index(
            "ix_loan_installments_company_status_due",
            "company_id",
            "status",
            "due_date",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    loan_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    principal_due: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    interest_due: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    amount_due: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    payroll_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    payroll_result_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("payroll_results.id", ondelete="SET NULL")
    )
    deducted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
