"""Organisation, company, branch and cost-centre persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from nexus_api.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Tenant boundary. Every tenant-owned row carries this identity."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'closed')", name="status_valid"),
    )

    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(250))
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="KE")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Company(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Legal entity within a tenant. Financial records are company-scoped."""

    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_company_org_code"),
        UniqueConstraint("id", "organization_id", name="uq_company_id_org"),
        CheckConstraint("status IN ('active', 'suspended', 'closed')", name="status_valid"),
        CheckConstraint(
            "fiscal_year_start_month BETWEEN 1 AND 12",
            name="fiscal_month_valid",
        ),
        Index("ix_companies_org_status", "organization_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(250))
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="KE")
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KES")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Africa/Nairobi")
    fiscal_year_start_month: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class Branch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_branch_company_code"),
        UniqueConstraint("id", "company_id", name="uq_branch_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_branch_company_org",
        ),
        CheckConstraint("status IN ('active', 'closed')", name="status_valid"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class Department(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_department_company_code"),
        UniqueConstraint("id", "company_id", name="uq_department_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_department_company_org",
        ),
        CheckConstraint("status IN ('active', 'archived')", name="status_valid"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class CostCentre(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cost_centres"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_cost_centre_company_code"),
        UniqueConstraint("id", "company_id", name="uq_cost_centre_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_cost_centre_company_org",
        ),
        CheckConstraint("status IN ('active', 'archived')", name="status_valid"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class FiscalCalendarMarker(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Tenant-visible creation marker used to reject uninitialised deployments."""

    __tablename__ = "organization_setup_markers"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_setup_marker_org"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
