"""Expense categories and employee reimbursement claims."""

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


class ExpenseCategory(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    """A company expense category mapped to a ledger expense account."""

    __tablename__ = "expense_categories"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_expense_category_company_code"),
        UniqueConstraint("id", "company_id", name="uq_expense_category_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_expense_category_company_org",
        ),
        ForeignKeyConstraint(
            ["expense_account_id", "company_id"],
            ["accounts.id", "accounts.company_id"],
            ondelete="RESTRICT",
            name="fk_expense_category_account_company",
        ),
        CheckConstraint("status IN ('active', 'archived')", name="status_valid"),
        Index("ix_expense_categories_company_status", "company_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    expense_account_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class ExpenseClaim(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    """An employee expense reimbursement claim and its approval lifecycle."""

    __tablename__ = "expense_claims"
    __table_args__ = (
        UniqueConstraint("company_id", "claim_number", name="uq_expense_claim_company_number"),
        UniqueConstraint("id", "company_id", name="uq_expense_claim_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_expense_claim_company_org",
        ),
        ForeignKeyConstraint(
            ["employee_id", "company_id"],
            ["employees.id", "employees.company_id"],
            ondelete="RESTRICT",
            name="fk_expense_claim_employee_company",
        ),
        ForeignKeyConstraint(
            ["category_id", "company_id"],
            ["expense_categories.id", "expense_categories.company_id"],
            ondelete="RESTRICT",
            name="fk_expense_claim_category_company",
        ),
        CheckConstraint(
            "status IN ('draft', 'submitted', 'approved', 'rejected', 'reimbursed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "receipt_date IS NULL OR receipt_date >= expense_date",
            name="receipt_date_valid",
        ),
        Index("ix_expense_claims_company_status", "company_id", "status"),
        Index("ix_expense_claims_employee_status", "employee_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    claim_number: Mapped[str] = mapped_column(String(40), nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    receipt_number: Mapped[str | None] = mapped_column(String(100))
    receipt_date: Mapped[date | None] = mapped_column(Date)
    receipt_merchant: Mapped[str | None] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    requested_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(1000))
    paid_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    paid_on: Mapped[date | None] = mapped_column(Date)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_reference: Mapped[str | None] = mapped_column(String(100))
    journal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("journals.id", ondelete="RESTRICT")
    )
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
