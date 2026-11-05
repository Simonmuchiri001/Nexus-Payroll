"""Expense category and reimbursement API routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from nexus_api.api.deps import AuthDep, PaginationDep, SessionDep
from nexus_api.expenses import service
from nexus_api.expenses.schemas import (
    ExpenseCategoryCreate,
    ExpenseCategoryResponse,
    ExpenseCategoryUpdate,
    ExpenseClaimCreate,
    ExpenseClaimPage,
    ExpenseClaimResponse,
    ExpenseClaimUpdate,
    ExpenseDecision,
    ExpenseReimbursement,
)

router = APIRouter(prefix="/expenses", tags=["Expenses"])


@router.get("/categories", response_model=list[ExpenseCategoryResponse])
async def categories(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    status: Annotated[str | None, Query(pattern="^(active|archived)$")] = None,
) -> list[ExpenseCategoryResponse]:
    return await service.list_categories(session, context, company_id=company_id, status=status)


@router.post("/categories", response_model=ExpenseCategoryResponse, status_code=201)
async def create_category(
    payload: ExpenseCategoryCreate, session: SessionDep, context: AuthDep
) -> ExpenseCategoryResponse:
    return await service.create_category(session, context, payload)


@router.patch("/categories/{category_id}", response_model=ExpenseCategoryResponse)
async def update_category(
    category_id: uuid.UUID,
    payload: ExpenseCategoryUpdate,
    session: SessionDep,
    context: AuthDep,
) -> ExpenseCategoryResponse:
    return await service.update_category(session, context, category_id, payload)


@router.get("/claims", response_model=ExpenseClaimPage)
async def claims(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    pagination: PaginationDep,
    employee_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    status: Annotated[
        str | None,
        Query(pattern=("^(draft|submitted|approved|rejected|reimbursed|cancelled)$")),
    ] = None,
) -> ExpenseClaimPage:
    return await service.list_claims(
        session,
        context,
        company_id=company_id,
        pagination=pagination,
        employee_id=employee_id,
        category_id=category_id,
        status=status,
    )


@router.post("/claims", response_model=ExpenseClaimResponse, status_code=201)
async def create_claim(
    payload: ExpenseClaimCreate,
    response: Response,
    session: SessionDep,
    context: AuthDep,
) -> ExpenseClaimResponse:
    claim = await service.create_claim(session, context, payload)
    response.headers["Location"] = f"/api/v1/expenses/claims/{claim.id}"
    return claim


@router.get("/claims/{claim_id}", response_model=ExpenseClaimResponse)
async def claim_detail(
    claim_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> ExpenseClaimResponse:
    return await service.get_claim(session, context, claim_id)


@router.patch("/claims/{claim_id}", response_model=ExpenseClaimResponse)
async def update_claim(
    claim_id: uuid.UUID,
    payload: ExpenseClaimUpdate,
    session: SessionDep,
    context: AuthDep,
) -> ExpenseClaimResponse:
    return await service.update_claim(session, context, claim_id, payload)


@router.post("/claims/{claim_id}/submit", response_model=ExpenseClaimResponse)
async def submit_claim(
    claim_id: uuid.UUID,
    payload: ExpenseDecision,
    session: SessionDep,
    context: AuthDep,
) -> ExpenseClaimResponse:
    return await service.submit_claim(session, context, claim_id, payload)


@router.post("/claims/{claim_id}/approve", response_model=ExpenseClaimResponse)
async def approve_claim(
    claim_id: uuid.UUID,
    payload: ExpenseDecision,
    session: SessionDep,
    context: AuthDep,
) -> ExpenseClaimResponse:
    return await service.approve_claim(session, context, claim_id, payload)


@router.post("/claims/{claim_id}/reject", response_model=ExpenseClaimResponse)
async def reject_claim(
    claim_id: uuid.UUID,
    payload: ExpenseDecision,
    session: SessionDep,
    context: AuthDep,
) -> ExpenseClaimResponse:
    return await service.reject_claim(session, context, claim_id, payload)


@router.post("/claims/{claim_id}/cancel", response_model=ExpenseClaimResponse)
async def cancel_claim(
    claim_id: uuid.UUID,
    payload: ExpenseDecision,
    session: SessionDep,
    context: AuthDep,
) -> ExpenseClaimResponse:
    return await service.cancel_claim(session, context, claim_id, payload)


@router.post("/claims/{claim_id}/reimburse", response_model=ExpenseClaimResponse)
async def reimburse_claim(
    claim_id: uuid.UUID,
    payload: ExpenseReimbursement,
    session: SessionDep,
    context: AuthDep,
) -> ExpenseClaimResponse:
    return await service.reimburse_claim(session, context, claim_id, payload)
