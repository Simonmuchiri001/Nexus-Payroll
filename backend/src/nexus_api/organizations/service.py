"""Organisation queries and company setup."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.audit.service import record_audit
from nexus_api.core.errors import ConflictError, NotFoundError, ValidationFailureError
from nexus_api.identity.permissions import PermissionCode
from nexus_api.identity.service import AuthContext, validate_company_access
from nexus_api.organizations.models import Branch, Company, Department, Organization
from nexus_api.organizations.schemas import (
    BranchCreate,
    CompanyCreate,
    DepartmentCreate,
)


async def get_current_organization(session: AsyncSession, context: AuthContext) -> Organization:
    organization = await session.get(Organization, context.organization.id)
    if organization is None:
        raise NotFoundError("Organisation not found.")
    return organization


async def list_companies(session: AsyncSession, context: AuthContext) -> list[Company]:
    context.require(PermissionCode.COMPANY_VIEW)
    return list(
        (
            await session.scalars(
                select(Company)
                .where(
                    Company.organization_id == context.organization.id,
                    Company.id.in_(context.accessible_companies),
                )
                .order_by(Company.name)
            )
        ).all()
    )


async def create_company(
    session: AsyncSession, context: AuthContext, payload: CompanyCreate
) -> Company:
    context.require(PermissionCode.COMPANY_MANAGE, organization_wide=True)
    code = payload.code.upper()
    existing = await session.scalar(
        select(Company.id).where(
            Company.organization_id == context.organization.id,
            Company.code == code,
        )
    )
    if existing is not None:
        raise ConflictError("A company with that code already exists.")
    country = payload.country_code.upper()
    currency = payload.base_currency.upper()
    if country == "KE" and currency != "KES":
        raise ValidationFailureError("Kenyan companies must use KES as their base currency.")
    company = Company(
        organization_id=context.organization.id,
        code=code,
        name=payload.name.strip(),
        legal_name=payload.legal_name.strip() if payload.legal_name else None,
        country_code=country,
        base_currency=currency,
        timezone=payload.timezone,
        fiscal_year_start_month=payload.fiscal_year_start_month,
        status="active",
    )
    session.add(company)
    await session.flush()
    from nexus_api.accounting.service import (
        ensure_default_chart,
        ensure_fiscal_year,
        fiscal_year_start_for,
    )

    await ensure_default_chart(session, company)
    fiscal_start = fiscal_year_start_for(company, date.today())
    await ensure_fiscal_year(session, company, fiscal_start.year)
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="company.created",
        entity_type="company",
        entity_id=company.id,
        metadata={"code": company.code, "name": company.name},
    )
    return company


async def get_company(
    session: AsyncSession, context: AuthContext, company_id: uuid.UUID
) -> Company:
    return await validate_company_access(session, context, company_id, PermissionCode.COMPANY_VIEW)


async def list_branches(
    session: AsyncSession, context: AuthContext, company_id: uuid.UUID
) -> list[Branch]:
    await validate_company_access(session, context, company_id, PermissionCode.COMPANY_VIEW)
    return list(
        (
            await session.scalars(
                select(Branch).where(Branch.company_id == company_id).order_by(Branch.name)
            )
        ).all()
    )


async def create_branch(
    session: AsyncSession,
    context: AuthContext,
    company_id: uuid.UUID,
    payload: BranchCreate,
) -> Branch:
    await validate_company_access(session, context, company_id, PermissionCode.COMPANY_MANAGE)
    code = payload.code.upper()
    existing = await session.scalar(
        select(Branch.id).where(Branch.company_id == company_id, Branch.code == code)
    )
    if existing is not None:
        raise ConflictError("A branch with that code already exists in this company.")
    branch = Branch(
        organization_id=context.organization.id,
        company_id=company_id,
        code=code,
        name=payload.name.strip(),
        address=payload.address.strip() if payload.address else None,
        status="active",
    )
    session.add(branch)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="branch.created",
        entity_type="branch",
        entity_id=branch.id,
        metadata={"company_id": str(company_id), "code": code},
    )
    return branch


async def list_departments(
    session: AsyncSession, context: AuthContext, company_id: uuid.UUID
) -> list[Department]:
    await validate_company_access(session, context, company_id, PermissionCode.COMPANY_VIEW)
    return list(
        (
            await session.scalars(
                select(Department)
                .where(Department.company_id == company_id)
                .order_by(Department.name)
            )
        ).all()
    )


async def create_department(
    session: AsyncSession,
    context: AuthContext,
    company_id: uuid.UUID,
    payload: DepartmentCreate,
) -> Department:
    await validate_company_access(session, context, company_id, PermissionCode.COMPANY_MANAGE)
    code = payload.code.upper()
    existing = await session.scalar(
        select(Department.id).where(Department.company_id == company_id, Department.code == code)
    )
    if existing is not None:
        raise ConflictError("A department with that code already exists in this company.")
    department = Department(
        organization_id=context.organization.id,
        company_id=company_id,
        code=code,
        name=payload.name.strip(),
        status="active",
    )
    session.add(department)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="department.created",
        entity_type="department",
        entity_id=department.id,
        metadata={"company_id": str(company_id), "code": code},
    )
    return department
