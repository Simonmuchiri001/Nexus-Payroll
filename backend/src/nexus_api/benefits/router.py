"""Benefit plan and enrolment API routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from nexus_api.api.deps import AuthDep, PaginationDep, SessionDep
from nexus_api.benefits import service
from nexus_api.benefits.schemas import (
    BenefitEnrollmentCreate,
    BenefitEnrollmentEnd,
    BenefitEnrollmentPage,
    BenefitEnrollmentResponse,
    BenefitPlanCreate,
    BenefitPlanResponse,
    BenefitPlanUpdate,
)

router = APIRouter(prefix="/benefits", tags=["Benefits"])


@router.get("/plans", response_model=list[BenefitPlanResponse])
async def plans(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    status: Annotated[str | None, Query(pattern="^(active|archived)$")] = None,
) -> list[BenefitPlanResponse]:
    return await service.list_plans(session, context, company_id=company_id, status=status)


@router.post("/plans", response_model=BenefitPlanResponse, status_code=201)
async def create_plan(
    payload: BenefitPlanCreate,
    session: SessionDep,
    context: AuthDep,
) -> BenefitPlanResponse:
    return await service.create_plan(session, context, payload)


@router.patch("/plans/{plan_id}", response_model=BenefitPlanResponse)
async def update_plan(
    plan_id: uuid.UUID,
    payload: BenefitPlanUpdate,
    session: SessionDep,
    context: AuthDep,
) -> BenefitPlanResponse:
    return await service.update_plan(session, context, plan_id, payload)


@router.get("/enrollments", response_model=BenefitEnrollmentPage)
async def enrollments(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    pagination: PaginationDep,
    employee_id: uuid.UUID | None = None,
    plan_id: uuid.UUID | None = None,
    status: Annotated[str | None, Query(pattern="^(active|ended)$")] = None,
) -> BenefitEnrollmentPage:
    return await service.list_enrollments(
        session,
        context,
        company_id=company_id,
        pagination=pagination,
        employee_id=employee_id,
        plan_id=plan_id,
        status=status,
    )


@router.post("/enrollments", response_model=BenefitEnrollmentResponse, status_code=201)
async def create_enrollment(
    payload: BenefitEnrollmentCreate,
    session: SessionDep,
    context: AuthDep,
) -> BenefitEnrollmentResponse:
    return await service.create_enrollment(session, context, payload)


@router.post(
    "/enrollments/{enrollment_id}/end",
    response_model=BenefitEnrollmentResponse,
)
async def end_enrollment(
    enrollment_id: uuid.UUID,
    payload: BenefitEnrollmentEnd,
    session: SessionDep,
    context: AuthDep,
) -> BenefitEnrollmentResponse:
    return await service.end_enrollment(session, context, enrollment_id, payload)
