"""Employee lifecycle and effective-dated compensation services."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.api.deps import PaginationParams
from nexus_api.audit.service import record_audit
from nexus_api.core.errors import ConflictError, NotFoundError, ValidationFailureError
from nexus_api.employees.models import Employee, EmployeeCompensation
from nexus_api.employees.schemas import (
    CompensationCreate,
    EmployeeCreate,
    EmployeeUpdate,
    TerminationRequest,
)
from nexus_api.identity.permissions import PermissionCode
from nexus_api.identity.service import AuthContext, validate_company_access
from nexus_api.organizations.models import Branch, Company, CostCentre, Department


async def _validate_org_unit(
    session: AsyncSession,
    model: type[Branch] | type[Department] | type[CostCentre],
    unit_id: uuid.UUID | None,
    company_id: uuid.UUID,
) -> None:
    if unit_id is None:
        return
    exists = await session.scalar(
        select(model.id).where(model.id == unit_id, model.company_id == company_id)
    )
    if exists is None:
        label = model.__tablename__.replace("_", " ")
        raise ValidationFailureError(f"The selected {label} does not belong to the company.")


async def _validate_manager(
    session: AsyncSession,
    manager_id: uuid.UUID | None,
    company_id: uuid.UUID,
    *,
    employee_id: uuid.UUID | None = None,
) -> None:
    if manager_id is None:
        return
    if employee_id is not None and manager_id == employee_id:
        raise ValidationFailureError("An employee cannot be their own manager.")
    exists = await session.scalar(
        select(Employee.id).where(
            Employee.id == manager_id,
            Employee.company_id == company_id,
            Employee.status != "terminated",
        )
    )
    if exists is None:
        raise ValidationFailureError("The selected manager is not an active employee.")


async def _next_employee_number(session: AsyncSession, company_id: uuid.UUID) -> str:
    await session.execute(select(Company.id).where(Company.id == company_id).with_for_update())
    count = await session.scalar(
        select(func.count(Employee.id)).where(Employee.company_id == company_id)
    )
    candidate = (count or 0) + 1
    while True:
        number = f"EMP-{candidate:05d}"
        exists = await session.scalar(
            select(Employee.id).where(
                Employee.company_id == company_id,
                Employee.employee_number == number,
            )
        )
        if exists is None:
            return number
        candidate += 1


def _validate_dates(hire_date: date, date_of_birth: date | None) -> None:
    if date_of_birth is None:
        return
    if date_of_birth >= hire_date:
        raise ValidationFailureError("Date of birth must precede the hire date.")
    sixteenth_birthday = date_of_birth.replace(year=date_of_birth.year + 16)
    if hire_date < sixteenth_birthday:
        raise ValidationFailureError("The employee must be at least 16 years old at hire.")


async def list_employees(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    pagination: PaginationParams,
    search: str | None,
    status: str | None,
) -> tuple[list[Employee], int]:
    await validate_company_access(session, context, company_id, PermissionCode.EMPLOYEE_VIEW)
    filters = [Employee.company_id == company_id]
    if status:
        filters.append(Employee.status == status)
    if search:
        term = f"%{search.strip().lower()}%"
        filters.append(
            or_(
                func.lower(Employee.first_name).like(term),
                func.lower(Employee.last_name).like(term),
                func.lower(Employee.employee_number).like(term),
                func.lower(func.coalesce(Employee.work_email, "")).like(term),
            )
        )
    total = await session.scalar(select(func.count(Employee.id)).where(*filters))
    employees = (
        await session.scalars(
            select(Employee)
            .where(*filters)
            .order_by(Employee.last_name, Employee.first_name)
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()
    return list(employees), total or 0


async def get_employee(
    session: AsyncSession, context: AuthContext, employee_id: uuid.UUID
) -> Employee:
    employee = await session.get(Employee, employee_id)
    if employee is None or employee.organization_id != context.organization.id:
        raise NotFoundError("Employee not found.")
    await validate_company_access(
        session, context, employee.company_id, PermissionCode.EMPLOYEE_VIEW
    )
    return employee


async def create_employee(
    session: AsyncSession, context: AuthContext, payload: EmployeeCreate
) -> Employee:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.EMPLOYEE_CREATE
    )
    _validate_dates(payload.hire_date, payload.date_of_birth)
    await _validate_org_unit(session, Branch, payload.branch_id, company.id)
    await _validate_org_unit(session, Department, payload.department_id, company.id)
    await _validate_org_unit(session, CostCentre, payload.cost_centre_id, company.id)
    await _validate_manager(session, payload.manager_id, company.id)

    employee_number = (
        payload.employee_number.strip().upper()
        if payload.employee_number
        else await _next_employee_number(session, company.id)
    )
    duplicate = await session.scalar(
        select(Employee.id).where(
            Employee.company_id == company.id,
            Employee.employee_number == employee_number,
        )
    )
    if duplicate is not None:
        raise ConflictError("An employee with that employee number already exists.")

    if payload.initial_compensation is not None:
        context.require(PermissionCode.COMPENSATION_MANAGE, company_id=company.id)

    employee = Employee(
        organization_id=context.organization.id,
        company_id=company.id,
        branch_id=payload.branch_id,
        department_id=payload.department_id,
        cost_centre_id=payload.cost_centre_id,
        manager_id=payload.manager_id,
        employee_number=employee_number,
        first_name=payload.first_name.strip(),
        middle_name=payload.middle_name.strip() if payload.middle_name else None,
        last_name=payload.last_name.strip(),
        work_email=str(payload.work_email).lower() if payload.work_email else None,
        personal_email=str(payload.personal_email).lower() if payload.personal_email else None,
        phone=payload.phone,
        national_id=payload.national_id,
        kra_pin=payload.kra_pin.upper() if payload.kra_pin else None,
        nssf_number=payload.nssf_number,
        shif_number=payload.shif_number,
        date_of_birth=payload.date_of_birth,
        gender=payload.gender,
        job_title=payload.job_title.strip(),
        hire_date=payload.hire_date,
        status=payload.status,
        is_payroll_eligible=payload.is_payroll_eligible,
    )
    session.add(employee)
    await session.flush()

    if payload.initial_compensation is not None:
        await _create_compensation(
            session, context, employee, company, payload.initial_compensation
        )

    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="employee.created",
        entity_type="employee",
        entity_id=employee.id,
        metadata={"company_id": str(company.id), "employee_number": employee.employee_number},
    )
    return employee


async def update_employee(
    session: AsyncSession,
    context: AuthContext,
    employee: Employee,
    payload: EmployeeUpdate,
) -> Employee:
    context.require(PermissionCode.EMPLOYEE_UPDATE, company_id=employee.company_id)
    if payload.version is not None and payload.version != employee.version:
        raise ConflictError(
            "The employee record changed since it was loaded. Reload and try again."
        )
    if employee.status == "terminated":
        raise ConflictError("A terminated employee cannot be updated.")
    if payload.status == "active" and employee.status == "suspended":
        raise ValidationFailureError("Reactivation requires an HR approval workflow.")

    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    if "branch_id" in changes:
        await _validate_org_unit(session, Branch, payload.branch_id, employee.company_id)
    if "department_id" in changes:
        await _validate_org_unit(session, Department, payload.department_id, employee.company_id)
    if "cost_centre_id" in changes:
        await _validate_org_unit(session, CostCentre, payload.cost_centre_id, employee.company_id)
    if "manager_id" in changes:
        await _validate_manager(
            session,
            payload.manager_id,
            employee.company_id,
            employee_id=employee.id,
        )
    if "date_of_birth" in changes and payload.date_of_birth is not None:
        _validate_dates(employee.hire_date, payload.date_of_birth)

    for field, value in changes.items():
        setattr(employee, field, value)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="employee.updated",
        entity_type="employee",
        entity_id=employee.id,
        metadata={"fields": sorted(changes)},
    )
    return employee


async def terminate_employee(
    session: AsyncSession,
    context: AuthContext,
    employee: Employee,
    payload: TerminationRequest,
) -> Employee:
    context.require(PermissionCode.EMPLOYEE_TERMINATE, company_id=employee.company_id)
    if employee.status == "terminated":
        raise ConflictError("The employee is already terminated.")
    if payload.termination_date < employee.hire_date:
        raise ValidationFailureError("Termination date cannot precede the hire date.")

    employee.termination_date = payload.termination_date
    employee.termination_reason = payload.reason.strip()
    employee.status = "terminated"

    compensations = (
        await session.scalars(
            select(EmployeeCompensation).where(
                EmployeeCompensation.employee_id == employee.id,
                EmployeeCompensation.status == "active",
                or_(
                    EmployeeCompensation.effective_to.is_(None),
                    EmployeeCompensation.effective_to > payload.termination_date,
                ),
            )
        )
    ).all()
    for compensation in compensations:
        compensation.effective_to = payload.termination_date

    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="employee.terminated",
        entity_type="employee",
        entity_id=employee.id,
        metadata={
            "termination_date": payload.termination_date.isoformat(),
            "reason": employee.termination_reason,
        },
    )
    return employee


async def list_compensations(
    session: AsyncSession, context: AuthContext, employee: Employee
) -> list[EmployeeCompensation]:
    context.require(PermissionCode.COMPENSATION_VIEW, company_id=employee.company_id)
    return list(
        (
            await session.scalars(
                select(EmployeeCompensation)
                .where(EmployeeCompensation.employee_id == employee.id)
                .order_by(EmployeeCompensation.effective_from.desc())
            )
        ).all()
    )


async def _create_compensation(
    session: AsyncSession,
    context: AuthContext,
    employee: Employee,
    company: Company,
    payload: CompensationCreate,
) -> EmployeeCompensation:
    if employee.status == "terminated":
        raise ConflictError("Compensation cannot be added to a terminated employee.")
    if payload.effective_from < employee.hire_date:
        raise ValidationFailureError("Compensation cannot start before the hire date.")
    if employee.termination_date and payload.effective_from > employee.termination_date:
        raise ValidationFailureError("Compensation cannot start after termination.")
    if payload.currency.upper() != company.base_currency:
        raise ValidationFailureError(
            f"Compensation must use the company base currency ({company.base_currency})."
        )

    await session.execute(select(Employee.id).where(Employee.id == employee.id).with_for_update())
    new_start = payload.effective_from
    overlapping = (
        await session.scalars(
            select(EmployeeCompensation).where(
                EmployeeCompensation.employee_id == employee.id,
                EmployeeCompensation.status == "active",
                EmployeeCompensation.effective_from < new_start,
                or_(
                    EmployeeCompensation.effective_to.is_(None),
                    EmployeeCompensation.effective_to >= new_start,
                ),
            )
        )
    ).all()
    future = await session.scalar(
        select(EmployeeCompensation.id).where(
            EmployeeCompensation.employee_id == employee.id,
            EmployeeCompensation.status == "active",
            EmployeeCompensation.effective_from >= new_start,
        )
    )
    if future is not None:
        raise ConflictError(
            "The effective date conflicts with an existing or future compensation record. "
            "Backdated corrections require an authorised adjustment workflow."
        )
    for previous in overlapping:
        previous.effective_to = new_start - timedelta(days=1)
        previous.status = "superseded"

    compensation = EmployeeCompensation(
        organization_id=context.organization.id,
        company_id=company.id,
        employee_id=employee.id,
        base_salary=payload.base_salary.quantize(Decimal("0.01")),
        housing_allowance=payload.housing_allowance.quantize(Decimal("0.01")),
        transport_allowance=payload.transport_allowance.quantize(Decimal("0.01")),
        other_allowance=payload.other_allowance.quantize(Decimal("0.01")),
        currency=payload.currency.upper(),
        effective_from=payload.effective_from,
        status="active",
        created_by=context.user.id,
    )
    session.add(compensation)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="compensation.created",
        entity_type="employee_compensation",
        entity_id=compensation.id,
        metadata={
            "employee_id": str(employee.id),
            "effective_from": payload.effective_from.isoformat(),
            "base_salary": str(compensation.base_salary),
            "currency": compensation.currency,
        },
    )
    return compensation


async def create_compensation(
    session: AsyncSession,
    context: AuthContext,
    employee: Employee,
    payload: CompensationCreate,
) -> EmployeeCompensation:
    context.require(PermissionCode.COMPENSATION_MANAGE, company_id=employee.company_id)
    company = await session.get(Company, employee.company_id)
    if company is None:
        raise NotFoundError("Company not found.")
    return await _create_compensation(session, context, employee, company, payload)


async def count_employees_due_for_payroll(session: AsyncSession, company_id: uuid.UUID) -> int:
    """Small read model used by the dashboard; payroll uses its own selection."""

    value = await session.scalar(
        select(func.count(Employee.id)).where(
            Employee.company_id == company_id,
            Employee.status.in_(["active", "probation", "suspended"]),
            Employee.is_payroll_eligible.is_(True),
        )
    )
    return value or 0
