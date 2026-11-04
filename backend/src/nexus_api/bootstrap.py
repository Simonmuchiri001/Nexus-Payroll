"""First-run tenant bootstrap.

This is deliberately a command-line operation: an unauthenticated API endpoint
must never be able to create a platform administrator or a new tenant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.accounting.service import ensure_default_chart, ensure_fiscal_year
from nexus_api.audit.service import record_audit
from nexus_api.core.errors import ConflictError, ValidationFailureError
from nexus_api.identity.models import (
    OrganizationMembership,
    Role,
    RoleAssignment,
    User,
)
from nexus_api.identity.security import hash_password, validate_password_strength
from nexus_api.identity.service import seed_identity
from nexus_api.organizations.models import Company, Organization
from nexus_api.payroll.models import PayGroup, PayrollPeriod
from nexus_api.payroll.service import ensure_kenya_rule_set
from nexus_api.payroll.statutory import load_kenya_rule_seed


@dataclass(frozen=True, slots=True)
class BootstrapRequest:
    organization_slug: str
    organization_name: str
    company_code: str
    company_name: str
    admin_email: str
    admin_password: str
    admin_full_name: str
    country_code: str = "KE"
    base_currency: str = "KES"


async def bootstrap_tenant(session: AsyncSession, request: BootstrapRequest) -> Organization:
    validate_password_strength(request.admin_password)
    slug = request.organization_slug.lower().strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,78}[a-z0-9]", slug):
        raise ValidationFailureError("Organisation slug must be a URL-safe identifier.")
    existing_org = await session.scalar(select(Organization.id).where(Organization.slug == slug))
    if existing_org is not None:
        raise ConflictError("That organisation slug already exists.")

    await seed_identity(session)
    await ensure_kenya_rule_set(session)
    seed = load_kenya_rule_seed()

    email = request.admin_email.lower().strip()
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            password_hash=hash_password(request.admin_password),
            full_name=request.admin_full_name.strip(),
            is_platform_admin=False,
            is_active=True,
        )
        session.add(user)
        await session.flush()

    organization = Organization(
        slug=slug,
        name=request.organization_name.strip(),
        legal_name=request.organization_name.strip(),
        country_code=request.country_code.upper(),
        status="active",
    )
    session.add(organization)
    await session.flush()

    company = Company(
        organization_id=organization.id,
        code=request.company_code.upper(),
        name=request.company_name.strip(),
        legal_name=request.company_name.strip(),
        country_code=request.country_code.upper(),
        base_currency=request.base_currency.upper(),
        timezone="Africa/Nairobi" if request.country_code.upper() == "KE" else "UTC",
        fiscal_year_start_month=1,
        status="active",
    )
    session.add(company)
    await session.flush()
    await ensure_default_chart(session, company)
    await ensure_fiscal_year(session, company, date.today().year)

    membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=user.id,
        default_company_id=company.id,
        status="active",
    )
    session.add(membership)
    await session.flush()

    admin_role = await session.scalar(select(Role).where(Role.code == "org_admin"))
    if admin_role is None:
        raise ValidationFailureError("The org_admin role was not installed.")
    session.add(
        RoleAssignment(
            organization_id=organization.id,
            membership_id=membership.id,
            role_id=admin_role.id,
            company_id=None,
            branch_id=None,
            assigned_by=user.id,
        )
    )

    pay_group = PayGroup(
        organization_id=organization.id,
        company_id=company.id,
        code="MONTHLY",
        name="Monthly Payroll",
        frequency="monthly",
        currency=company.base_currency,
        status="active",
    )
    session.add(pay_group)
    await session.flush()
    today = date.today()
    period_start = today.replace(day=1)
    next_month = period_start.replace(
        year=period_start.year + (1 if period_start.month == 12 else 0),
        month=1 if period_start.month == 12 else period_start.month + 1,
    )
    period_end = next_month - date.resolution
    payment_date = next_month.replace(day=9)
    session.add(
        PayrollPeriod(
            pay_group_id=pay_group.id,
            period_start=period_start,
            period_end=period_end,
            payment_date=payment_date,
            status="open",
        )
    )

    await record_audit(
        session,
        organization_id=organization.id,
        actor_user_id=user.id,
        action="organization.bootstrapped",
        entity_type="organization",
        entity_id=organization.id,
        metadata={
            "company_id": str(company.id),
            "company_code": company.code,
            "rule_set_version": seed.version,
        },
    )
    await session.flush()
    return organization
