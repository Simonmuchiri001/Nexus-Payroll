"""Accounting API routes."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Query, Response

from nexus_api.accounting import service
from nexus_api.accounting.schemas import (
    AccountCreate,
    AccountResponse,
    FiscalPeriodResponse,
    JournalCreate,
    JournalDetail,
    JournalLineResponse,
    JournalPage,
    JournalResponse,
    TrialBalanceResponse,
)
from nexus_api.api.deps import AuthDep, PaginationDep, SessionDep

router = APIRouter(prefix="/accounting", tags=["Accounting"])


@router.get("/accounts", response_model=list[AccountResponse])
async def accounts(
    company_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> list[AccountResponse]:
    return [
        AccountResponse.model_validate(account)
        for account in await service.list_accounts(session, context, company_id)
    ]


@router.post("/accounts", response_model=AccountResponse, status_code=201)
async def create_account(
    payload: AccountCreate, session: SessionDep, context: AuthDep
) -> AccountResponse:
    account = await service.create_account(session, context, payload)
    return AccountResponse.model_validate(account)


@router.get("/fiscal-periods", response_model=list[FiscalPeriodResponse])
async def fiscal_periods(
    company_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> list[FiscalPeriodResponse]:
    return [
        FiscalPeriodResponse.model_validate(period)
        for period in await service.list_fiscal_periods(session, context, company_id)
    ]


@router.get("/journals", response_model=JournalPage)
async def journals(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    pagination: PaginationDep,
    status: str | None = None,
) -> JournalPage:
    items, total = await service.list_journals(
        session,
        context,
        company_id=company_id,
        pagination=pagination,
        status=status,
    )
    return JournalPage(
        items=[JournalResponse.model_validate(journal) for journal in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("/journals", response_model=JournalResponse, status_code=201)
async def create_journal(
    payload: JournalCreate,
    response: Response,
    session: SessionDep,
    context: AuthDep,
) -> JournalResponse:
    journal = await service.create_journal(session, context, payload)
    response.headers["Location"] = f"/api/v1/accounting/journals/{journal.id}"
    return JournalResponse.model_validate(journal)


@router.get("/journals/{journal_id}", response_model=JournalDetail)
async def journal_detail(
    journal_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> JournalDetail:
    journal = await service.get_journal(session, context, journal_id)
    lines = await service.list_journal_lines(session, journal.id)
    return JournalDetail(
        journal=JournalResponse.model_validate(journal),
        lines=[JournalLineResponse.model_validate(line) for line in lines],
    )


@router.post("/journals/{journal_id}/post", response_model=JournalResponse)
async def post_journal(
    journal_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> JournalResponse:
    journal = await service.post_journal(session, context, journal_id)
    return JournalResponse.model_validate(journal)


@router.get("/trial-balance", response_model=TrialBalanceResponse)
async def trial_balance(
    company_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
    as_of: date | None = Query(default=None),
) -> TrialBalanceResponse:
    resolved = as_of or date.today()
    rows, total_debit, total_credit, currency = await service.trial_balance(
        session, context, company_id=company_id, as_of=resolved
    )
    return TrialBalanceResponse(
        company_id=company_id,
        as_of=resolved,
        currency=currency,
        rows=rows,
        total_debit=total_debit,
        total_credit=total_credit,
        balanced=total_debit == total_credit,
    )
