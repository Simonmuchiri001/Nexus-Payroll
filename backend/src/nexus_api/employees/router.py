"""Employee, compensation and termination API routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from nexus_api.api.deps import AuthDep, PaginationDep, SessionDep
from nexus_api.employees import service
from nexus_api.employees.schemas import (
    CompensationCreate,
    CompensationResponse,
    EmployeeCreate,
    EmployeePage,
    EmployeeResponse,
    EmployeeUpdate,
    TerminationRequest,
)

router = APIRouter(prefix="/employees", tags=["Employees"])


@router.get("", response_model=EmployeePage)
async def employees(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    pagination: PaginationDep,
    q: Annotated[str | None, Query(max_length=120)] = None,
    status: Annotated[
        str | None, Query(pattern="^(active|probation|suspended|terminated)$")
    ] = None,
) -> EmployeePage:
    items, total = await service.list_employees(
        session,
        context,
        company_id=company_id,
        pagination=pagination,
        search=q,
        status=status,
    )
    return EmployeePage(
        items=[EmployeeResponse.model_validate(employee) for employee in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("", response_model=EmployeeResponse, status_code=201)
async def create_employee(
    payload: EmployeeCreate,
    response: Response,
    session: SessionDep,
    context: AuthDep,
) -> EmployeeResponse:
    employee = await service.create_employee(session, context, payload)
    response.headers["Location"] = f"/api/v1/employees/{employee.id}"
    return EmployeeResponse.model_validate(employee)


@router.get("/{employee_id}", response_model=EmployeeResponse)
async def employee_detail(
    employee_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> EmployeeResponse:
    return EmployeeResponse.model_validate(
        await service.get_employee(session, context, employee_id)
    )


@router.patch("/{employee_id}", response_model=EmployeeResponse)
async def update_employee(
    employee_id: uuid.UUID,
    payload: EmployeeUpdate,
    session: SessionDep,
    context: AuthDep,
) -> EmployeeResponse:
    employee = await service.get_employee(session, context, employee_id)
    updated = await service.update_employee(session, context, employee, payload)
    return EmployeeResponse.model_validate(updated)


@router.post("/{employee_id}/terminate", response_model=EmployeeResponse)
async def terminate_employee(
    employee_id: uuid.UUID,
    payload: TerminationRequest,
    session: SessionDep,
    context: AuthDep,
) -> EmployeeResponse:
    employee = await service.get_employee(session, context, employee_id)
    terminated = await service.terminate_employee(session, context, employee, payload)
    return EmployeeResponse.model_validate(terminated)


@router.get("/{employee_id}/compensation", response_model=list[CompensationResponse])
async def compensations(
    employee_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> list[CompensationResponse]:
    employee = await service.get_employee(session, context, employee_id)
    records = await service.list_compensations(session, context, employee)
    return [CompensationResponse.model_validate(record) for record in records]


@router.post(
    "/{employee_id}/compensation",
    response_model=CompensationResponse,
    status_code=201,
)
async def create_compensation(
    employee_id: uuid.UUID,
    payload: CompensationCreate,
    session: SessionDep,
    context: AuthDep,
) -> CompensationResponse:
    employee = await service.get_employee(session, context, employee_id)
    record = await service.create_compensation(session, context, employee, payload)
    return CompensationResponse.model_validate(record)
