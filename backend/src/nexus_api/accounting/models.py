"""Double-entry accounting persistence: accounts, periods, journals and lines."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from nexus_api.database.base import (
    Base,
    OptimisticLockMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Account(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_account_company_code"),
        UniqueConstraint("id", "company_id", name="uq_account_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_account_company_org",
        ),
        CheckConstraint(
            "account_type IN ('asset', 'liability', 'equity', 'revenue', 'expense')",
            name="type_valid",
        ),
        CheckConstraint("normal_balance IN ('debit', 'credit')", name="balance_valid"),
        Index("ix_accounts_company_type", "company_id", "account_type"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)
    normal_balance: Mapped[str] = mapped_column(String(10), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class FiscalPeriod(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fiscal_periods"
    __table_args__ = (
        UniqueConstraint("company_id", "start_date", name="uq_fiscal_period_company_start"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_fiscal_period_company_org",
        ),
        CheckConstraint("end_date >= start_date", name="range_valid"),
        CheckConstraint("status IN ('open', 'locked', 'closed')", name="status_valid"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")


class Journal(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    __tablename__ = "journals"
    __table_args__ = (
        UniqueConstraint("company_id", "journal_number", name="uq_journal_company_number"),
        UniqueConstraint("id", "company_id", name="uq_journal_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_journal_company_org",
        ),
        CheckConstraint(
            "status IN ('draft', 'posted', 'reversed')",
            name="status_valid",
        ),
        CheckConstraint("total_debit >= 0 AND total_credit >= 0", name="totals_non_negative"),
        CheckConstraint(
            "status <> 'posted' OR total_debit = total_credit",
            name="posted_must_balance",
        ),
        Index("ix_journals_company_date", "company_id", "journal_date"),
        Index(
            "uq_journals_source",
            "company_id",
            "source_type",
            "source_id",
            unique=True,
            postgresql_where=text("source_id IS NOT NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    journal_number: Mapped[str] = mapped_column(String(40), nullable=False)
    journal_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_debit: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00")
    )
    total_credit: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00")
    )
    source_type: Mapped[str | None] = mapped_column(String(50))
    source_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("journals.id", ondelete="RESTRICT")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    posted_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JournalLine(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("debit >= 0 AND credit >= 0", name="amounts_non_negative"),
        CheckConstraint("(debit = 0) <> (credit = 0)", name="exactly_one_side"),
        Index("ix_journal_lines_journal", "journal_id"),
        Index("ix_journal_lines_account", "account_id"),
    )

    journal_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("journals.id", ondelete="CASCADE"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0.00"))
    credit: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0.00"))
    cost_centre_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cost_centres.id", ondelete="RESTRICT")
    )
