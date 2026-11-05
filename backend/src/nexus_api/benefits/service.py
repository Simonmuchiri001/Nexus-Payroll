"""Benefit plan administration and employee enrolment services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.api.deps import PaginationParams
from nexus_api.audit.service import record_audit
from nexus_api.benefits.models import BenefitEnrollment, BenefitPlan
from nexus_api.benefits.schemas import (
    BenefitCategory,
    BenefitEnrollmentCreate,
    BenefitEnrollmentEnd,
    BenefitEnrollmentPage,
    BenefitEnrollmentResponse,
    BenefitPlanCreate,
    BenefitPlanResponse,
    BenefitPlanStatus,
    BenefitPlanUpdate,
    EnrollmentStatus,
)
from nexus_api.core.errors import ConflictError, NotFoundError, ValidationFailureError
from nexus_api.employees.models import Employee
from nexus_api.identity.permissions import PermissionCode
from nexus_api.identity.service import AuthContext, validate_company_access


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _plan_response(plan: BenefitPlan) -> BenefitPlanResponse:
    return BenefitPlanResponse(
        id=plan.id,
        organization_id=plan.organization_id,
        company_id=plan.company_id,
        code=plan.code,
        name=plan.name,
        category=cast(BenefitCategory, plan.category),
        employee_contribution=plan.employee_contribution,
        employer_contribution=plan.employer_contribution,
        currency=plan.currency,
        description=plan.description,
        status=cast(BenefitPlanStatus, plan.status),
        created_at=plan.created_at,
        version=plan.version,
    )


def _employee_name(employee: Employee) -> str:
    return " ".join(
        part for part in (employee.first_name, employee.middle_name, employee.last_name) if part
    )


def _enrollment_response(
    enrollment: BenefitEnrollment,
    employee: Employee,
    plan: BenefitPlan,
) -> BenefitEnrollmentResponse:
    return BenefitEnrollmentResponse(
        id=enrollment.id,
        organization_id=enrollment.organization_id,
        company_id=enrollment.company_id,
        employee_id=enrollment.employee_id,
        employee_number=employee.employee_number,
        employee_name=_employee_name(employee),
        plan_id=enrollment.plan_id,
        plan_code=plan.code,
        plan_name=plan.name,
        plan_category=cast(BenefitCategory, plan.category),
        effective_from=enrollment.effective_from,
        effective_to=enrollment.effective_to,
        employee_contribution=enrollment.employee_contribution,
        employer_contribution=enrollment.employer_contribution,
        currency=enrollment.currency,
        status=cast(EnrollmentStatus, enrollment.status),
        notes=enrollment.notes,
        ended_by=enrollment.ended_by,
        ended_at=enrollment.ended_at,
        end_note=enrollment.end_note,
        created_at=enrollment.created_at,
        version=enrollment.version,
    )


async def _locked_plan(
    session: AsyncSession, context: AuthContext, plan_id: uuid.UUID
) -> BenefitPlan:
    plan = await session.scalar(
        select(BenefitPlan)
        .where(
            BenefitPlan.id == plan_id,
            BenefitPlan.organization_id == context.organization.id,
        )
        .with_for_update()
    )
    if plan is None:
        raise NotFoundError("Benefit plan not found.")
    await validate_company_access(session, context, plan.company_id, PermissionCode.BENEFIT_MANAGE)
    return plan


def _check_version(record: BenefitPlan | BenefitEnrollment, version: int | None) -> None:
    if version is not None and version != record.version:
        raise ConflictError(
            "This benefit record was changed by another user. Reload it and try again."
        )


async def list_plans(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    status: str | None = None,
) -> list[BenefitPlanResponse]:
    await validate_company_access(session, context, company_id, PermissionCode.BENEFIT_VIEW)
    filters = [BenefitPlan.company_id == company_id]
    if status:
        filters.append(BenefitPlan.status == status)
    plans = (
        await session.scalars(
            select(BenefitPlan).where(*filters).order_by(BenefitPlan.name, BenefitPlan.code)
        )
    ).all()
    return [_plan_response(plan) for plan in plans]


async def create_plan(
    session: AsyncSession, context: AuthContext, payload: BenefitPlanCreate
) -> BenefitPlanResponse:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.BENEFIT_MANAGE
    )
    existing = await session.scalar(
        select(BenefitPlan.id).where(
            BenefitPlan.company_id == company.id,
            BenefitPlan.code == payload.code.upper(),
        )
    )
    if existing is not None:
        raise ConflictError("A benefit plan with that code already exists.")
    plan = BenefitPlan(
        organization_id=context.organization.id,
        company_id=company.id,
        code=payload.code.upper(),
        name=payload.name.strip(),
        category=payload.category,
        employee_contribution=_money(payload.employee_contribution),
        employer_contribution=_money(payload.employer_contribution),
        currency=company.base_currency,
        description=payload.description.strip() if payload.description else None,
        status="active",
    )
    session.add(plan)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="benefit_plan.created",
        entity_type="benefit_plan",
        entity_id=plan.id,
        metadata={
            "company_id": str(company.id),
            "code": plan.code,
            "employee_contribution": str(plan.employee_contribution),
            "employer_contribution": str(plan.employer_contribution),
        },
    )
    return _plan_response(plan)


async def update_plan(
    session: AsyncSession,
    context: AuthContext,
    plan_id: uuid.UUID,
    payload: BenefitPlanUpdate,
) -> BenefitPlanResponse:
    plan = await _locked_plan(session, context, plan_id)
    _check_version(plan, payload.version)

    employee_contribution = (
        payload.employee_contribution
        if payload.employee_contribution is not None
        else plan.employee_contribution
    )
    employer_contribution = (
        payload.employer_contribution
        if payload.employer_contribution is not None
        else plan.employer_contribution
    )
    if employee_contribution + employer_contribution <= 0:
        raise ValidationFailureError("At least one contribution must be greater than zero.")

    if payload.name is not None:
        plan.name = payload.name.strip()
    if payload.category is not None:
        plan.category = payload.category
    if payload.employee_contribution is not None:
        plan.employee_contribution = _money(payload.employee_contribution)
    if payload.employer_contribution is not None:
        plan.employer_contribution = _money(payload.employer_contribution)
    if "description" in payload.model_fields_set:
        plan.description = payload.description.strip() if payload.description else None
    if payload.status is not None:
        if payload.status == "archived":
            active_count = await session.scalar(
                select(func.count(BenefitEnrollment.id)).where(
                    BenefitEnrollment.plan_id == plan.id,
                    BenefitEnrollment.status == "active",
                )
            )
            if active_count:
                raise ConflictError("End all active enrolments before archiving this benefit plan.")
        plan.status = payload.status

    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="benefit_plan.updated",
        entity_type="benefit_plan",
        entity_id=plan.id,
        metadata={
            "code": plan.code,
            "status": plan.status,
            "employee_contribution": str(plan.employee_contribution),
            "employer_contribution": str(plan.employer_contribution),
        },
    )
    return _plan_response(plan)


async def _locked_enrollment(
    session: AsyncSession, context: AuthContext, enrollment_id: uuid.UUID
) -> BenefitEnrollment:
    enrollment = await session.scalar(
        select(BenefitEnrollment)
        .where(
            BenefitEnrollment.id == enrollment_id,
            BenefitEnrollment.organization_id == context.organization.id,
        )
        .with_for_update()
    )
    if enrollment is None:
        raise NotFoundError("Benefit enrolment not found.")
    await validate_company_access(
        session, context, enrollment.company_id, PermissionCode.BENEFIT_MANAGE
    )
    return enrollment


async def create_enrollment(
    session: AsyncSession, context: AuthContext, payload: BenefitEnrollmentCreate
) -> BenefitEnrollmentResponse:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.BENEFIT_MANAGE
    )
    plan = await session.scalar(
        select(BenefitPlan)
        .where(
            BenefitPlan.id == payload.plan_id,
            BenefitPlan.company_id == company.id,
            BenefitPlan.organization_id == context.organization.id,
        )
        .with_for_update()
    )
    if plan is None:
        raise NotFoundError("Benefit plan not found for this company.")
    if plan.status != "active":
        raise ConflictError("Employees can only be enrolled in an active benefit plan.")

    employee = await session.scalar(
        select(Employee).where(
            Employee.id == payload.employee_id,
            Employee.company_id == company.id,
            Employee.organization_id == context.organization.id,
        )
    )
    if employee is None:
        raise NotFoundError("Employee not found for this company.")
    if employee.status not in ("active", "probation"):
        raise ConflictError("Benefits can only be enrolled for active employees.")
    if not employee.is_payroll_eligible:
        raise ConflictError("Benefits can only be enrolled for payroll-eligible employees.")
    if payload.effective_from < employee.hire_date:
        raise ValidationFailureError("Benefits cannot start before the employee's hire date.")
    if employee.termination_date is not None and payload.effective_from > employee.termination_date:
        raise ValidationFailureError("Benefits cannot start after the employee's termination date.")

    employee_contribution = _money(
        payload.employee_contribution
        if payload.employee_contribution is not None
        else plan.employee_contribution
    )
    employer_contribution = _money(
        payload.employer_contribution
        if payload.employer_contribution is not None
        else plan.employer_contribution
    )
    if employee_contribution + employer_contribution <= 0:
        raise ValidationFailureError("At least one contribution must be greater than zero.")

    existing = await session.scalar(
        select(BenefitEnrollment.id).where(
            BenefitEnrollment.employee_id == employee.id,
            BenefitEnrollment.plan_id == plan.id,
            BenefitEnrollment.status == "active",
        )
    )
    if existing is not None:
        raise ConflictError("This employee already has an active enrolment in the plan.")

    enrollment = BenefitEnrollment(
        organization_id=context.organization.id,
        company_id=company.id,
        employee_id=employee.id,
        plan_id=plan.id,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        employee_contribution=employee_contribution,
        employer_contribution=employer_contribution,
        currency=plan.currency,
        status="active",
        notes=payload.notes.strip() if payload.notes else None,
    )
    session.add(enrollment)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="benefit_enrollment.created",
        entity_type="benefit_enrollment",
        entity_id=enrollment.id,
        metadata={
            "employee_id": str(employee.id),
            "plan_id": str(plan.id),
            "effective_from": enrollment.effective_from.isoformat(),
            "employee_contribution": str(enrollment.employee_contribution),
            "employer_contribution": str(enrollment.employer_contribution),
        },
    )
    return _enrollment_response(enrollment, employee, plan)


async def list_enrollments(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    pagination: PaginationParams,
    employee_id: uuid.UUID | None = None,
    plan_id: uuid.UUID | None = None,
    status: str | None = None,
) -> BenefitEnrollmentPage:
    await validate_company_access(session, context, company_id, PermissionCode.BENEFIT_VIEW)
    filters = [BenefitEnrollment.company_id == company_id]
    if employee_id is not None:
        filters.append(BenefitEnrollment.employee_id == employee_id)
    if plan_id is not None:
        filters.append(BenefitEnrollment.plan_id == plan_id)
    if status:
        filters.append(BenefitEnrollment.status == status)

    total = await session.scalar(select(func.count(BenefitEnrollment.id)).where(*filters))
    rows = (
        await session.execute(
            select(BenefitEnrollment, Employee, BenefitPlan)
            .join(Employee, Employee.id == BenefitEnrollment.employee_id)
            .join(BenefitPlan, BenefitPlan.id == BenefitEnrollment.plan_id)
            .where(*filters)
            .order_by(BenefitEnrollment.created_at.desc(), Employee.employee_number)
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()
    items = [
        _enrollment_response(enrollment, employee, plan) for enrollment, employee, plan in rows
    ]
    return BenefitEnrollmentPage(
        items=items,
        total=total or 0,
        page=pagination.page,
        page_size=pagination.page_size,
    )


async def end_enrollment(
    session: AsyncSession,
    context: AuthContext,
    enrollment_id: uuid.UUID,
    payload: BenefitEnrollmentEnd,
) -> BenefitEnrollmentResponse:
    enrollment = await _locked_enrollment(session, context, enrollment_id)
    _check_version(enrollment, payload.version)
    if enrollment.status != "active":
        raise ConflictError("Only an active benefit enrolment can be ended.")
    if payload.effective_to < enrollment.effective_from:
        raise ValidationFailureError("The end date must not precede the start date.")

    enrollment.status = "ended"
    enrollment.effective_to = payload.effective_to
    enrollment.ended_by = context.user.id
    enrollment.ended_at = datetime.now(UTC)
    enrollment.end_note = payload.note.strip() if payload.note else None

    employee = await session.get(Employee, enrollment.employee_id)
    plan = await session.get(BenefitPlan, enrollment.plan_id)
    if employee is None or plan is None:
        raise NotFoundError("The employee or benefit plan linked to this enrolment is missing.")
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="benefit_enrollment.ended",
        entity_type="benefit_enrollment",
        entity_id=enrollment.id,
        metadata={
            "employee_id": str(enrollment.employee_id),
            "plan_id": str(enrollment.plan_id),
            "effective_to": payload.effective_to.isoformat(),
        },
    )
    return _enrollment_response(enrollment, employee, plan)
