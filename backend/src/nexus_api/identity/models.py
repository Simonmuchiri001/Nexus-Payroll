"""Users, tenant membership, RBAC and session persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from nexus_api.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_active", "is_active"),)

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OrganizationMembership(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
        UniqueConstraint("id", "organization_id", name="uq_membership_id_org"),
        ForeignKeyConstraint(
            ["default_company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="RESTRICT",
            name="fk_membership_default_company_org",
        ),
        CheckConstraint("status IN ('active', 'suspended')", name="status_valid"),
        Index("ix_memberships_user", "user_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    default_company_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class Permission(Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(80), primary_key=True)
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("code", name="uq_role_code"),)

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_code: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("permissions.code", ondelete="CASCADE"),
        primary_key=True,
    )


class RoleAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Role granted to a membership, optionally narrowed to one company."""

    __tablename__ = "role_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["membership_id", "organization_id"],
            ["organization_memberships.id", "organization_memberships.organization_id"],
            ondelete="CASCADE",
            name="fk_assignment_membership_org",
        ),
        ForeignKeyConstraint(
            ["company_id", "organization_id"],
            ["companies.id", "companies.organization_id"],
            ondelete="CASCADE",
            name="fk_assignment_company_org",
        ),
        UniqueConstraint(
            "membership_id",
            "role_id",
            "company_id",
            "branch_id",
            name="uq_assignment_scope",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "company_id IS NOT NULL OR branch_id IS NULL",
            name="branch_requires_company",
        ),
        Index("ix_assignments_membership", "membership_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class RefreshToken(Base):
    """Opaque rotating refresh token; only its SHA-256 digest is stored."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_token_hash"),
        Index("ix_refresh_tokens_user", "user_id"),
    )

    jti: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_jti: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("refresh_tokens.jti", ondelete="SET NULL")
    )
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(String(64))


class LoginEvent(Base):
    """Successful and failed authentication attempts for security review."""

    __tablename__ = "login_events"
    __table_args__ = (Index("ix_login_events_email_created", "email", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(100))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
