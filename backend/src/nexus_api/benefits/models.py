"""Benefit plans and employee enrolments."""

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


class BenefitPlan(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    """A company benefit product with monthly employee and employer contributions."""

    __tablename__ = "benefit_plans"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_benefit_plan_company_code"),
        UniqueConstraint("id", "company_id", name="uq_benefit_plan_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_benefit_plan_company_org",
        ),
        CheckConstraint(
            "category IN ('medical', 'pension', 'insurance', 'allowance', 'other')",
            name="category_valid",
        ),
        CheckConstraint("status IN ('active', 'archived')", name="status_valid"),
        CheckConstraint(
            "employee_contribution >= 0 AND employer_contribution >= 0",
            name="contributions_non_negative",
        ),
        CheckConstraint(
            "employee_contribution + employer_contribution > 0",
            name="contributions_positive",
        ),
        Index("ix_benefit_plans_company_status", "company_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    employee_contribution: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    employer_contribution: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class BenefitEnrollment(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    """An employee's effective-dated enrolment in a benefit plan.

    Contribution amounts are snapshotted at enrolment so later plan changes do
    not silently rewrite an employee's agreed contribution.
    """

    __tablename__ = "benefit_enrollments"
    __table_args__ = (
        UniqueConstraint("id", "company_id", name="uq_benefit_enrollment_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_benefit_enrollment_company_org",
        ),
        ForeignKeyConstraint(
            ["employee_id", "company_id"],
            ["employees.id", "employees.company_id"],
            ondelete="RESTRICT",
            name="fk_benefit_enrollment_employee_company",
        ),
        ForeignKeyConstraint(
            ["plan_id", "company_id"],
            ["benefit_plans.id", "benefit_plans.company_id"],
            ondelete="RESTRICT",
            name="fk_benefit_enrollment_plan_company",
        ),
        CheckConstraint("status IN ('active', 'ended')", name="status_valid"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_range_valid",
        ),
        CheckConstraint(
            "employee_contribution >= 0 AND employer_contribution >= 0",
            name="contributions_non_negative",
        ),
        CheckConstraint(
            "employee_contribution + employer_contribution > 0",
            name="contributions_positive",
        ),
        Index("ix_benefit_enrollments_company_status", "company_id", "status"),
        Index("ix_benefit_enrollments_employee_status", "employee_id", "status"),
        Index(
            "uq_benefit_enrollments_active_employee_plan",
            "employee_id",
            "plan_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    employee_contribution: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    employer_contribution: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    notes: Mapped[str | None] = mapped_column(String(500))
    ended_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_note: Mapped[str | None] = mapped_column(String(500))
