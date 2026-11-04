"""Double-entry accounting services and payroll posting."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.accounting.models import Account, FiscalPeriod, Journal, JournalLine
from nexus_api.accounting.schemas import (
    AccountCreate,
    JournalCreate,
    JournalLineCreate,
    TrialBalanceRow,
)
from nexus_api.api.deps import PaginationParams
from nexus_api.audit.service import record_audit
from nexus_api.core.errors import (
    ConfigurationError,
    ConflictError,
    IntegrityError,
    NotFoundError,
    ValidationFailureError,
)
from nexus_api.identity.permissions import PermissionCode
from nexus_api.identity.service import AuthContext, validate_company_access
from nexus_api.organizations.models import Company, CostCentre
from nexus_api.payroll.models import PayrollResult, PayrollRun

CENT = Decimal("0.01")

DEFAULT_CHART: tuple[tuple[str, str, str, str], ...] = (
    ("1000", "Cash at Bank", "asset", "debit"),
    ("1100", "Accounts Receivable", "asset", "debit"),
    ("2000", "Accounts Payable", "liability", "credit"),
    ("2100", "PAYE Payable", "liability", "credit"),
    ("2110", "NSSF Payable", "liability", "credit"),
    ("2120", "SHIF Payable", "liability", "credit"),
    ("2130", "Affordable Housing Levy Payable", "liability", "credit"),
    ("2140", "NITA Payable", "liability", "credit"),
    ("2200", "Net Pay Payable", "liability", "credit"),
    ("3000", "Retained Earnings", "equity", "credit"),
    ("4000", "Revenue", "revenue", "credit"),
    ("5100", "Salaries and Wages", "expense", "debit"),
    ("5200", "Employer Statutory Contributions", "expense", "debit"),
    ("5300", "General Expenses", "expense", "debit"),
)

SYSTEM_CODES = {
    "salary_expense": "5100",
    "employer_expense": "5200",
    "paye_payable": "2100",
    "nssf_payable": "2110",
    "shif_payable": "2120",
    "housing_payable": "2130",
    "nita_payable": "2140",
    "net_pay_payable": "2200",
}


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


async def ensure_default_chart(session: AsyncSession, company: Company) -> None:
    existing = set(
        (await session.scalars(select(Account.code).where(Account.company_id == company.id))).all()
    )
    for code, name, account_type, normal_balance in DEFAULT_CHART:
        if code in existing:
            continue
        session.add(
            Account(
                organization_id=company.organization_id,
                company_id=company.id,
                code=code,
                name=name,
                account_type=account_type,
                normal_balance=normal_balance,
                is_system=True,
                is_active=True,
            )
        )
    await session.flush()


def fiscal_year_start_for(company: Company, on_date: date) -> date:
    month = company.fiscal_year_start_month
    start_year = on_date.year if on_date.month >= month else on_date.year - 1
    return date(start_year, month, 1)


async def ensure_fiscal_year(
    session: AsyncSession, company: Company, start_year: int
) -> list[FiscalPeriod]:
    start = date(start_year, company.fiscal_year_start_month, 1)
    periods: list[FiscalPeriod] = []
    for offset in range(12):
        period_start = _add_months(start, offset)
        period_end = _add_months(period_start, 1) - date.resolution
        existing = await session.scalar(
            select(FiscalPeriod).where(
                FiscalPeriod.company_id == company.id,
                FiscalPeriod.start_date == period_start,
            )
        )
        if existing is None:
            existing = FiscalPeriod(
                organization_id=company.organization_id,
                company_id=company.id,
                name=period_start.strftime("%Y-%m"),
                start_date=period_start,
                end_date=period_end,
                status="open",
            )
            session.add(existing)
            await session.flush()
        periods.append(existing)
    return periods


async def _next_journal_number(
    session: AsyncSession, company_id: uuid.UUID, journal_date: date
) -> str:
    await session.execute(select(Company.id).where(Company.id == company_id).with_for_update())
    prefix = f"JV-{journal_date.year}-"
    count = await session.scalar(
        select(func.count(Journal.id)).where(
            Journal.company_id == company_id,
            Journal.journal_number.like(f"{prefix}%"),
        )
    )
    return f"{prefix}{(count or 0) + 1:05d}"


async def list_accounts(
    session: AsyncSession, context: AuthContext, company_id: uuid.UUID
) -> list[Account]:
    await validate_company_access(session, context, company_id, PermissionCode.ACCOUNTING_VIEW)
    return list(
        (
            await session.scalars(
                select(Account).where(Account.company_id == company_id).order_by(Account.code)
            )
        ).all()
    )


async def create_account(
    session: AsyncSession, context: AuthContext, payload: AccountCreate
) -> Account:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.ACCOUNTING_POST
    )
    code = payload.code.upper()
    existing = await session.scalar(
        select(Account.id).where(Account.company_id == company.id, Account.code == code)
    )
    if existing is not None:
        raise ConflictError("An account with that code already exists.")
    account = Account(
        organization_id=context.organization.id,
        company_id=company.id,
        code=code,
        name=payload.name.strip(),
        account_type=payload.account_type,
        normal_balance=payload.normal_balance,
        is_system=False,
        is_active=True,
    )
    session.add(account)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="account.created",
        entity_type="account",
        entity_id=account.id,
        metadata={"company_id": str(company.id), "code": code},
    )
    return account


async def list_fiscal_periods(
    session: AsyncSession, context: AuthContext, company_id: uuid.UUID
) -> list[FiscalPeriod]:
    await validate_company_access(session, context, company_id, PermissionCode.ACCOUNTING_VIEW)
    return list(
        (
            await session.scalars(
                select(FiscalPeriod)
                .where(FiscalPeriod.company_id == company_id)
                .order_by(FiscalPeriod.start_date)
            )
        ).all()
    )


async def _validate_line_accounts(
    session: AsyncSession, company_id: uuid.UUID, lines: list[JournalLineCreate]
) -> None:
    account_ids = {line.account_id for line in lines}
    accounts = (
        await session.scalars(
            select(Account.id).where(
                Account.company_id == company_id,
                Account.id.in_(account_ids),
                Account.is_active.is_(True),
            )
        )
    ).all()
    if set(accounts) != account_ids:
        raise ValidationFailureError(
            "One or more journal accounts do not belong to the company or are inactive."
        )
    cost_centre_ids = {line.cost_centre_id for line in lines if line.cost_centre_id is not None}
    if cost_centre_ids:
        centres = set(
            (
                await session.scalars(
                    select(CostCentre.id).where(
                        CostCentre.company_id == company_id,
                        CostCentre.id.in_(cost_centre_ids),
                    )
                )
            ).all()
        )
        if centres != cost_centre_ids:
            raise ValidationFailureError("One or more cost centres do not belong to the company.")


async def create_journal(
    session: AsyncSession, context: AuthContext, payload: JournalCreate
) -> Journal:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.ACCOUNTING_POST
    )
    await _validate_line_accounts(session, company.id, payload.lines)
    debit = _money(sum((line.debit for line in payload.lines), Decimal("0.00")))
    credit = _money(sum((line.credit for line in payload.lines), Decimal("0.00")))
    if debit != credit:
        raise ValidationFailureError("Journal debits and credits must be equal.")
    if debit == 0:
        raise ValidationFailureError("A journal must contain a non-zero amount.")

    journal = Journal(
        organization_id=context.organization.id,
        company_id=company.id,
        journal_number=await _next_journal_number(session, company.id, payload.journal_date),
        journal_date=payload.journal_date,
        description=payload.description.strip(),
        reference=payload.reference,
        status="draft",
        currency=company.base_currency,
        total_debit=debit,
        total_credit=credit,
        created_by=context.user.id,
    )
    session.add(journal)
    await session.flush()
    for number, line in enumerate(payload.lines, start=1):
        session.add(
            JournalLine(
                journal_id=journal.id,
                line_number=number,
                account_id=line.account_id,
                description=line.description.strip(),
                debit=_money(line.debit),
                credit=_money(line.credit),
                cost_centre_id=line.cost_centre_id,
            )
        )
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="journal.created",
        entity_type="journal",
        entity_id=journal.id,
        metadata={
            "journal_number": journal.journal_number,
            "company_id": str(company.id),
            "total_debit": str(journal.total_debit),
        },
    )
    return journal


async def post_journal(
    session: AsyncSession, context: AuthContext, journal_id: uuid.UUID
) -> Journal:
    journal = await session.get(Journal, journal_id)
    if journal is None or journal.organization_id != context.organization.id:
        raise NotFoundError("Journal not found.")
    context.require(PermissionCode.ACCOUNTING_POST, company_id=journal.company_id)
    journal = await session.scalar(
        select(Journal).where(Journal.id == journal.id).with_for_update()
    )
    if journal is None:
        raise NotFoundError("Journal not found.")
    if journal.status == "posted":
        return journal
    if journal.status != "draft":
        raise ConflictError("Only a draft journal can be posted.")

    lines = (
        await session.scalars(select(JournalLine).where(JournalLine.journal_id == journal.id))
    ).all()
    debit = _money(sum((line.debit for line in lines), Decimal("0.00")))
    credit = _money(sum((line.credit for line in lines), Decimal("0.00")))
    if len(lines) < 2 or debit != credit or debit <= 0:
        raise IntegrityError("The journal does not satisfy double-entry requirements.")

    period = await session.scalar(
        select(FiscalPeriod).where(
            FiscalPeriod.company_id == journal.company_id,
            FiscalPeriod.start_date <= journal.journal_date,
            FiscalPeriod.end_date >= journal.journal_date,
        )
    )
    if period is None:
        raise ConfigurationError("No fiscal period covers the journal date.")
    if period.status != "open":
        raise ConflictError("The fiscal period is locked or closed.")

    journal.status = "posted"
    journal.posted_by = context.user.id
    journal.posted_at = datetime.now(UTC)
    journal.total_debit = debit
    journal.total_credit = credit
    await session.flush()
    await record_audit(
        session,
        organization_id=journal.organization_id,
        actor_user_id=context.user.id,
        action="journal.posted",
        entity_type="journal",
        entity_id=journal.id,
        metadata={
            "journal_number": journal.journal_number,
            "total_debit": str(debit),
            "fiscal_period": period.name,
        },
    )
    return journal


async def list_journals(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    pagination: PaginationParams,
    status: str | None,
) -> tuple[list[Journal], int]:
    await validate_company_access(session, context, company_id, PermissionCode.ACCOUNTING_VIEW)
    filters = [Journal.company_id == company_id]
    if status:
        filters.append(Journal.status == status)
    total = await session.scalar(select(func.count(Journal.id)).where(*filters))
    journals = (
        await session.scalars(
            select(Journal)
            .where(*filters)
            .order_by(Journal.journal_date.desc(), Journal.journal_number.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()
    return list(journals), total or 0


async def get_journal(
    session: AsyncSession, context: AuthContext, journal_id: uuid.UUID
) -> Journal:
    journal = await session.get(Journal, journal_id)
    if journal is None or journal.organization_id != context.organization.id:
        raise NotFoundError("Journal not found.")
    await validate_company_access(
        session, context, journal.company_id, PermissionCode.ACCOUNTING_VIEW
    )
    return journal


async def list_journal_lines(session: AsyncSession, journal_id: uuid.UUID) -> list[JournalLine]:
    return list(
        (
            await session.scalars(
                select(JournalLine)
                .where(JournalLine.journal_id == journal_id)
                .order_by(JournalLine.line_number)
            )
        ).all()
    )


async def trial_balance(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    as_of: date,
) -> tuple[list[TrialBalanceRow], Decimal, Decimal, str]:
    company = await validate_company_access(
        session, context, company_id, PermissionCode.ACCOUNTING_VIEW
    )
    totals = (
        await session.execute(
            select(
                JournalLine.account_id,
                func.coalesce(func.sum(JournalLine.debit), 0),
                func.coalesce(func.sum(JournalLine.credit), 0),
            )
            .join(Journal, Journal.id == JournalLine.journal_id)
            .where(
                Journal.company_id == company_id,
                Journal.status == "posted",
                Journal.journal_date <= as_of,
            )
            .group_by(JournalLine.account_id)
        )
    ).all()
    by_account: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    for account_id, debit, credit in totals:
        by_account[account_id] = (Decimal(str(debit)), Decimal(str(credit)))

    accounts = (
        await session.scalars(
            select(Account).where(Account.company_id == company_id).order_by(Account.code)
        )
    ).all()
    rows: list[TrialBalanceRow] = []
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    for account in accounts:
        debit, credit = by_account.get(account.id, (Decimal("0.00"), Decimal("0.00")))
        total_debit += debit
        total_credit += credit
        rows.append(
            TrialBalanceRow(
                account_id=account.id,
                code=account.code,
                name=account.name,
                account_type=account.account_type,
                debit=_money(debit),
                credit=_money(credit),
                balance=_money(debit - credit),
            )
        )
    total_debit = _money(total_debit)
    total_credit = _money(total_credit)
    return rows, total_debit, total_credit, company.base_currency


async def post_payroll_run(session: AsyncSession, context: AuthContext, run: PayrollRun) -> Journal:
    """Create and post the payroll journal exactly once for a finalised run."""

    existing = await session.scalar(
        select(Journal).where(
            Journal.company_id == run.company_id,
            Journal.source_type == "payroll_run",
            Journal.source_id == run.id,
        )
    )
    if existing is not None:
        if run.status != "posted":
            run.status = "posted"
            run.posted_at = existing.posted_at
            run.journal_id = existing.id
        return existing

    company = await session.get(Company, run.company_id)
    if company is None:
        raise NotFoundError("Company not found.")
    await ensure_default_chart(session, company)
    period = await session.scalar(
        select(FiscalPeriod).where(
            FiscalPeriod.company_id == company.id,
            FiscalPeriod.start_date <= run.payment_date,
            FiscalPeriod.end_date >= run.payment_date,
        )
    )
    if period is None:
        raise ConfigurationError(
            "No fiscal period covers the payroll payment date. Create the fiscal calendar first."
        )
    if period.status != "open":
        raise ConflictError("The payroll payment date falls in a locked or closed fiscal period.")

    accounts = {
        account.code: account
        for account in (
            await session.scalars(
                select(Account).where(
                    Account.company_id == company.id,
                    Account.code.in_(SYSTEM_CODES.values()),
                    Account.is_active.is_(True),
                )
            )
        ).all()
    }
    missing = sorted(set(SYSTEM_CODES.values()) - set(accounts))
    if missing:
        raise ConfigurationError(
            "The payroll posting account mapping is incomplete.",
            details={"missing_account_codes": missing},
        )

    results = await _run_results(session, run.id)
    employer_total = _money(run.employer_statutory_total)
    nssf_total = _money(
        sum(
            (result.nssf_employee + result.nssf_employer for result in results),
            Decimal("0.00"),
        )
    )
    shif_total = _run_shif_total(results)
    housing_total = _money(
        sum(
            (result.housing_levy_employee + result.housing_levy_employer for result in results),
            Decimal("0.00"),
        )
    )
    nita_total = _run_nita_total(results)
    debit_total = _money(run.gross_total + employer_total)
    credit_total = _money(
        run.paye_total + nssf_total + shif_total + housing_total + nita_total + run.net_total
    )
    if debit_total != credit_total:
        raise IntegrityError("Payroll posting is not balanced; the journal was not created.")

    journal = Journal(
        organization_id=run.organization_id,
        company_id=run.company_id,
        journal_number=await _next_journal_number(session, run.company_id, run.payment_date),
        journal_date=run.payment_date,
        description=f"Payroll posting for {run.run_number}",
        reference=run.run_number,
        status="posted",
        currency=run.currency,
        total_debit=debit_total,
        total_credit=credit_total,
        source_type="payroll_run",
        source_id=run.id,
        created_by=context.user.id,
        posted_by=context.user.id,
        posted_at=datetime.now(UTC),
    )
    session.add(journal)
    await session.flush()

    debit_lines = [
        (SYSTEM_CODES["salary_expense"], "Gross salaries and allowances", run.gross_total),
        (SYSTEM_CODES["employer_expense"], "Employer statutory contributions", employer_total),
    ]
    credit_lines = [
        (SYSTEM_CODES["paye_payable"], "PAYE payable", run.paye_total),
        (SYSTEM_CODES["nssf_payable"], "NSSF payable", nssf_total),
        (SYSTEM_CODES["shif_payable"], "SHIF payable", shif_total),
        (SYSTEM_CODES["housing_payable"], "Affordable Housing Levy payable", housing_total),
        (SYSTEM_CODES["nita_payable"], "NITA payable", nita_total),
        (SYSTEM_CODES["net_pay_payable"], "Net pay payable", run.net_total),
    ]
    sequence = 1
    for code, description, amount in debit_lines:
        if amount <= 0:
            continue
        session.add(
            JournalLine(
                journal_id=journal.id,
                line_number=sequence,
                account_id=accounts[code].id,
                description=description,
                debit=_money(amount),
                credit=Decimal("0.00"),
            )
        )
        sequence += 1
    for code, description, amount in credit_lines:
        if amount <= 0:
            continue
        session.add(
            JournalLine(
                journal_id=journal.id,
                line_number=sequence,
                account_id=accounts[code].id,
                description=description,
                debit=Decimal("0.00"),
                credit=_money(amount),
            )
        )
        sequence += 1

    run.status = "posted"
    run.posted_at = journal.posted_at
    run.journal_id = journal.id
    await session.flush()
    await record_audit(
        session,
        organization_id=run.organization_id,
        actor_user_id=context.user.id,
        action="payroll_run.posted",
        entity_type="payroll_run",
        entity_id=run.id,
        metadata={
            "run_number": run.run_number,
            "journal_id": str(journal.id),
            "journal_number": journal.journal_number,
            "total_debit": str(debit_total),
        },
    )
    return journal


async def _run_results(session: AsyncSession, run_id: uuid.UUID) -> list[PayrollResult]:
    return list(
        (
            await session.scalars(
                select(PayrollResult).where(PayrollResult.payroll_run_id == run_id)
            )
        ).all()
    )


def _run_shif_total(results: list[PayrollResult]) -> Decimal:
    return _money(sum((result.shif_employee for result in results), Decimal("0.00")))


def _run_nita_total(results: list[PayrollResult]) -> Decimal:
    return _money(sum((result.nita for result in results), Decimal("0.00")))
