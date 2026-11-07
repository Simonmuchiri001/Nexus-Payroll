"""Leave configuration, balances, requests and attendance records."""

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


class LeaveType(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    __tablename__ = "leave_types"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_leave_type_company_code"),
        UniqueConstraint("id", "company_id", name="uq_leave_type_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_leave_type_company_org",
        ),
        CheckConstraint("annual_entitlement_days >= 0", name="entitlement_non_negative"),
        CheckConstraint("max_carryover_days >= 0", name="carryover_non_negative"),
        CheckConstraint("status IN ('active', 'archived')", name="status_valid"),
        Index("ix_leave_types_company_status", "company_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    is_paid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    requires_balance: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    annual_entitlement_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    max_carryover_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class CompanyHoliday(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "company_holidays"
    __table_args__ = (
        UniqueConstraint("company_id", "holiday_date", name="uq_company_holiday_date"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="CASCADE",
            name="fk_holiday_company_org",
        ),
        CheckConstraint("origin IN ('company', 'national')", name="origin_valid"),
        Index("ix_company_holidays_company_date", "company_id", "holiday_date"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default="company", server_default="company"
    )
    calendar_version: Mapped[str | None] = mapped_column(String(30))


class LeaveBalance(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    __tablename__ = "leave_balances"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "leave_type_id", "leave_year", name="uq_leave_balance_employee_type_year"
        ),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_leave_balance_company_org",
        ),
        ForeignKeyConstraint(
            ["employee_id", "company_id"],
            ["employees.id", "employees.company_id"],
            ondelete="RESTRICT",
            name="fk_leave_balance_employee_company",
        ),
        ForeignKeyConstraint(
            ["leave_type_id", "company_id"],
            ["leave_types.id", "leave_types.company_id"],
            ondelete="RESTRICT",
            name="fk_leave_balance_type_company",
        ),
        CheckConstraint("leave_year BETWEEN 2000 AND 2100", name="year_valid"),
        CheckConstraint("entitled_days >= 0", name="entitled_non_negative"),
        CheckConstraint("carried_over_days >= 0", name="carried_non_negative"),
        CheckConstraint("used_days >= 0", name="used_non_negative"),
        CheckConstraint("pending_days >= 0", name="pending_non_negative"),
        CheckConstraint(
            "used_days + pending_days <= entitled_days + carried_over_days",
            name="consumption_within_entitlement",
        ),
        Index("ix_leave_balances_company_year", "company_id", "leave_year"),
        Index("ix_leave_balances_employee_year", "employee_id", "leave_year"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    leave_type_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    leave_year: Mapped[int] = mapped_column(Integer, nullable=False)
    entitled_days: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    carried_over_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    used_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    pending_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )


class LeaveRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    __tablename__ = "leave_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_leave_request_company_org",
        ),
        ForeignKeyConstraint(
            ["employee_id", "company_id"],
            ["employees.id", "employees.company_id"],
            ondelete="RESTRICT",
            name="fk_leave_request_employee_company",
        ),
        ForeignKeyConstraint(
            ["leave_type_id", "company_id"],
            ["leave_types.id", "leave_types.company_id"],
            ondelete="RESTRICT",
            name="fk_leave_request_type_company",
        ),
        CheckConstraint("end_date >= start_date", name="date_range_valid"),
        CheckConstraint("requested_days > 0", name="days_positive"),
        CheckConstraint("leave_year BETWEEN 2000 AND 2100", name="year_valid"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')", name="status_valid"
        ),
        Index("ix_leave_requests_company_status", "company_id", "status"),
        Index("ix_leave_requests_employee_dates", "employee_id", "start_date", "end_date"),
        Index("ix_leave_requests_company_dates", "company_id", "start_date"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    leave_type_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    leave_year: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_days: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    balance_tracked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
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


class AttendanceRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", name="uq_attendance_employee_date"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_attendance_company_org",
        ),
        ForeignKeyConstraint(
            ["employee_id", "company_id"],
            ["employees.id", "employees.company_id"],
            ondelete="RESTRICT",
            name="fk_attendance_employee_company",
        ),
        CheckConstraint(
            "status IN ('present', 'absent', 'leave', 'holiday', 'remote')",
            name="status_valid",
        ),
        CheckConstraint("source IN ('manual', 'leave')", name="source_valid"),
        CheckConstraint(
            "source = 'manual' OR leave_request_id IS NOT NULL",
            name="leave_source_requires_request",
        ),
        Index("ix_attendance_company_date", "company_id", "work_date"),
        Index("ix_attendance_employee_date", "employee_id", "work_date"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    leave_request_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leave_requests.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
