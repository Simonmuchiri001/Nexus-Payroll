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
from nexus_api.assets.models import AssetDepreciation, AssetDisposal, FixedAsset
from nexus_api.audit.service import record_audit
from nexus_api.core.errors import (
    ConfigurationError,
    ConflictError,
    IntegrityError,
    NotFoundError,
    ValidationFailureError,
)
from nexus_api.expenses.models import ExpenseCategory, ExpenseClaim
from nexus_api.identity.permissions import PermissionCode
from nexus_api.identity.service import AuthContext, validate_company_access
from nexus_api.loans.models import EmployeeLoan
from nexus_api.organizations.models import Company, CostCentre
from nexus_api.payroll.models import PayrollResult, PayrollRun

CENT = Decimal("0.01")

DEFAULT_CHART: tuple[tuple[str, str, str, str], ...] = (
    ("1000", "Cash at Bank", "asset", "debit"),
    ("1100", "Accounts Receivable", "asset", "debit"),
    ("1200", "Employee Loans Receivable", "asset", "debit"),
    ("2000", "Accounts Payable", "liability", "credit"),
    ("2100", "PAYE Payable", "liability", "credit"),
    ("2110", "NSSF Payable", "liability", "credit"),
    ("2120", "SHIF Payable", "liability", "credit"),
    ("2130", "Affordable Housing Levy Payable", "liability", "credit"),
    ("2140", "NITA Payable", "liability", "credit"),
    ("2200", "Net Pay Payable", "liability", "credit"),
    ("2300", "Employee Benefits Payable", "liability", "credit"),
    ("3000", "Retained Earnings", "equity", "credit"),
    ("4000", "Revenue", "revenue", "credit"),
    ("4100", "Interest Income", "revenue", "credit"),
    ("5100", "Salaries and Wages", "expense", "debit"),
    ("5200", "Employer Statutory Contributions", "expense", "debit"),
    ("5300", "General Expenses", "expense", "debit"),
    ("5400", "Employee Benefits Expense", "expense", "debit"),
)

SYSTEM_CODES = {
    "cash": "1000",
    "loan_receivable": "1200",
    "salary_expense": "5100",
    "employer_expense": "5200",
    "paye_payable": "2100",
    "nssf_payable": "2110",
    "shif_payable": "2120",
    "housing_payable": "2130",
    "nita_payable": "2140",
    "net_pay_payable": "2200",
    "benefit_payable": "2300",
    "interest_income": "4100",
    "benefit_expense": "5400",
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
    loan_principal_total = _money(
        sum((result.loan_principal for result in results), Decimal("0.00"))
    )
    loan_interest_total = _money(sum((result.loan_interest for result in results), Decimal("0.00")))
    loan_total = _money(loan_principal_total + loan_interest_total)
    benefit_employee_total = _money(
        sum((result.benefit_employee_deduction for result in results), Decimal("0.00"))
    )
    benefit_employer_total = _money(
        sum((result.benefit_employer_contribution for result in results), Decimal("0.00"))
    )
    if benefit_employee_total != _money(run.benefit_employee_total):
        raise IntegrityError("Payroll benefit employee totals do not match the run snapshot.")
    if benefit_employer_total != _money(run.benefit_employer_total):
        raise IntegrityError("Payroll benefit employer totals do not match the run snapshot.")
    benefit_total = _money(benefit_employee_total + benefit_employer_total)
    debit_total = _money(run.gross_total + employer_total + benefit_employer_total)
    credit_total = _money(
        run.paye_total
        + nssf_total
        + shif_total
        + housing_total
        + nita_total
        + benefit_total
        + run.net_total
        + loan_total
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
        (
            SYSTEM_CODES["benefit_expense"],
            "Employer benefit contributions",
            benefit_employer_total,
        ),
    ]
    credit_lines = [
        (SYSTEM_CODES["paye_payable"], "PAYE payable", run.paye_total),
        (SYSTEM_CODES["nssf_payable"], "NSSF payable", nssf_total),
        (SYSTEM_CODES["shif_payable"], "SHIF payable", shif_total),
        (SYSTEM_CODES["housing_payable"], "Affordable Housing Levy payable", housing_total),
        (SYSTEM_CODES["nita_payable"], "NITA payable", nita_total),
        (
            SYSTEM_CODES["benefit_payable"],
            "Employee benefits payable",
            benefit_total,
        ),
        (
            SYSTEM_CODES["loan_receivable"],
            "Employee loan principal recovered",
            loan_principal_total,
        ),
        (SYSTEM_CODES["interest_income"], "Employee loan interest income", loan_interest_total),
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


async def post_loan_disbursement(
    session: AsyncSession, context: AuthContext, loan: EmployeeLoan
) -> Journal:
    """Record the cash disbursement of an approved loan exactly once."""

    existing = await session.scalar(
        select(Journal).where(
            Journal.company_id == loan.company_id,
            Journal.source_type == "employee_loan",
            Journal.source_id == loan.id,
        )
    )
    if existing is not None:
        return existing

    if loan.disbursed_on is None:
        raise IntegrityError("The loan has no disbursement date and cannot be posted.")
    if loan.principal_amount <= 0:
        raise IntegrityError("The loan principal must be positive before disbursement.")

    company = await session.get(Company, loan.company_id)
    if company is None:
        raise NotFoundError("Company not found.")
    await ensure_default_chart(session, company)
    period = await session.scalar(
        select(FiscalPeriod).where(
            FiscalPeriod.company_id == company.id,
            FiscalPeriod.start_date <= loan.disbursed_on,
            FiscalPeriod.end_date >= loan.disbursed_on,
        )
    )
    if period is None:
        raise ConfigurationError(
            "No fiscal period covers the loan disbursement date. Create the fiscal calendar first."
        )
    if period.status != "open":
        raise ConflictError("The loan disbursement date falls in a locked or closed fiscal period.")

    accounts = {
        account.code: account
        for account in (
            await session.scalars(
                select(Account).where(
                    Account.company_id == company.id,
                    Account.code.in_((SYSTEM_CODES["cash"], SYSTEM_CODES["loan_receivable"])),
                    Account.is_active.is_(True),
                )
            )
        ).all()
    }
    missing = sorted({SYSTEM_CODES["cash"], SYSTEM_CODES["loan_receivable"]} - set(accounts))
    if missing:
        raise ConfigurationError(
            "The loan disbursement account mapping is incomplete.",
            details={"missing_account_codes": missing},
        )

    amount = _money(loan.principal_amount)
    journal = Journal(
        organization_id=loan.organization_id,
        company_id=loan.company_id,
        journal_number=await _next_journal_number(session, loan.company_id, loan.disbursed_on),
        journal_date=loan.disbursed_on,
        description=f"Disbursement of employee {loan.kind} {loan.loan_number}",
        reference=loan.loan_number,
        status="posted",
        currency=loan.currency,
        total_debit=amount,
        total_credit=amount,
        source_type="employee_loan",
        source_id=loan.id,
        created_by=context.user.id,
        posted_by=context.user.id,
        posted_at=datetime.now(UTC),
    )
    session.add(journal)
    await session.flush()
    session.add_all(
        [
            JournalLine(
                journal_id=journal.id,
                line_number=1,
                account_id=accounts[SYSTEM_CODES["loan_receivable"]].id,
                description=f"Principal receivable from {loan.loan_number}",
                debit=amount,
                credit=Decimal("0.00"),
            ),
            JournalLine(
                journal_id=journal.id,
                line_number=2,
                account_id=accounts[SYSTEM_CODES["cash"]].id,
                description=f"Cash disbursed for {loan.loan_number}",
                debit=Decimal("0.00"),
                credit=amount,
            ),
        ]
    )
    await session.flush()
    await record_audit(
        session,
        organization_id=loan.organization_id,
        actor_user_id=context.user.id,
        action="employee_loan.disbursed",
        entity_type="employee_loan",
        entity_id=loan.id,
        metadata={
            "loan_number": loan.loan_number,
            "journal_id": str(journal.id),
            "journal_number": journal.journal_number,
            "amount": str(amount),
        },
    )
    return journal


async def post_expense_reimbursement(
    session: AsyncSession,
    context: AuthContext,
    claim: ExpenseClaim,
    category: ExpenseCategory,
    paid_on: date,
    reference: str,
) -> Journal:
    """Record the cash reimbursement of an approved expense claim exactly once."""

    existing = await session.scalar(
        select(Journal).where(
            Journal.company_id == claim.company_id,
            Journal.source_type == "expense_claim",
            Journal.source_id == claim.id,
        )
    )
    if existing is not None:
        return existing

    if claim.amount <= 0:
        raise IntegrityError("The expense claim amount must be positive before reimbursement.")
    company = await session.get(Company, claim.company_id)
    if company is None:
        raise NotFoundError("Company not found.")
    await ensure_default_chart(session, company)
    period = await session.scalar(
        select(FiscalPeriod).where(
            FiscalPeriod.company_id == company.id,
            FiscalPeriod.start_date <= paid_on,
            FiscalPeriod.end_date >= paid_on,
        )
    )
    if period is None:
        raise ConfigurationError(
            "No fiscal period covers the reimbursement date. Create the fiscal calendar first."
        )
    if period.status != "open":
        raise ConflictError("The reimbursement date falls in a locked or closed fiscal period.")

    cash = await session.scalar(
        select(Account).where(
            Account.company_id == company.id,
            Account.code == SYSTEM_CODES["cash"],
            Account.is_active.is_(True),
        )
    )
    if cash is None:
        raise ConfigurationError(
            "The cash account for expense reimbursement is missing or inactive.",
            details={"account_code": SYSTEM_CODES["cash"]},
        )
    expense_account = await session.scalar(
        select(Account).where(
            Account.id == category.expense_account_id,
            Account.company_id == company.id,
            Account.account_type == "expense",
            Account.is_active.is_(True),
        )
    )
    if expense_account is None:
        raise ConfigurationError(
            "The expense account mapped to this category is missing, inactive or not an "
            "expense account."
        )

    amount = _money(claim.amount)
    journal = Journal(
        organization_id=claim.organization_id,
        company_id=claim.company_id,
        journal_number=await _next_journal_number(session, claim.company_id, paid_on),
        journal_date=paid_on,
        description=f"Reimbursement of expense claim {claim.claim_number}",
        reference=reference,
        status="posted",
        currency=claim.currency,
        total_debit=amount,
        total_credit=amount,
        source_type="expense_claim",
        source_id=claim.id,
        created_by=context.user.id,
        posted_by=context.user.id,
        posted_at=datetime.now(UTC),
    )
    session.add(journal)
    await session.flush()
    session.add_all(
        [
            JournalLine(
                journal_id=journal.id,
                line_number=1,
                account_id=expense_account.id,
                description=f"{category.name} expense for {claim.claim_number}",
                debit=amount,
                credit=Decimal("0.00"),
            ),
            JournalLine(
                journal_id=journal.id,
                line_number=2,
                account_id=cash.id,
                description=f"Cash reimbursed for {claim.claim_number}",
                debit=Decimal("0.00"),
                credit=amount,
            ),
        ]
    )
    await session.flush()
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


async def _open_period_for(
    session: AsyncSession, company_id: uuid.UUID, on_date: date, label: str
) -> FiscalPeriod:
    period = await session.scalar(
        select(FiscalPeriod).where(
            FiscalPeriod.company_id == company_id,
            FiscalPeriod.start_date <= on_date,
            FiscalPeriod.end_date >= on_date,
        )
    )
    if period is None:
        raise ConfigurationError(
            f"No fiscal period covers the {label} date. Create the fiscal calendar first."
        )
    if period.status != "open":
        raise ConflictError(f"The {label} date falls in a locked or closed fiscal period.")
    return period


async def _asset_account(
    session: AsyncSession,
    company_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    label: str,
    allowed_types: tuple[str, ...],
) -> Account:
    account = await session.get(Account, account_id)
    if account is None or account.company_id != company_id:
        raise ConfigurationError(f"The {label} for this asset is missing or belongs elsewhere.")
    if not account.is_active:
        raise ConfigurationError(f"The {label} for this asset is inactive.")
    if account.account_type not in allowed_types:
        raise ConfigurationError(f"The {label} has an invalid account type.")
    return account


async def post_asset_capitalization(
    session: AsyncSession,
    context: AuthContext,
    asset: FixedAsset,
    *,
    funding_account_id: uuid.UUID,
    reference: str,
) -> Journal:
    """Post the capitalization of an approved asset exactly once."""

    existing = await session.scalar(
        select(Journal).where(
            Journal.company_id == asset.company_id,
            Journal.source_type == "asset_capitalization",
            Journal.source_id == asset.id,
        )
    )
    if existing is not None:
        return existing

    funding = await session.get(Account, funding_account_id)
    if funding is None or funding.company_id != asset.company_id:
        raise ValidationFailureError("The funding account does not belong to this company.")
    if not funding.is_active:
        raise ValidationFailureError("The funding account must be active.")
    if funding.account_type not in ("asset", "liability", "equity"):
        raise ValidationFailureError(
            "The funding account must be an asset, liability or equity account."
        )
    cost_account = await _asset_account(
        session,
        asset.company_id,
        asset.cost_account_id,
        label="asset cost account",
        allowed_types=("asset",),
    )
    await _open_period_for(session, asset.company_id, asset.acquisition_date, "capitalization")

    amount = _money(asset.cost)
    if amount <= 0:
        raise IntegrityError("The asset cost must be positive before capitalization.")
    journal = Journal(
        organization_id=asset.organization_id,
        company_id=asset.company_id,
        journal_number=await _next_journal_number(
            session, asset.company_id, asset.acquisition_date
        ),
        journal_date=asset.acquisition_date,
        description=f"Capitalization of fixed asset {asset.asset_number}",
        reference=reference.strip(),
        status="posted",
        currency=asset.currency,
        total_debit=amount,
        total_credit=amount,
        source_type="asset_capitalization",
        source_id=asset.id,
        created_by=context.user.id,
        posted_by=context.user.id,
        posted_at=datetime.now(UTC),
    )
    session.add(journal)
    await session.flush()
    session.add_all(
        [
            JournalLine(
                journal_id=journal.id,
                line_number=1,
                account_id=cost_account.id,
                description=f"Cost of {asset.asset_number}",
                debit=amount,
                credit=Decimal("0.00"),
            ),
            JournalLine(
                journal_id=journal.id,
                line_number=2,
                account_id=funding.id,
                description=f"Funding of {asset.asset_number}",
                debit=Decimal("0.00"),
                credit=amount,
            ),
        ]
    )
    await session.flush()
    return journal


async def post_asset_depreciation(
    session: AsyncSession,
    context: AuthContext,
    asset: FixedAsset,
    depreciation: AssetDepreciation,
) -> Journal:
    """Post one depreciation period exactly once."""

    existing = await session.scalar(
        select(Journal).where(
            Journal.company_id == asset.company_id,
            Journal.source_type == "asset_depreciation",
            Journal.source_id == depreciation.id,
        )
    )
    if existing is not None:
        return existing

    expense_account = await _asset_account(
        session,
        asset.company_id,
        asset.depreciation_expense_account_id,
        label="depreciation expense account",
        allowed_types=("expense",),
    )
    accumulated_account = await _asset_account(
        session,
        asset.company_id,
        asset.accumulated_depreciation_account_id,
        label="accumulated depreciation account",
        allowed_types=("asset",),
    )
    await _open_period_for(session, asset.company_id, depreciation.period_end, "depreciation")

    amount = _money(depreciation.amount)
    if amount <= 0:
        raise IntegrityError("The depreciation amount must be positive before posting.")
    journal = Journal(
        organization_id=asset.organization_id,
        company_id=asset.company_id,
        journal_number=await _next_journal_number(
            session, asset.company_id, depreciation.period_end
        ),
        journal_date=depreciation.period_end,
        description=(
            f"Depreciation of {asset.asset_number} for "
            f"{depreciation.period_start:%Y-%m}"
        ),
        reference=depreciation.reference,
        status="posted",
        currency=asset.currency,
        total_debit=amount,
        total_credit=amount,
        source_type="asset_depreciation",
        source_id=depreciation.id,
        created_by=context.user.id,
        posted_by=context.user.id,
        posted_at=datetime.now(UTC),
    )
    session.add(journal)
    await session.flush()
    session.add_all(
        [
            JournalLine(
                journal_id=journal.id,
                line_number=1,
                account_id=expense_account.id,
                description=f"Depreciation expense for {asset.asset_number}",
                debit=amount,
                credit=Decimal("0.00"),
            ),
            JournalLine(
                journal_id=journal.id,
                line_number=2,
                account_id=accumulated_account.id,
                description=f"Accumulated depreciation for {asset.asset_number}",
                debit=Decimal("0.00"),
                credit=amount,
            ),
        ]
    )
    await session.flush()
    return journal


async def post_asset_disposal(
    session: AsyncSession,
    context: AuthContext,
    asset: FixedAsset,
    disposal: AssetDisposal,
) -> Journal:
    """Post the disposal of an asset exactly once, including gain or loss."""

    existing = await session.scalar(
        select(Journal).where(
            Journal.company_id == asset.company_id,
            Journal.source_type == "asset_disposal",
            Journal.source_id == asset.id,
        )
    )
    if existing is not None:
        return existing

    proceeds_account = await _asset_account(
        session,
        asset.company_id,
        disposal.proceeds_account_id,
        label="disposal proceeds account",
        allowed_types=("asset", "revenue"),
    )
    accumulated_account = await _asset_account(
        session,
        asset.company_id,
        asset.accumulated_depreciation_account_id,
        label="accumulated depreciation account",
        allowed_types=("asset",),
    )
    cost_account = await _asset_account(
        session,
        asset.company_id,
        asset.cost_account_id,
        label="asset cost account",
        allowed_types=("asset",),
    )
    gain_loss_account = await _asset_account(
        session,
        asset.company_id,
        asset.gain_loss_account_id,
        label="disposal gain/loss account",
        allowed_types=("expense", "revenue"),
    )
    await _open_period_for(session, asset.company_id, disposal.disposal_date, "disposal")

    total_depreciation = _money(
        Decimal(
            str(
                await session.scalar(
                    select(func.coalesce(func.sum(AssetDepreciation.amount), 0)).where(
                        AssetDepreciation.asset_id == asset.id
                    )
                )
                or 0
            )
        )
    )
    cost = _money(asset.cost)
    proceeds = _money(disposal.proceeds_amount)
    book_value = _money(cost - total_depreciation)
    gain_loss = _money(proceeds - book_value)

    lines: list[tuple[int, uuid.UUID, str, Decimal, Decimal]] = []
    sequence = 1
    if proceeds > 0:
        lines.append(
            (
                sequence,
                proceeds_account.id,
                f"Proceeds from disposal of {asset.asset_number}",
                proceeds,
                Decimal("0.00"),
            )
        )
        sequence += 1
    if total_depreciation > 0:
        lines.append(
            (
                sequence,
                accumulated_account.id,
                f"Clear accumulated depreciation of {asset.asset_number}",
                total_depreciation,
                Decimal("0.00"),
            )
        )
        sequence += 1
    if gain_loss > 0:
        lines.append(
            (
                sequence,
                gain_loss_account.id,
                f"Gain on disposal of {asset.asset_number}",
                Decimal("0.00"),
                gain_loss,
            )
        )
        sequence += 1
    elif gain_loss < 0:
        lines.append(
            (
                sequence,
                gain_loss_account.id,
                f"Loss on disposal of {asset.asset_number}",
                -gain_loss,
                Decimal("0.00"),
            )
        )
        sequence += 1
    lines.append(
        (
            sequence,
            cost_account.id,
            f"Remove cost of {asset.asset_number}",
            Decimal("0.00"),
            cost,
        )
    )

    debit_total = _money(sum((line[3] for line in lines), Decimal("0.00")))
    credit_total = _money(sum((line[4] for line in lines), Decimal("0.00")))
    if debit_total != credit_total or debit_total <= 0:
        raise IntegrityError("The asset disposal journal is not balanced.")

    journal = Journal(
        organization_id=asset.organization_id,
        company_id=asset.company_id,
        journal_number=await _next_journal_number(
            session, asset.company_id, disposal.disposal_date
        ),
        journal_date=disposal.disposal_date,
        description=f"Disposal of fixed asset {asset.asset_number}",
        reference=f"DISP-{asset.asset_number}",
        status="posted",
        currency=asset.currency,
        total_debit=debit_total,
        total_credit=credit_total,
        source_type="asset_disposal",
        source_id=asset.id,
        created_by=context.user.id,
        posted_by=context.user.id,
        posted_at=datetime.now(UTC),
    )
    session.add(journal)
    await session.flush()
    session.add_all(
        [
            JournalLine(
                journal_id=journal.id,
                line_number=line_number,
                account_id=account_id,
                description=description,
                debit=debit,
                credit=credit,
            )
            for line_number, account_id, description, debit, credit in lines
        ]
    )
    await session.flush()
    return journal
