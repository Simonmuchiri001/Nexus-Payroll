"""Payroll API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response
from sqlalchemy import select

from nexus_api.api.deps import AuthDep, PaginationDep, SessionDep
from nexus_api.employees.models import Employee
from nexus_api.payroll import service
from nexus_api.payroll.schemas import (
    PayGroupCreate,
    PayGroupResponse,
    PayrollPeriodCreate,
    PayrollPeriodResponse,
    PayrollResultResponse,
    PayrollRunCreate,
    PayrollRunResponse,
    RuleSetResponse,
)

router = APIRouter(prefix="/payroll", tags=["Payroll"])


@router.get("/rule-sets", response_model=list[RuleSetResponse])
async def rule_sets(session: SessionDep, context: AuthDep) -> list[RuleSetResponse]:
    return [
        RuleSetResponse.model_validate(rule_set)
        for rule_set in await service.list_rule_sets(session, context)
    ]


@router.post("/pay-groups", response_model=PayGroupResponse, status_code=201)
async def create_pay_group(
    payload: PayGroupCreate, session: SessionDep, context: AuthDep
) -> PayGroupResponse:
    group = await service.create_pay_group(session, context, payload)
    return PayGroupResponse.model_validate(group)


@router.get("/pay-groups", response_model=list[PayGroupResponse])
async def pay_groups(
    company_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> list[PayGroupResponse]:
    return [
        PayGroupResponse.model_validate(group)
        for group in await service.list_pay_groups(session, context, company_id)
    ]


@router.post("/periods", response_model=PayrollPeriodResponse, status_code=201)
async def create_period(
    payload: PayrollPeriodCreate, session: SessionDep, context: AuthDep
) -> PayrollPeriodResponse:
    period = await service.create_payroll_period(session, context, payload)
    return PayrollPeriodResponse.model_validate(period)


@router.get("/periods", response_model=list[PayrollPeriodResponse])
async def periods(
    pay_group_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> list[PayrollPeriodResponse]:
    return [
        PayrollPeriodResponse.model_validate(period)
        for period in await service.list_payroll_periods(session, context, pay_group_id)
    ]


@router.post("/runs", response_model=PayrollRunResponse, status_code=201)
async def create_run(
    payload: PayrollRunCreate,
    response: Response,
    session: SessionDep,
    context: AuthDep,
) -> PayrollRunResponse:
    run = await service.create_run(session, context, payload)
    response.headers["Location"] = f"/api/v1/payroll/runs/{run.id}"
    return PayrollRunResponse.model_validate(run)


@router.get("/runs", response_model=list[PayrollRunResponse])
async def runs(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    pagination: PaginationDep,
    status: str | None = None,
) -> list[PayrollRunResponse]:
    items, _ = await service.list_runs(
        session,
        context,
        company_id=company_id,
        pagination=pagination,
        status=status,
    )
    return [PayrollRunResponse.model_validate(run) for run in items]


@router.get("/runs/{run_id}", response_model=PayrollRunResponse)
async def run_detail(
    run_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> PayrollRunResponse:
    return PayrollRunResponse.model_validate(await service.get_run(session, context, run_id))


@router.get("/runs/{run_id}/results", response_model=list[PayrollResultResponse])
async def run_results(
    run_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> list[PayrollResultResponse]:
    results = await service.list_results(session, context, run_id)
    if not results:
        return []
    employees = {
        employee.id: employee
        for employee in (
            await session.scalars(
                select(Employee).where(Employee.id.in_({result.employee_id for result in results}))
            )
        ).all()
    }
    responses: list[PayrollResultResponse] = []
    for result in results:
        employee = employees.get(result.employee_id)
        item = PayrollResultResponse.model_validate(result)
        if employee is not None:
            item = item.model_copy(
                update={
                    "employee_number": employee.employee_number,
                    "employee_name": " ".join(
                        part
                        for part in (
                            employee.first_name,
                            employee.middle_name,
                            employee.last_name,
                        )
                        if part
                    ),
                }
            )
        responses.append(item)
    return responses


@router.post("/runs/{run_id}/calculate", response_model=PayrollRunResponse)
async def calculate(run_id: uuid.UUID, session: SessionDep, context: AuthDep) -> PayrollRunResponse:
    return PayrollRunResponse.model_validate(await service.calculate_run(session, context, run_id))


@router.post("/runs/{run_id}/approve", response_model=PayrollRunResponse)
async def approve(run_id: uuid.UUID, session: SessionDep, context: AuthDep) -> PayrollRunResponse:
    return PayrollRunResponse.model_validate(await service.approve_run(session, context, run_id))


@router.post("/runs/{run_id}/finalize", response_model=PayrollRunResponse)
async def finalize(run_id: uuid.UUID, session: SessionDep, context: AuthDep) -> PayrollRunResponse:
    return PayrollRunResponse.model_validate(await service.finalize_run(session, context, run_id))


@router.post("/runs/{run_id}/post", response_model=PayrollRunResponse)
async def post_to_accounting(
    run_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> PayrollRunResponse:
    return PayrollRunResponse.model_validate(await service.post_run(session, context, run_id))
