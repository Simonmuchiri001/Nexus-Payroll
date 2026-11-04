"""Employee master data and effective-dated compensation."""

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
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from nexus_api.database.base import (
    Base,
    OptimisticLockMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Employee(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("company_id", "employee_number", name="uq_employee_company_number"),
        UniqueConstraint("id", "company_id", name="uq_employee_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_employee_company_org",
        ),
        ForeignKeyConstraint(
            ["branch_id", "company_id"],
            ["branches.id", "branches.company_id"],
            ondelete="RESTRICT",
            name="fk_employee_branch_company",
        ),
        ForeignKeyConstraint(
            ["department_id", "company_id"],
            ["departments.id", "departments.company_id"],
            ondelete="RESTRICT",
            name="fk_employee_department_company",
        ),
        ForeignKeyConstraint(
            ["cost_centre_id", "company_id"],
            ["cost_centres.id", "cost_centres.company_id"],
            ondelete="RESTRICT",
            name="fk_employee_cost_centre_company",
        ),
        CheckConstraint(
            "status IN ('active', 'probation', 'suspended', 'terminated')",
            name="status_valid",
        ),
        CheckConstraint(
            "termination_date IS NULL OR termination_date >= hire_date",
            name="termination_after_hire",
        ),
        CheckConstraint(
            "gender IS NULL OR gender IN ('female', 'male', 'other', 'undisclosed')",
            name="gender_valid",
        ),
        Index("ix_employees_company_status", "company_id", "status"),
        Index("ix_employees_company_name", "company_id", "last_name", "first_name"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    department_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    cost_centre_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL")
    )

    employee_number: Mapped[str] = mapped_column(String(32), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    work_email: Mapped[str | None] = mapped_column(String(320))
    personal_email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(40))
    national_id: Mapped[str | None] = mapped_column(String(40))
    kra_pin: Mapped[str | None] = mapped_column(String(20))
    nssf_number: Mapped[str | None] = mapped_column(String(30))
    shif_number: Mapped[str | None] = mapped_column(String(30))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(20))
    job_title: Mapped[str] = mapped_column(String(150), nullable=False)
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    confirmation_date: Mapped[date | None] = mapped_column(Date)
    termination_date: Mapped[date | None] = mapped_column(Date)
    termination_reason: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    is_payroll_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class EmployeeBankAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "employee_bank_accounts"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "bank_name", "account_fingerprint", name="uq_employee_bank_account"
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_range_valid",
        ),
        Index("ix_bank_accounts_employee", "employee_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    bank_name: Mapped[str] = mapped_column(String(150), nullable=False)
    bank_branch: Mapped[str | None] = mapped_column(String(150))
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    encrypted_account_number: Mapped[str] = mapped_column(String(1000), nullable=False)
    account_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KES")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class EmployeeCompensation(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    __tablename__ = "employee_compensations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_compensation_company_org",
        ),
        CheckConstraint("base_salary >= 0", name="base_salary_non_negative"),
        CheckConstraint("housing_allowance >= 0", name="housing_non_negative"),
        CheckConstraint("transport_allowance >= 0", name="transport_non_negative"),
        CheckConstraint("other_allowance >= 0", name="other_non_negative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_range_valid",
        ),
        CheckConstraint("status IN ('active', 'superseded')", name="status_valid"),
        Index(
            "ix_compensation_employee_effective",
            "employee_id",
            "effective_from",
            "effective_to",
        ),
        Index("ix_compensation_company_status", "company_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    base_salary: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    housing_allowance: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    transport_allowance: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    other_allowance: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KES")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
