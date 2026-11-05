"""Employee loan and advance API routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from nexus_api.api.deps import AuthDep, PaginationDep, SessionDep
from nexus_api.loans import service
from nexus_api.loans.schemas import (
    LoanApplicationCreate,
    LoanDecision,
    LoanDetailResponse,
    LoanDisbursement,
    LoanPage,
    LoanResponse,
)

router = APIRouter(prefix="/loans", tags=["Loans and advances"])


@router.get("", response_model=LoanPage)
async def loans(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    pagination: PaginationDep,
    employee_id: uuid.UUID | None = None,
    status: Annotated[
        str | None,
        Query(pattern=("^(pending|approved|rejected|cancelled|disbursed|closed)$")),
    ] = None,
    kind: Annotated[str | None, Query(pattern="^(loan|advance)$")] = None,
) -> LoanPage:
    return await service.list_loans(
        session,
        context,
        company_id=company_id,
        pagination=pagination,
        employee_id=employee_id,
        status=status,
        kind=kind,
    )


@router.post("", response_model=LoanResponse, status_code=201)
async def create_loan(
    payload: LoanApplicationCreate,
    response: Response,
    session: SessionDep,
    context: AuthDep,
) -> LoanResponse:
    loan = await service.create_loan(session, context, payload)
    response.headers["Location"] = f"/api/v1/loans/{loan.id}"
    return loan


@router.get("/{loan_id}", response_model=LoanDetailResponse)
async def loan_detail(
    loan_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> LoanDetailResponse:
    return await service.get_loan(session, context, loan_id)


@router.post("/{loan_id}/approve", response_model=LoanDetailResponse)
async def approve_loan(
    loan_id: uuid.UUID,
    payload: LoanDecision,
    session: SessionDep,
    context: AuthDep,
) -> LoanDetailResponse:
    return await service.approve_loan(session, context, loan_id, payload)


@router.post("/{loan_id}/reject", response_model=LoanDetailResponse)
async def reject_loan(
    loan_id: uuid.UUID,
    payload: LoanDecision,
    session: SessionDep,
    context: AuthDep,
) -> LoanDetailResponse:
    return await service.reject_loan(session, context, loan_id, payload)


@router.post("/{loan_id}/cancel", response_model=LoanDetailResponse)
async def cancel_loan(
    loan_id: uuid.UUID,
    payload: LoanDecision,
    session: SessionDep,
    context: AuthDep,
) -> LoanDetailResponse:
    return await service.cancel_loan(session, context, loan_id, payload)


@router.post("/{loan_id}/disburse", response_model=LoanDetailResponse)
async def disburse_loan(
    loan_id: uuid.UUID,
    payload: LoanDisbursement,
    session: SessionDep,
    context: AuthDep,
) -> LoanDetailResponse:
    return await service.disburse_loan(session, context, loan_id, payload)
