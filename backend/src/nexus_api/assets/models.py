"""Fixed asset persistence: categories, assets, depreciation and disposals."""

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


class AssetCategory(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    """A company category mapping an asset to four ledger accounts."""

    __tablename__ = "asset_categories"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_asset_category_company_code"),
        UniqueConstraint("id", "company_id", name="uq_asset_category_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_asset_category_company_org",
        ),
        Index("ix_asset_categories_company_active", "company_id", "is_active"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    cost_account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    accumulated_depreciation_account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    depreciation_expense_account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    gain_loss_account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class FixedAsset(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    """A fixed asset whose lifecycle and accounting are owned by the API."""

    __tablename__ = "fixed_assets"
    __table_args__ = (
        UniqueConstraint("company_id", "asset_number", name="uq_fixed_asset_company_number"),
        UniqueConstraint("id", "company_id", name="uq_fixed_asset_id_company"),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_fixed_asset_company_org",
        ),
        ForeignKeyConstraint(
            ["category_id", "company_id"],
            ["asset_categories.id", "asset_categories.company_id"],
            ondelete="RESTRICT",
            name="fk_fixed_asset_category_company",
        ),
        CheckConstraint(
            "status IN ('draft', 'submitted', 'active', 'disposal_pending', "
            "'disposed', 'rejected', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("cost > 0", name="cost_positive"),
        CheckConstraint(
            "residual_value >= 0 AND residual_value <= cost", name="residual_value_valid"
        ),
        CheckConstraint("useful_life_months > 0", name="useful_life_positive"),
        CheckConstraint("in_service_date >= acquisition_date", name="service_after_acquisition"),
        CheckConstraint(
            "decided_by IS NULL OR decided_by <> requested_by",
            name="decision_separation_of_duties",
        ),
        Index("ix_fixed_assets_company_status", "company_id", "status"),
        Index("ix_fixed_assets_company_category", "company_id", "category_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    asset_number: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    serial_reference: Mapped[str | None] = mapped_column(String(120))
    acquisition_date: Mapped[date] = mapped_column(Date, nullable=False)
    in_service_date: Mapped[date] = mapped_column(Date, nullable=False)
    cost: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    residual_value: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    useful_life_months: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    cost_account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    accumulated_depreciation_account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    depreciation_expense_account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    gain_loss_account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(1000))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capitalization_journal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("journals.id", ondelete="RESTRICT")
    )
    disposal_journal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("journals.id", ondelete="RESTRICT")
    )


class AssetDepreciation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One posted depreciation period for an asset."""

    __tablename__ = "asset_depreciations"
    __table_args__ = (
        UniqueConstraint("asset_id", "period_start", name="uq_asset_depreciation_asset_period"),
        ForeignKeyConstraint(
            ["asset_id", "company_id"],
            ["fixed_assets.id", "fixed_assets.company_id"],
            ondelete="CASCADE",
            name="fk_asset_depreciation_asset_company",
        ),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_asset_depreciation_company_org",
        ),
        CheckConstraint("period_end > period_start", name="period_range_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_asset_depreciations_company_asset", "company_id", "asset_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    journal_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("journals.id", ondelete="RESTRICT"), nullable=False
    )
    posted_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100))


class AssetDisposal(Base, UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    """A disposal request and its approval decision for one asset."""

    __tablename__ = "asset_disposals"
    __table_args__ = (
        UniqueConstraint("asset_id", name="uq_asset_disposal_asset"),
        ForeignKeyConstraint(
            ["asset_id", "company_id"],
            ["fixed_assets.id", "fixed_assets.company_id"],
            ondelete="CASCADE",
            name="fk_asset_disposal_asset_company",
        ),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_asset_disposal_company_org",
        ),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status_valid"),
        CheckConstraint("proceeds_amount >= 0", name="proceeds_non_negative"),
        CheckConstraint(
            "decided_by IS NULL OR decided_by <> requested_by",
            name="decision_separation_of_duties",
        ),
        Index("ix_asset_disposals_company_status", "company_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    disposal_date: Mapped[date] = mapped_column(Date, nullable=False)
    proceeds_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    proceeds_account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    requested_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(1000))
    journal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("journals.id", ondelete="RESTRICT")
    )
