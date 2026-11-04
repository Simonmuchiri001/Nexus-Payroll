"""Leave entitlements, request workflow, holidays and attendance services."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from nexus_api.api.deps import PaginationParams
from nexus_api.audit.service import record_audit
from nexus_api.core.errors import ConflictError, NotFoundError, ValidationFailureError
from nexus_api.employees.models import Employee
from nexus_api.identity.models import User
from nexus_api.identity.permissions import PermissionCode
from nexus_api.identity.service import AuthContext, validate_company_access
from nexus_api.leave.models import (
    AttendanceRecord,
    CompanyHoliday,
    LeaveBalance,
    LeaveRequest,
    LeaveType,
)
from nexus_api.leave.schemas import (
    AttendanceCreate,
    AttendancePage,
    AttendanceResponse,
    AttendanceUpdate,
    HolidayCreate,
    HolidayResponse,
    LeaveBalanceAdjust,
    LeaveBalanceInitialize,
    LeaveBalanceResponse,
    LeaveDecision,
    LeaveRequestCreate,
    LeaveRequestPage,
    LeaveRequestResponse,
    LeaveTypeCreate,
    LeaveTypeResponse,
    LeaveTypeUpdate,
)

ZERO = Decimal("0.00")


def _available(balance: LeaveBalance) -> Decimal:
    return (
        balance.entitled_days + balance.carried_over_days - balance.used_days - balance.pending_days
    )


def _balance_response(balance: LeaveBalance, leave_type: LeaveType) -> LeaveBalanceResponse:
    return LeaveBalanceResponse(
        id=balance.id,
        company_id=balance.company_id,
        employee_id=balance.employee_id,
        leave_type_id=leave_type.id,
        leave_type_code=leave_type.code,
        leave_type_name=leave_type.name,
        leave_year=balance.leave_year,
        entitled_days=balance.entitled_days,
        carried_over_days=balance.carried_over_days,
        used_days=balance.used_days,
        pending_days=balance.pending_days,
        available_days=_available(balance),
        version=balance.version,
    )


def _employee_name(employee: Employee) -> str:
    return " ".join(
        part for part in (employee.first_name, employee.middle_name, employee.last_name) if part
    )


async def _employee_for_update(
    session: AsyncSession,
    context: AuthContext,
    employee_id: uuid.UUID,
    permission: PermissionCode,
    *,
    for_update: bool = False,
    require_active: bool = False,
) -> Employee:
    statement = select(Employee).where(
        Employee.id == employee_id,
        Employee.organization_id == context.organization.id,
    )
    if for_update:
        statement = statement.with_for_update()
    employee = await session.scalar(statement)
    if employee is None:
        raise NotFoundError("Employee not found.")
    await validate_company_access(session, context, employee.company_id, permission)
    if require_active and employee.status == "terminated":
        raise ConflictError("Leave cannot be recorded for a terminated employee.")
    return employee


async def get_leave_type(
    session: AsyncSession, context: AuthContext, leave_type_id: uuid.UUID
) -> LeaveType:
    leave_type = await session.scalar(
        select(LeaveType).where(
            LeaveType.id == leave_type_id,
            LeaveType.organization_id == context.organization.id,
        )
    )
    if leave_type is None:
        raise NotFoundError("Leave type not found.")
    await validate_company_access(
        session, context, leave_type.company_id, PermissionCode.LEAVE_VIEW
    )
    return leave_type


async def _holiday_set(
    session: AsyncSession, company_id: uuid.UUID, start: date, end: date
) -> set[date]:
    return set(
        (
            await session.scalars(
                select(CompanyHoliday.holiday_date).where(
                    CompanyHoliday.company_id == company_id,
                    CompanyHoliday.holiday_date >= start,
                    CompanyHoliday.holiday_date <= end,
                )
            )
        ).all()
    )


def _working_dates(start: date, end: date, holidays: set[date]) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in holidays:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def working_days(start: date, end: date, holidays: set[date]) -> Decimal:
    return Decimal(len(_working_dates(start, end, holidays))).quantize(Decimal("0.01"))


async def list_leave_types(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    status: str | None = None,
) -> list[LeaveTypeResponse]:
    await validate_company_access(session, context, company_id, PermissionCode.LEAVE_VIEW)
    filters = [LeaveType.company_id == company_id]
    if status:
        filters.append(LeaveType.status == status)
    types = (
        await session.scalars(select(LeaveType).where(*filters).order_by(LeaveType.name))
    ).all()
    return [LeaveTypeResponse.model_validate(leave_type) for leave_type in types]


async def create_leave_type(
    session: AsyncSession, context: AuthContext, payload: LeaveTypeCreate
) -> LeaveTypeResponse:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.LEAVE_MANAGE
    )
    code = payload.code.strip().upper()
    existing = await session.scalar(
        select(LeaveType.id).where(LeaveType.company_id == company.id, LeaveType.code == code)
    )
    if existing is not None:
        raise ConflictError("A leave type with that code already exists.")
    if payload.max_carryover_days > payload.annual_entitlement_days:
        raise ValidationFailureError("Maximum carryover cannot exceed the annual entitlement.")
    leave_type = LeaveType(
        organization_id=context.organization.id,
        company_id=company.id,
        code=code,
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        is_paid=payload.is_paid,
        requires_balance=payload.requires_balance,
        annual_entitlement_days=payload.annual_entitlement_days,
        max_carryover_days=payload.max_carryover_days,
        status="active",
    )
    session.add(leave_type)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="leave_type.created",
        entity_type="leave_type",
        entity_id=leave_type.id,
        metadata={"company_id": str(company.id), "code": code},
    )
    return LeaveTypeResponse.model_validate(leave_type)


async def update_leave_type(
    session: AsyncSession,
    context: AuthContext,
    leave_type: LeaveType,
    payload: LeaveTypeUpdate,
) -> LeaveTypeResponse:
    await validate_company_access(
        session, context, leave_type.company_id, PermissionCode.LEAVE_MANAGE
    )
    if payload.version is not None and payload.version != leave_type.version:
        raise ConflictError("This leave type was changed by another user. Reload it and try again.")
    fields = payload.model_fields_set - {"version"}
    if not fields:
        raise ValidationFailureError("No leave type changes were supplied.")
    if payload.status == "archived" and leave_type.status != "archived":
        pending = await session.scalar(
            select(func.count(LeaveRequest.id)).where(
                LeaveRequest.leave_type_id == leave_type.id,
                LeaveRequest.status == "pending",
            )
        )
        if (pending or 0) > 0:
            raise ConflictError("Resolve pending leave requests before archiving this type.")
    if payload.name is not None:
        leave_type.name = payload.name.strip()
    if payload.description is not None:
        leave_type.description = payload.description.strip() or None
    if payload.is_paid is not None:
        leave_type.is_paid = payload.is_paid
    if payload.requires_balance is not None:
        leave_type.requires_balance = payload.requires_balance
    if payload.annual_entitlement_days is not None:
        leave_type.annual_entitlement_days = payload.annual_entitlement_days
    if payload.max_carryover_days is not None:
        leave_type.max_carryover_days = payload.max_carryover_days
    if payload.status is not None:
        leave_type.status = payload.status
    if leave_type.max_carryover_days > leave_type.annual_entitlement_days:
        raise ValidationFailureError("Maximum carryover cannot exceed the annual entitlement.")
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="leave_type.updated",
        entity_type="leave_type",
        entity_id=leave_type.id,
        metadata={"company_id": str(leave_type.company_id), "fields": sorted(fields)},
    )
    return LeaveTypeResponse.model_validate(leave_type)


async def list_holidays(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    year: int | None = None,
) -> list[HolidayResponse]:
    await validate_company_access(session, context, company_id, PermissionCode.LEAVE_VIEW)
    filters = [CompanyHoliday.company_id == company_id]
    if year is not None:
        filters.append(CompanyHoliday.holiday_date >= date(year, 1, 1))
        filters.append(CompanyHoliday.holiday_date <= date(year, 12, 31))
    holidays = (
        await session.scalars(
            select(CompanyHoliday).where(*filters).order_by(CompanyHoliday.holiday_date)
        )
    ).all()
    return [HolidayResponse.model_validate(holiday) for holiday in holidays]


async def create_holiday(
    session: AsyncSession, context: AuthContext, payload: HolidayCreate
) -> HolidayResponse:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.LEAVE_MANAGE
    )
    existing = await session.scalar(
        select(CompanyHoliday.id).where(
            CompanyHoliday.company_id == company.id,
            CompanyHoliday.holiday_date == payload.holiday_date,
        )
    )
    if existing is not None:
        raise ConflictError("A company holiday already exists on that date.")
    holiday = CompanyHoliday(
        organization_id=context.organization.id,
        company_id=company.id,
        holiday_date=payload.holiday_date,
        name=payload.name.strip(),
    )
    session.add(holiday)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="holiday.created",
        entity_type="company_holiday",
        entity_id=holiday.id,
        metadata={"company_id": str(company.id), "date": payload.holiday_date.isoformat()},
    )
    return HolidayResponse.model_validate(holiday)


async def delete_holiday(
    session: AsyncSession, context: AuthContext, holiday_id: uuid.UUID
) -> None:
    holiday = await session.scalar(
        select(CompanyHoliday).where(
            CompanyHoliday.id == holiday_id,
            CompanyHoliday.organization_id == context.organization.id,
        )
    )
    if holiday is None:
        raise NotFoundError("Company holiday not found.")
    await validate_company_access(session, context, holiday.company_id, PermissionCode.LEAVE_MANAGE)
    covering_request = await session.scalar(
        select(LeaveRequest.id).where(
            LeaveRequest.company_id == holiday.company_id,
            LeaveRequest.status.in_(("pending", "approved")),
            LeaveRequest.start_date <= holiday.holiday_date,
            LeaveRequest.end_date >= holiday.holiday_date,
        )
    )
    if covering_request is not None:
        raise ConflictError(
            "This holiday falls inside pending or approved leave. Resolve that leave first."
        )
    await session.delete(holiday)
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="holiday.deleted",
        entity_type="company_holiday",
        entity_id=holiday.id,
        metadata={
            "company_id": str(holiday.company_id),
            "date": holiday.holiday_date.isoformat(),
        },
    )


async def _ensure_balance(
    session: AsyncSession,
    *,
    employee: Employee,
    leave_type: LeaveType,
    leave_year: int,
) -> LeaveBalance:
    balance = await session.scalar(
        select(LeaveBalance)
        .where(
            LeaveBalance.employee_id == employee.id,
            LeaveBalance.leave_type_id == leave_type.id,
            LeaveBalance.leave_year == leave_year,
        )
        .with_for_update()
    )
    if balance is not None:
        return balance
    carried = ZERO
    if leave_year > 2000 and leave_type.max_carryover_days > 0:
        prior = await session.scalar(
            select(LeaveBalance).where(
                LeaveBalance.employee_id == employee.id,
                LeaveBalance.leave_type_id == leave_type.id,
                LeaveBalance.leave_year == leave_year - 1,
            )
        )
        if prior is not None:
            remaining = _available(prior)
            if remaining > 0:
                carried = min(remaining, leave_type.max_carryover_days)
    balance = LeaveBalance(
        organization_id=employee.organization_id,
        company_id=employee.company_id,
        employee_id=employee.id,
        leave_type_id=leave_type.id,
        leave_year=leave_year,
        entitled_days=leave_type.annual_entitlement_days,
        carried_over_days=carried,
        used_days=ZERO,
        pending_days=ZERO,
    )
    session.add(balance)
    await session.flush()
    return balance


async def initialize_balances(
    session: AsyncSession, context: AuthContext, payload: LeaveBalanceInitialize
) -> list[LeaveBalanceResponse]:
    employee = await _employee_for_update(
        session,
        context,
        payload.employee_id,
        PermissionCode.LEAVE_MANAGE,
        for_update=True,
        require_active=True,
    )
    if employee.hire_date.year > payload.leave_year:
        raise ValidationFailureError("The leave year precedes the employee's hire date.")
    types = (
        await session.scalars(
            select(LeaveType)
            .where(
                LeaveType.company_id == employee.company_id,
                LeaveType.status == "active",
                LeaveType.requires_balance.is_(True),
            )
            .order_by(LeaveType.name)
        )
    ).all()
    balances = [
        await _ensure_balance(
            session, employee=employee, leave_type=leave_type, leave_year=payload.leave_year
        )
        for leave_type in types
    ]
    return [
        _balance_response(balance, leave_type)
        for balance, leave_type in zip(balances, types, strict=True)
    ]


async def list_balances(
    session: AsyncSession,
    context: AuthContext,
    *,
    employee_id: uuid.UUID,
    leave_year: int,
) -> list[LeaveBalanceResponse]:
    employee = await _employee_for_update(session, context, employee_id, PermissionCode.LEAVE_VIEW)
    rows = (
        await session.execute(
            select(LeaveBalance, LeaveType)
            .join(LeaveType, LeaveType.id == LeaveBalance.leave_type_id)
            .where(
                LeaveBalance.employee_id == employee.id,
                LeaveBalance.leave_year == leave_year,
            )
            .order_by(LeaveType.name)
        )
    ).all()
    return [_balance_response(balance, leave_type) for balance, leave_type in rows]


async def adjust_balance(
    session: AsyncSession,
    context: AuthContext,
    balance_id: uuid.UUID,
    payload: LeaveBalanceAdjust,
) -> LeaveBalanceResponse:
    row = (
        await session.execute(
            select(LeaveBalance, LeaveType)
            .join(LeaveType, LeaveType.id == LeaveBalance.leave_type_id)
            .where(
                LeaveBalance.id == balance_id,
                LeaveBalance.organization_id == context.organization.id,
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Leave balance not found.")
    balance, leave_type = row
    await validate_company_access(session, context, balance.company_id, PermissionCode.LEAVE_MANAGE)
    if payload.version is not None and payload.version != balance.version:
        raise ConflictError(
            "This leave balance was changed by another user. Reload it and try again."
        )
    if payload.entitled_days is not None:
        balance.entitled_days = payload.entitled_days
    if payload.carried_over_days is not None:
        balance.carried_over_days = payload.carried_over_days
    if balance.carried_over_days > leave_type.max_carryover_days:
        raise ValidationFailureError(
            "The carryover exceeds the maximum configured for this leave type."
        )
    if balance.used_days + balance.pending_days > balance.entitled_days + balance.carried_over_days:
        raise ConflictError(
            "The adjusted entitlement is below leave already used or pending approval."
        )
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="leave_balance.adjusted",
        entity_type="leave_balance",
        entity_id=balance.id,
        metadata={
            "employee_id": str(balance.employee_id),
            "leave_type_id": str(balance.leave_type_id),
            "leave_year": balance.leave_year,
            "entitled_days": str(balance.entitled_days),
            "carried_over_days": str(balance.carried_over_days),
            "reason": payload.reason,
        },
    )
    return _balance_response(balance, leave_type)


def _request_response(
    request: LeaveRequest,
    employee: Employee,
    leave_type: LeaveType,
    requester_name: str | None,
    decider_name: str | None,
) -> LeaveRequestResponse:
    return LeaveRequestResponse(
        id=request.id,
        company_id=request.company_id,
        employee_id=employee.id,
        employee_number=employee.employee_number,
        employee_name=_employee_name(employee),
        leave_type_id=leave_type.id,
        leave_type_code=leave_type.code,
        leave_type_name=leave_type.name,
        leave_year=request.leave_year,
        start_date=request.start_date,
        end_date=request.end_date,
        requested_days=request.requested_days,
        balance_tracked=request.balance_tracked,
        reason=request.reason,
        status=request.status,
        requested_by=request.requested_by,
        requested_by_name=requester_name,
        decided_by=request.decided_by,
        decided_by_name=decider_name,
        decided_at=request.decided_at,
        decision_note=request.decision_note,
        cancelled_by=request.cancelled_by,
        cancelled_at=request.cancelled_at,
        created_at=request.created_at,
        version=request.version,
    )


async def list_leave_requests(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    pagination: PaginationParams,
    employee_id: uuid.UUID | None = None,
    status: str | None = None,
    leave_year: int | None = None,
) -> LeaveRequestPage:
    await validate_company_access(session, context, company_id, PermissionCode.LEAVE_VIEW)
    filters = [LeaveRequest.company_id == company_id]
    if employee_id is not None:
        filters.append(LeaveRequest.employee_id == employee_id)
    if status:
        filters.append(LeaveRequest.status == status)
    if leave_year is not None:
        filters.append(LeaveRequest.leave_year == leave_year)

    requester = aliased(User)
    decider = aliased(User)
    base = (
        select(LeaveRequest, Employee, LeaveType, requester.full_name, decider.full_name)
        .join(Employee, Employee.id == LeaveRequest.employee_id)
        .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
        .outerjoin(requester, requester.id == LeaveRequest.requested_by)
        .outerjoin(decider, decider.id == LeaveRequest.decided_by)
        .where(*filters)
    )
    total = await session.scalar(select(func.count(LeaveRequest.id)).where(*filters))
    rows = (
        await session.execute(
            base.order_by(LeaveRequest.start_date.desc(), LeaveRequest.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()
    return LeaveRequestPage(
        items=[
            _request_response(request, employee, leave_type, requester_name, decider_name)
            for request, employee, leave_type, requester_name, decider_name in rows
        ],
        total=total or 0,
        page=pagination.page,
        page_size=pagination.page_size,
    )


async def create_leave_request(
    session: AsyncSession, context: AuthContext, payload: LeaveRequestCreate
) -> LeaveRequestResponse:
    employee = await _employee_for_update(
        session,
        context,
        payload.employee_id,
        PermissionCode.LEAVE_REQUEST,
        for_update=True,
        require_active=True,
    )
    leave_type = await get_leave_type(session, context, payload.leave_type_id)
    if leave_type.company_id != employee.company_id:
        raise ValidationFailureError("The leave type belongs to a different company.")
    if leave_type.status != "active":
        raise ConflictError("The selected leave type is archived.")

    overlap = await session.scalar(
        select(LeaveRequest.id).where(
            LeaveRequest.employee_id == employee.id,
            LeaveRequest.status.in_(("pending", "approved")),
            LeaveRequest.start_date <= payload.end_date,
            LeaveRequest.end_date >= payload.start_date,
        )
    )
    if overlap is not None:
        raise ConflictError("The employee already has pending or approved leave in that period.")

    holidays = await _holiday_set(
        session, employee.company_id, payload.start_date, payload.end_date
    )
    requested_days = working_days(payload.start_date, payload.end_date, holidays)
    if requested_days <= 0:
        raise ValidationFailureError(
            "The selected period contains no working days after weekends and company holidays."
        )

    leave_year = payload.start_date.year
    balance_tracked = leave_type.requires_balance
    balance: LeaveBalance | None = None
    if balance_tracked:
        balance = await _ensure_balance(
            session, employee=employee, leave_type=leave_type, leave_year=leave_year
        )
        if requested_days > _available(balance):
            raise ConflictError(
                f"Insufficient {leave_type.name} balance: {_available(balance)} day(s) available."
            )

    leave_request = LeaveRequest(
        organization_id=employee.organization_id,
        company_id=employee.company_id,
        employee_id=employee.id,
        leave_type_id=leave_type.id,
        leave_year=leave_year,
        start_date=payload.start_date,
        end_date=payload.end_date,
        requested_days=requested_days,
        balance_tracked=balance_tracked,
        reason=payload.reason.strip(),
        status="pending",
        requested_by=context.user.id,
    )
    session.add(leave_request)
    if balance is not None:
        balance.pending_days += requested_days
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="leave_request.created",
        entity_type="leave_request",
        entity_id=leave_request.id,
        metadata={
            "employee_id": str(employee.id),
            "leave_type_id": str(leave_type.id),
            "days": str(requested_days),
        },
    )
    return _request_response(leave_request, employee, leave_type, context.user.full_name, None)


async def _locked_request(
    session: AsyncSession, context: AuthContext, request_id: uuid.UUID
) -> tuple[LeaveRequest, Employee, LeaveType]:
    snapshot = await session.scalar(
        select(LeaveRequest).where(
            LeaveRequest.id == request_id,
            LeaveRequest.organization_id == context.organization.id,
        )
    )
    if snapshot is None:
        raise NotFoundError("Leave request not found.")
    employee = await _employee_for_update(
        session, context, snapshot.employee_id, PermissionCode.LEAVE_VIEW, for_update=True
    )
    request = await session.scalar(
        select(LeaveRequest).where(LeaveRequest.id == request_id).with_for_update()
    )
    if request is None:
        raise NotFoundError("Leave request not found.")
    leave_type = await session.scalar(
        select(LeaveType).where(LeaveType.id == request.leave_type_id)
    )
    if leave_type is None:
        raise NotFoundError("Leave type not found.")
    await validate_company_access(session, context, request.company_id, PermissionCode.LEAVE_VIEW)
    return request, employee, leave_type


def _check_decision_version(request: LeaveRequest, payload: LeaveDecision) -> None:
    if payload.version is not None and payload.version != request.version:
        raise ConflictError(
            "This leave request was changed by another user. Reload it and try again."
        )


def _require_separate_approver(request: LeaveRequest, context: AuthContext) -> None:
    if request.requested_by == context.user.id:
        raise ConflictError("The user who recorded a leave request cannot decide it.")


async def _balance_for_request(
    session: AsyncSession, request: LeaveRequest, employee: Employee, leave_type: LeaveType
) -> LeaveBalance:
    balance = await session.scalar(
        select(LeaveBalance)
        .where(
            LeaveBalance.employee_id == employee.id,
            LeaveBalance.leave_type_id == leave_type.id,
            LeaveBalance.leave_year == request.leave_year,
        )
        .with_for_update()
    )
    if balance is None:
        raise ConflictError("The leave balance for this request no longer exists.")
    return balance


async def approve_leave_request(
    session: AsyncSession,
    context: AuthContext,
    request_id: uuid.UUID,
    payload: LeaveDecision,
) -> LeaveRequestResponse:
    request, employee, leave_type = await _locked_request(session, context, request_id)
    context.require(PermissionCode.LEAVE_APPROVE, company_id=request.company_id)
    _check_decision_version(request, payload)
    _require_separate_approver(request, context)
    if request.status != "pending":
        raise ConflictError("Only a pending leave request can be approved.")

    holidays = await _holiday_set(session, request.company_id, request.start_date, request.end_date)
    dates = _working_dates(request.start_date, request.end_date, holidays)
    if Decimal(len(dates)).quantize(Decimal("0.01")) != request.requested_days:
        raise ConflictError(
            "The working-day count changed because of a company holiday. "
            "Cancel and re-record this request."
        )
    existing = await session.scalar(
        select(AttendanceRecord.work_date).where(
            AttendanceRecord.employee_id == employee.id,
            AttendanceRecord.work_date.in_(dates),
        )
    )
    if existing is not None:
        raise ConflictError(
            f"Attendance already exists on {existing.isoformat()}; resolve it before approval."
        )
    if request.balance_tracked:
        balance = await _balance_for_request(session, request, employee, leave_type)
        if balance.pending_days < request.requested_days:
            raise ConflictError("The pending leave balance is inconsistent with this request.")

        balance.pending_days -= request.requested_days
        balance.used_days += request.requested_days
    request.status = "approved"
    request.decided_by = context.user.id
    request.decided_at = datetime.now(UTC)
    request.decision_note = payload.note.strip() if payload.note else None
    for work_date in dates:
        session.add(
            AttendanceRecord(
                organization_id=request.organization_id,
                company_id=request.company_id,
                employee_id=employee.id,
                work_date=work_date,
                status="leave",
                source="leave",
                leave_request_id=request.id,
                note=leave_type.name,
                created_by=context.user.id,
                updated_by=context.user.id,
            )
        )
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="leave_request.approved",
        entity_type="leave_request",
        entity_id=request.id,
        metadata={"employee_id": str(employee.id), "days": str(request.requested_days)},
    )
    return _request_response(
        request,
        employee,
        leave_type,
        await _user_name(session, request.requested_by),
        context.user.full_name,
    )


async def reject_leave_request(
    session: AsyncSession,
    context: AuthContext,
    request_id: uuid.UUID,
    payload: LeaveDecision,
) -> LeaveRequestResponse:
    request, employee, leave_type = await _locked_request(session, context, request_id)
    context.require(PermissionCode.LEAVE_APPROVE, company_id=request.company_id)
    _check_decision_version(request, payload)
    _require_separate_approver(request, context)
    if request.status != "pending":
        raise ConflictError("Only a pending leave request can be rejected.")
    if request.balance_tracked:
        balance = await _balance_for_request(session, request, employee, leave_type)
        if balance.pending_days < request.requested_days:
            raise ConflictError("The pending leave balance is inconsistent with this request.")
        balance.pending_days -= request.requested_days
    request.status = "rejected"
    request.decided_by = context.user.id
    request.decided_at = datetime.now(UTC)
    request.decision_note = payload.note.strip() if payload.note else None
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="leave_request.rejected",
        entity_type="leave_request",
        entity_id=request.id,
        metadata={"employee_id": str(employee.id)},
    )
    return _request_response(
        request,
        employee,
        leave_type,
        await _user_name(session, request.requested_by),
        context.user.full_name,
    )


async def cancel_leave_request(
    session: AsyncSession,
    context: AuthContext,
    request_id: uuid.UUID,
    payload: LeaveDecision,
) -> LeaveRequestResponse:
    request, employee, leave_type = await _locked_request(session, context, request_id)
    _check_decision_version(request, payload)
    is_manager = context.has_permission(PermissionCode.LEAVE_MANAGE, company_id=request.company_id)
    is_requester = request.requested_by == context.user.id
    if not is_manager and not is_requester:
        raise ConflictError("Only the requester or a leave administrator can cancel this request.")
    if request.status == "pending":
        if request.balance_tracked:
            balance = await _balance_for_request(session, request, employee, leave_type)
            if balance.pending_days < request.requested_days:
                raise ConflictError("The pending leave balance is inconsistent with this request.")
            balance.pending_days -= request.requested_days
    elif request.status == "approved":
        if request.start_date <= date.today():
            raise ConflictError("Approved leave that has started cannot be cancelled.")
        if request.balance_tracked:
            balance = await _balance_for_request(session, request, employee, leave_type)
            if balance.used_days < request.requested_days:
                raise ConflictError("The used leave balance is inconsistent with this request.")
            balance.used_days -= request.requested_days
        await session.execute(
            delete(AttendanceRecord).where(AttendanceRecord.leave_request_id == request.id)
        )
    else:
        raise ConflictError("Only pending or future approved leave can be cancelled.")

    request.status = "cancelled"
    request.cancelled_by = context.user.id
    request.cancelled_at = datetime.now(UTC)
    request.decision_note = payload.note.strip() if payload.note else request.decision_note
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="leave_request.cancelled",
        entity_type="leave_request",
        entity_id=request.id,
        metadata={"employee_id": str(employee.id)},
    )
    return _request_response(
        request,
        employee,
        leave_type,
        await _user_name(session, request.requested_by),
        await _user_name(session, request.decided_by),
    )


async def _user_name(session: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return await session.scalar(select(User.full_name).where(User.id == user_id))


def _attendance_response(record: AttendanceRecord, employee: Employee) -> AttendanceResponse:
    return AttendanceResponse(
        id=record.id,
        company_id=record.company_id,
        employee_id=employee.id,
        employee_number=employee.employee_number,
        employee_name=_employee_name(employee),
        work_date=record.work_date,
        status=record.status,
        source=record.source,
        leave_request_id=record.leave_request_id,
        note=record.note,
        created_at=record.created_at,
        version=record.version,
    )


async def list_attendance(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    pagination: PaginationParams,
    start_date: date,
    end_date: date,
    employee_id: uuid.UUID | None = None,
) -> AttendancePage:
    await validate_company_access(session, context, company_id, PermissionCode.ATTENDANCE_VIEW)
    if end_date < start_date:
        raise ValidationFailureError("The attendance end date must not precede the start date.")
    if (end_date - start_date).days > 92:
        raise ValidationFailureError("Attendance ranges are limited to 93 days.")
    filters = [
        AttendanceRecord.company_id == company_id,
        AttendanceRecord.work_date >= start_date,
        AttendanceRecord.work_date <= end_date,
    ]
    if employee_id is not None:
        filters.append(AttendanceRecord.employee_id == employee_id)
    total = await session.scalar(select(func.count(AttendanceRecord.id)).where(*filters))
    rows = (
        await session.execute(
            select(AttendanceRecord, Employee)
            .join(Employee, Employee.id == AttendanceRecord.employee_id)
            .where(*filters)
            .order_by(AttendanceRecord.work_date.desc(), Employee.last_name)
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()
    return AttendancePage(
        items=[_attendance_response(record, employee) for record, employee in rows],
        total=total or 0,
        page=pagination.page,
        page_size=pagination.page_size,
    )


async def _assert_no_leave(session: AsyncSession, employee_id: uuid.UUID, work_date: date) -> None:
    covered = await session.scalar(
        select(LeaveRequest.id).where(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= work_date,
            LeaveRequest.end_date >= work_date,
        )
    )
    if covered is not None:
        raise ConflictError("Approved leave already covers that date.")


async def create_attendance(
    session: AsyncSession, context: AuthContext, payload: AttendanceCreate
) -> AttendanceResponse:
    employee = await _employee_for_update(
        session, context, payload.employee_id, PermissionCode.ATTENDANCE_MANAGE
    )
    if payload.work_date > date.today():
        raise ValidationFailureError("Attendance cannot be recorded for a future date.")
    if payload.work_date.weekday() >= 5:
        raise ValidationFailureError("Attendance is recorded for weekdays only.")
    await _assert_no_leave(session, employee.id, payload.work_date)
    existing = await session.scalar(
        select(AttendanceRecord.id).where(
            AttendanceRecord.employee_id == employee.id,
            AttendanceRecord.work_date == payload.work_date,
        )
    )
    if existing is not None:
        raise ConflictError("Attendance already exists for that employee and date.")
    is_holiday = await session.scalar(
        select(CompanyHoliday.id).where(
            CompanyHoliday.company_id == employee.company_id,
            CompanyHoliday.holiday_date == payload.work_date,
        )
    )
    if is_holiday is not None and payload.status != "holiday":
        raise ValidationFailureError("That date is a company holiday; record it as a holiday.")
    record = AttendanceRecord(
        organization_id=employee.organization_id,
        company_id=employee.company_id,
        employee_id=employee.id,
        work_date=payload.work_date,
        status=payload.status,
        source="manual",
        note=payload.note.strip() if payload.note else None,
        created_by=context.user.id,
        updated_by=context.user.id,
    )
    session.add(record)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="attendance.recorded",
        entity_type="attendance_record",
        entity_id=record.id,
        metadata={
            "employee_id": str(employee.id),
            "work_date": payload.work_date.isoformat(),
            "status": payload.status,
        },
    )
    return _attendance_response(record, employee)


async def update_attendance(
    session: AsyncSession,
    context: AuthContext,
    record_id: uuid.UUID,
    payload: AttendanceUpdate,
) -> AttendanceResponse:
    snapshot = await session.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.id == record_id,
            AttendanceRecord.organization_id == context.organization.id,
        )
    )
    if snapshot is None:
        raise NotFoundError("Attendance record not found.")
    employee = await _employee_for_update(
        session, context, snapshot.employee_id, PermissionCode.ATTENDANCE_MANAGE
    )
    record = await session.scalar(
        select(AttendanceRecord).where(AttendanceRecord.id == record_id).with_for_update()
    )
    if record is None:
        raise NotFoundError("Attendance record not found.")
    if payload.version is not None and payload.version != record.version:
        raise ConflictError("This attendance record was changed by another user. Reload it.")
    if record.source != "manual":
        raise ConflictError("Leave attendance is managed through the leave request.")
    await _assert_no_leave(session, record.employee_id, record.work_date)
    if payload.status is not None:
        record.status = payload.status
    if payload.note is not None:
        record.note = payload.note.strip() or None
    record.updated_by = context.user.id
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="attendance.updated",
        entity_type="attendance_record",
        entity_id=record.id,
        metadata={
            "employee_id": str(employee.id),
            "work_date": record.work_date.isoformat(),
            "status": record.status,
        },
    )
    return _attendance_response(record, employee)
