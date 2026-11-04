"""Leave configuration, request workflow and attendance API routes."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response

from nexus_api.api.deps import AuthDep, PaginationDep, SessionDep
from nexus_api.leave import service
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

router = APIRouter(tags=["Leave and attendance"])


@router.get("/leave/types", response_model=list[LeaveTypeResponse])
async def leave_types(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    status: Annotated[str | None, Query(pattern="^(active|archived)$")] = None,
) -> list[LeaveTypeResponse]:
    return await service.list_leave_types(session, context, company_id=company_id, status=status)


@router.post("/leave/types", response_model=LeaveTypeResponse, status_code=201)
async def create_leave_type(
    payload: LeaveTypeCreate,
    response: Response,
    session: SessionDep,
    context: AuthDep,
) -> LeaveTypeResponse:
    leave_type = await service.create_leave_type(session, context, payload)
    response.headers["Location"] = f"/api/v1/leave/types/{leave_type.id}"
    return leave_type


@router.patch("/leave/types/{leave_type_id}", response_model=LeaveTypeResponse)
async def update_leave_type(
    leave_type_id: uuid.UUID,
    payload: LeaveTypeUpdate,
    session: SessionDep,
    context: AuthDep,
) -> LeaveTypeResponse:
    leave_type = await service.get_leave_type(session, context, leave_type_id)
    return await service.update_leave_type(session, context, leave_type, payload)


@router.get("/leave/holidays", response_model=list[HolidayResponse])
async def holidays(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> list[HolidayResponse]:
    return await service.list_holidays(session, context, company_id=company_id, year=year)


@router.post("/leave/holidays", response_model=HolidayResponse, status_code=201)
async def create_holiday(
    payload: HolidayCreate,
    response: Response,
    session: SessionDep,
    context: AuthDep,
) -> HolidayResponse:
    holiday = await service.create_holiday(session, context, payload)
    response.headers["Location"] = f"/api/v1/leave/holidays/{holiday.id}"
    return holiday


@router.delete("/leave/holidays/{holiday_id}", status_code=204)
async def delete_holiday(holiday_id: uuid.UUID, session: SessionDep, context: AuthDep) -> Response:
    await service.delete_holiday(session, context, holiday_id)
    return Response(status_code=204)


@router.get("/leave/balances", response_model=list[LeaveBalanceResponse])
async def balances(
    employee_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    leave_year: Annotated[int, Query(ge=2000, le=2100)],
) -> list[LeaveBalanceResponse]:
    return await service.list_balances(
        session, context, employee_id=employee_id, leave_year=leave_year
    )


@router.post("/leave/balances/initialize", response_model=list[LeaveBalanceResponse])
async def initialize_balances(
    payload: LeaveBalanceInitialize, session: SessionDep, context: AuthDep
) -> list[LeaveBalanceResponse]:
    return await service.initialize_balances(session, context, payload)


@router.patch("/leave/balances/{balance_id}", response_model=LeaveBalanceResponse)
async def adjust_balance(
    balance_id: uuid.UUID,
    payload: LeaveBalanceAdjust,
    session: SessionDep,
    context: AuthDep,
) -> LeaveBalanceResponse:
    return await service.adjust_balance(session, context, balance_id, payload)


@router.get("/leave/requests", response_model=LeaveRequestPage)
async def leave_requests(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    pagination: PaginationDep,
    employee_id: uuid.UUID | None = None,
    status: Annotated[str | None, Query(pattern="^(pending|approved|rejected|cancelled)$")] = None,
    leave_year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> LeaveRequestPage:
    return await service.list_leave_requests(
        session,
        context,
        company_id=company_id,
        pagination=pagination,
        employee_id=employee_id,
        status=status,
        leave_year=leave_year,
    )


@router.post("/leave/requests", response_model=LeaveRequestResponse, status_code=201)
async def create_leave_request(
    payload: LeaveRequestCreate,
    response: Response,
    session: SessionDep,
    context: AuthDep,
) -> LeaveRequestResponse:
    leave_request = await service.create_leave_request(session, context, payload)
    response.headers["Location"] = f"/api/v1/leave/requests/{leave_request.id}"
    return leave_request


@router.post("/leave/requests/{request_id}/approve", response_model=LeaveRequestResponse)
async def approve_leave_request(
    request_id: uuid.UUID,
    payload: LeaveDecision,
    session: SessionDep,
    context: AuthDep,
) -> LeaveRequestResponse:
    return await service.approve_leave_request(session, context, request_id, payload)


@router.post("/leave/requests/{request_id}/reject", response_model=LeaveRequestResponse)
async def reject_leave_request(
    request_id: uuid.UUID,
    payload: LeaveDecision,
    session: SessionDep,
    context: AuthDep,
) -> LeaveRequestResponse:
    return await service.reject_leave_request(session, context, request_id, payload)


@router.post("/leave/requests/{request_id}/cancel", response_model=LeaveRequestResponse)
async def cancel_leave_request(
    request_id: uuid.UUID,
    payload: LeaveDecision,
    session: SessionDep,
    context: AuthDep,
) -> LeaveRequestResponse:
    return await service.cancel_leave_request(session, context, request_id, payload)


@router.get("/attendance", response_model=AttendancePage)
async def attendance(
    company_id: uuid.UUID,
    start_date: date,
    end_date: date,
    session: SessionDep,
    context: AuthDep,
    pagination: PaginationDep,
    employee_id: uuid.UUID | None = None,
) -> AttendancePage:
    return await service.list_attendance(
        session,
        context,
        company_id=company_id,
        pagination=pagination,
        start_date=start_date,
        end_date=end_date,
        employee_id=employee_id,
    )


@router.post("/attendance", response_model=AttendanceResponse, status_code=201)
async def create_attendance(
    payload: AttendanceCreate,
    response: Response,
    session: SessionDep,
    context: AuthDep,
) -> AttendanceResponse:
    record = await service.create_attendance(session, context, payload)
    response.headers["Location"] = f"/api/v1/attendance/{record.id}"
    return record


@router.patch("/attendance/{record_id}", response_model=AttendanceResponse)
async def update_attendance(
    record_id: uuid.UUID,
    payload: AttendanceUpdate,
    session: SessionDep,
    context: AuthDep,
) -> AttendanceResponse:
    return await service.update_attendance(session, context, record_id, payload)
