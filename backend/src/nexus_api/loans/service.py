"""Employee loan application, approval, disbursement and repayment services."""

from __future__ import annotations

import calendar
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.api.deps import PaginationParams
from nexus_api.audit.service import record_audit
from nexus_api.core.errors import (
    ConflictError,
    IntegrityError,
    NotFoundError,
    ValidationFailureError,
)
from nexus_api.employees.models import Employee
from nexus_api.identity.models import User
from nexus_api.identity.permissions import PermissionCode
from nexus_api.identity.service import AuthContext, validate_company_access
from nexus_api.loans.models import EmployeeLoan, LoanInstallment
from nexus_api.loans.schemas import (
    LoanApplicationCreate,
    LoanDecision,
    LoanDetailResponse,
    LoanDisbursement,
    LoanInstallmentResponse,
    LoanKind,
    LoanPage,
    LoanResponse,
    LoanStatus,
)
from nexus_api.organizations.models import Company
from nexus_api.payroll.models import PayrollRun

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class ScheduledInstallment:
    sequence: int
    due_date: date
    principal_due: Decimal
    interest_due: Decimal
    amount_due: Decimal


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def build_installment_schedule(
    principal: Decimal,
    interest_rate_annual: Decimal,
    term_months: int,
    first_due_date: date,
) -> list[ScheduledInstallment]:
    """Build a reducing-balance schedule with the final instalment absorbing rounding."""

    if principal <= 0:
        raise ValueError("The loan principal must be positive.")
    if term_months < 1:
        raise ValueError("The loan term must include at least one month.")
    if interest_rate_annual < 0:
        raise ValueError("The interest rate cannot be negative.")

    principal = _money(principal)
    monthly_rate = interest_rate_annual / Decimal("1200")
    instalments: list[ScheduledInstallment] = []
    remaining = principal

    if monthly_rate == 0:
        regular_principal = _money(principal / Decimal(term_months))
        if regular_principal <= 0:
            raise ValueError("The monthly instalment would be zero.")
        for sequence in range(1, term_months + 1):
            due_principal = remaining if sequence == term_months else regular_principal
            if sequence < term_months and remaining - due_principal < 0:
                due_principal = remaining
            instalments.append(
                ScheduledInstallment(
                    sequence=sequence,
                    due_date=_add_months(first_due_date, sequence - 1),
                    principal_due=due_principal,
                    interest_due=ZERO,
                    amount_due=due_principal,
                )
            )
            remaining = _money(remaining - due_principal)
    else:
        factor = (Decimal("1") + monthly_rate) ** term_months
        payment = _money(principal * monthly_rate * factor / (factor - Decimal("1")))
        if payment <= 0:
            raise ValueError("The monthly instalment would be zero.")
        for sequence in range(1, term_months + 1):
            interest = _money(remaining * monthly_rate)
            due_principal = remaining if sequence == term_months else _money(payment - interest)
            if due_principal <= 0:
                raise ValueError("The loan term and interest rate produce an invalid schedule.")
            if due_principal > remaining:
                due_principal = remaining
            instalments.append(
                ScheduledInstallment(
                    sequence=sequence,
                    due_date=_add_months(first_due_date, sequence - 1),
                    principal_due=due_principal,
                    interest_due=interest,
                    amount_due=_money(due_principal + interest),
                )
            )
            remaining = _money(remaining - due_principal)
            if remaining == 0 and sequence < term_months:
                raise ValueError("The loan schedule repaid the principal before the final month.")

    if remaining != 0:
        raise IntegrityError("The loan schedule does not repay the principal exactly.")
    return instalments


def _employee_name(employee: Employee) -> str:
    return " ".join(
        part for part in (employee.first_name, employee.middle_name, employee.last_name) if part
    )


async def _user_names(session: AsyncSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not user_ids:
        return {}
    rows = await session.execute(select(User.id, User.full_name).where(User.id.in_(user_ids)))
    return {user_id: name for user_id, name in rows.all()}


def _loan_response(
    loan: EmployeeLoan,
    employee: Employee,
    installments: list[LoanInstallment],
    user_names: dict[uuid.UUID, str],
) -> LoanResponse:
    if installments:
        total_repayable = _money(sum((item.amount_due for item in installments), ZERO))
        outstanding_principal = _money(
            sum(
                (item.principal_due for item in installments if item.status != "deducted"),
                ZERO,
            )
        )
    else:
        total_repayable = _money(loan.principal_amount)
        outstanding_principal = _money(loan.principal_amount)
    name = _employee_name(employee)
    return LoanResponse(
        id=loan.id,
        organization_id=loan.organization_id,
        company_id=loan.company_id,
        employee_id=loan.employee_id,
        employee_number=employee.employee_number,
        employee_name=name,
        loan_number=loan.loan_number,
        kind=cast(LoanKind, loan.kind),
        principal_amount=loan.principal_amount,
        currency=loan.currency,
        interest_rate_annual=loan.interest_rate_annual,
        term_months=loan.term_months,
        purpose=loan.purpose,
        status=cast(LoanStatus, loan.status),
        requested_by=loan.requested_by,
        requested_by_name=user_names.get(loan.requested_by) if loan.requested_by else None,
        decided_by=loan.decided_by,
        decided_by_name=user_names.get(loan.decided_by) if loan.decided_by else None,
        decided_at=loan.decided_at,
        decision_note=loan.decision_note,
        cancelled_by=loan.cancelled_by,
        cancelled_at=loan.cancelled_at,
        disbursed_by=loan.disbursed_by,
        disbursed_on=loan.disbursed_on,
        disbursed_at=loan.disbursed_at,
        disbursement_reference=loan.disbursement_reference,
        first_due_date=loan.first_due_date,
        journal_id=loan.journal_id,
        closed_at=loan.closed_at,
        total_repayable=total_repayable,
        outstanding_principal=outstanding_principal,
        installments_paid=sum(1 for item in installments if item.status == "deducted"),
        installments_total=len(installments),
        created_at=loan.created_at,
        version=loan.version,
    )


async def _detail_payload(session: AsyncSession, loan: EmployeeLoan) -> LoanDetailResponse:
    employee = await session.get(Employee, loan.employee_id)
    if employee is None or employee.company_id != loan.company_id:
        raise IntegrityError("The employee linked to this loan could not be loaded.")
    installments = list(
        (
            await session.scalars(
                select(LoanInstallment)
                .where(LoanInstallment.loan_id == loan.id)
                .order_by(LoanInstallment.sequence)
            )
        ).all()
    )
    user_names = await _user_names(
        session,
        {
            user_id
            for user_id in (
                loan.requested_by,
                loan.decided_by,
                loan.disbursed_by,
                loan.cancelled_by,
            )
            if user_id is not None
        },
    )
    return LoanDetailResponse(
        loan=_loan_response(loan, employee, installments, user_names),
        installments=[LoanInstallmentResponse.model_validate(item) for item in installments],
    )


async def _next_loan_number(
    session: AsyncSession, company_id: uuid.UUID, kind: str, year: int
) -> str:
    await session.execute(select(Company.id).where(Company.id == company_id).with_for_update())
    prefix = f"{'ADV' if kind == 'advance' else 'LN'}-{year}-"
    count = await session.scalar(
        select(func.count(EmployeeLoan.id)).where(
            EmployeeLoan.company_id == company_id,
            EmployeeLoan.loan_number.like(f"{prefix}%"),
        )
    )
    return f"{prefix}{(count or 0) + 1:04d}"


async def _locked_loan(
    session: AsyncSession,
    context: AuthContext,
    loan_id: uuid.UUID,
    permission: PermissionCode,
) -> EmployeeLoan:
    loan = await session.scalar(
        select(EmployeeLoan)
        .where(
            EmployeeLoan.id == loan_id,
            EmployeeLoan.organization_id == context.organization.id,
        )
        .with_for_update()
    )
    if loan is None:
        raise NotFoundError("Employee loan not found.")
    await validate_company_access(session, context, loan.company_id, permission)
    return loan


def _check_version(loan: EmployeeLoan, version: int | None) -> None:
    if version is not None and version != loan.version:
        raise ConflictError("This loan was changed by another user. Reload it and try again.")


async def create_loan(
    session: AsyncSession, context: AuthContext, payload: LoanApplicationCreate
) -> LoanResponse:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.LOAN_REQUEST
    )
    employee = await session.scalar(
        select(Employee).where(
            Employee.id == payload.employee_id,
            Employee.company_id == company.id,
            Employee.organization_id == context.organization.id,
        )
    )
    if employee is None:
        raise NotFoundError("Employee not found for this company.")
    if employee.status not in ("active", "probation"):
        raise ConflictError("Loans can only be requested for active employees.")
    if not employee.is_payroll_eligible:
        raise ConflictError("Loans can only be requested for payroll-eligible employees.")

    loan = EmployeeLoan(
        organization_id=context.organization.id,
        company_id=company.id,
        employee_id=employee.id,
        loan_number=await _next_loan_number(session, company.id, payload.kind, date.today().year),
        kind=payload.kind,
        principal_amount=_money(payload.principal_amount),
        currency=company.base_currency,
        interest_rate_annual=payload.interest_rate_annual,
        term_months=payload.term_months,
        purpose=payload.purpose.strip(),
        status="pending",
        requested_by=context.user.id,
    )
    session.add(loan)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="employee_loan.created",
        entity_type="employee_loan",
        entity_id=loan.id,
        metadata={
            "loan_number": loan.loan_number,
            "company_id": str(company.id),
            "employee_id": str(employee.id),
            "kind": loan.kind,
            "principal_amount": str(loan.principal_amount),
            "term_months": loan.term_months,
        },
    )
    detail = await _detail_payload(session, loan)
    return detail.loan


async def list_loans(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    pagination: PaginationParams,
    employee_id: uuid.UUID | None = None,
    status: str | None = None,
    kind: str | None = None,
) -> LoanPage:
    await validate_company_access(session, context, company_id, PermissionCode.LOAN_VIEW)
    filters = [EmployeeLoan.company_id == company_id]
    if employee_id is not None:
        filters.append(EmployeeLoan.employee_id == employee_id)
    if status:
        filters.append(EmployeeLoan.status == status)
    if kind:
        filters.append(EmployeeLoan.kind == kind)
    total = await session.scalar(select(func.count(EmployeeLoan.id)).where(*filters))
    loans = list(
        (
            await session.scalars(
                select(EmployeeLoan)
                .where(*filters)
                .order_by(EmployeeLoan.created_at.desc(), EmployeeLoan.loan_number.desc())
                .offset(pagination.offset)
                .limit(pagination.page_size)
            )
        ).all()
    )
    if not loans:
        return LoanPage(
            items=[], total=total or 0, page=pagination.page, page_size=pagination.page_size
        )

    loan_ids = [loan.id for loan in loans]
    employee_ids = {loan.employee_id for loan in loans}
    employees = {
        employee.id: employee
        for employee in (
            await session.scalars(select(Employee).where(Employee.id.in_(employee_ids)))
        ).all()
    }
    installments: dict[uuid.UUID, list[LoanInstallment]] = defaultdict(list)
    for item in (
        await session.scalars(
            select(LoanInstallment)
            .where(LoanInstallment.loan_id.in_(loan_ids))
            .order_by(LoanInstallment.loan_id, LoanInstallment.sequence)
        )
    ).all():
        installments[item.loan_id].append(item)
    user_ids = {
        user_id
        for loan in loans
        for user_id in (loan.requested_by, loan.decided_by, loan.disbursed_by, loan.cancelled_by)
        if user_id is not None
    }
    user_names = await _user_names(session, user_ids)
    items = [
        _loan_response(
            loan,
            employees[loan.employee_id],
            installments[loan.id],
            user_names,
        )
        for loan in loans
    ]
    return LoanPage(
        items=items,
        total=total or 0,
        page=pagination.page,
        page_size=pagination.page_size,
    )


async def get_loan(
    session: AsyncSession, context: AuthContext, loan_id: uuid.UUID
) -> LoanDetailResponse:
    loan = await session.scalar(
        select(EmployeeLoan).where(
            EmployeeLoan.id == loan_id,
            EmployeeLoan.organization_id == context.organization.id,
        )
    )
    if loan is None:
        raise NotFoundError("Employee loan not found.")
    await validate_company_access(session, context, loan.company_id, PermissionCode.LOAN_VIEW)
    return await _detail_payload(session, loan)


async def approve_loan(
    session: AsyncSession,
    context: AuthContext,
    loan_id: uuid.UUID,
    payload: LoanDecision,
) -> LoanDetailResponse:
    loan = await _locked_loan(session, context, loan_id, PermissionCode.LOAN_APPROVE)
    _check_version(loan, payload.version)
    if loan.status != "pending":
        raise ConflictError("Only a pending loan application can be approved.")
    if loan.requested_by == context.user.id:
        raise ConflictError("Separation of duties: the requester cannot approve a loan.")
    loan.decided_by = context.user.id
    loan.decided_at = datetime.now(UTC)
    loan.decision_note = payload.note.strip() if payload.note else None
    loan.status = "approved"
    await session.flush()
    await record_audit(
        session,
        organization_id=loan.organization_id,
        actor_user_id=context.user.id,
        action="employee_loan.approved",
        entity_type="employee_loan",
        entity_id=loan.id,
        metadata={"loan_number": loan.loan_number, "principal": str(loan.principal_amount)},
    )
    return await _detail_payload(session, loan)


async def reject_loan(
    session: AsyncSession,
    context: AuthContext,
    loan_id: uuid.UUID,
    payload: LoanDecision,
) -> LoanDetailResponse:
    loan = await _locked_loan(session, context, loan_id, PermissionCode.LOAN_APPROVE)
    _check_version(loan, payload.version)
    if loan.status != "pending":
        raise ConflictError("Only a pending loan application can be rejected.")
    if loan.requested_by == context.user.id:
        raise ConflictError("Separation of duties: the requester cannot reject a loan.")
    note = payload.note.strip() if payload.note else ""
    if not note:
        raise ValidationFailureError("A rejection note is required.")
    loan.decided_by = context.user.id
    loan.decided_at = datetime.now(UTC)
    loan.decision_note = note
    loan.status = "rejected"
    await session.flush()
    await record_audit(
        session,
        organization_id=loan.organization_id,
        actor_user_id=context.user.id,
        action="employee_loan.rejected",
        entity_type="employee_loan",
        entity_id=loan.id,
        metadata={"loan_number": loan.loan_number, "note": note},
    )
    return await _detail_payload(session, loan)


async def cancel_loan(
    session: AsyncSession,
    context: AuthContext,
    loan_id: uuid.UUID,
    payload: LoanDecision,
) -> LoanDetailResponse:
    loan = await _locked_loan(session, context, loan_id, PermissionCode.LOAN_REQUEST)
    _check_version(loan, payload.version)
    if loan.status not in ("pending", "approved"):
        raise ConflictError("Only a pending or approved loan can be cancelled.")
    if loan.requested_by != context.user.id and not context.has_permission(
        PermissionCode.LOAN_APPROVE, company_id=loan.company_id
    ):
        raise ConflictError("Only the requester or an approver can cancel this loan.")
    note = payload.note.strip() if payload.note else None
    loan.cancelled_by = context.user.id
    loan.cancelled_at = datetime.now(UTC)
    loan.decision_note = note or loan.decision_note
    loan.status = "cancelled"
    await session.flush()
    await record_audit(
        session,
        organization_id=loan.organization_id,
        actor_user_id=context.user.id,
        action="employee_loan.cancelled",
        entity_type="employee_loan",
        entity_id=loan.id,
        metadata={"loan_number": loan.loan_number, "note": note or ""},
    )
    return await _detail_payload(session, loan)


async def disburse_loan(
    session: AsyncSession,
    context: AuthContext,
    loan_id: uuid.UUID,
    payload: LoanDisbursement,
) -> LoanDetailResponse:
    from nexus_api.accounting.service import post_loan_disbursement

    loan = await _locked_loan(session, context, loan_id, PermissionCode.LOAN_DISBURSE)
    _check_version(loan, payload.version)
    if loan.status != "approved":
        raise ConflictError("Only an approved loan can be disbursed.")
    existing = await session.scalar(
        select(func.count(LoanInstallment.id)).where(LoanInstallment.loan_id == loan.id)
    )
    if existing:
        raise ConflictError("This loan already has an instalment schedule.")

    schedule = build_installment_schedule(
        loan.principal_amount,
        loan.interest_rate_annual,
        loan.term_months,
        payload.first_due_date,
    )
    loan.disbursed_by = context.user.id
    loan.disbursed_on = payload.disbursed_on
    loan.disbursed_at = datetime.now(UTC)
    loan.disbursement_reference = payload.reference.strip()
    loan.first_due_date = payload.first_due_date
    loan.status = "disbursed"
    schedule_rows = [
        LoanInstallment(
            organization_id=loan.organization_id,
            company_id=loan.company_id,
            loan_id=loan.id,
            sequence=item.sequence,
            due_date=item.due_date,
            principal_due=item.principal_due,
            interest_due=item.interest_due,
            amount_due=item.amount_due,
            status="scheduled",
        )
        for item in schedule
    ]
    session.add_all(schedule_rows)
    journal = await post_loan_disbursement(session, context, loan)
    loan.journal_id = journal.id
    await session.flush()
    await record_audit(
        session,
        organization_id=loan.organization_id,
        actor_user_id=context.user.id,
        action="employee_loan.schedule_created",
        entity_type="employee_loan",
        entity_id=loan.id,
        metadata={
            "loan_number": loan.loan_number,
            "instalments": len(schedule_rows),
            "first_due_date": payload.first_due_date.isoformat(),
            "journal_id": str(journal.id),
        },
    )
    return await _detail_payload(session, loan)


async def release_payroll_installments(
    session: AsyncSession, run_id: uuid.UUID, company_id: uuid.UUID
) -> None:
    """Return instalments reserved by a draft run so it can be recalculated."""

    await session.execute(
        update(LoanInstallment)
        .where(
            LoanInstallment.payroll_run_id == run_id,
            LoanInstallment.company_id == company_id,
            LoanInstallment.status == "reserved",
        )
        .values(status="scheduled", payroll_run_id=None, payroll_result_id=None)
    )


async def finalize_payroll_installments(
    session: AsyncSession, context: AuthContext, run: PayrollRun
) -> None:
    """Mark reserved instalments as deducted and close fully repaid loans."""

    installments = list(
        (
            await session.scalars(
                select(LoanInstallment)
                .where(
                    LoanInstallment.payroll_run_id == run.id,
                    LoanInstallment.company_id == run.company_id,
                    LoanInstallment.status == "reserved",
                )
                .with_for_update()
            )
        ).all()
    )
    if not installments:
        return

    now = datetime.now(UTC)
    totals: dict[uuid.UUID, tuple[Decimal, Decimal]] = defaultdict(lambda: (ZERO, ZERO))
    for item in installments:
        item.status = "deducted"
        item.deducted_at = now
        principal, interest = totals[item.loan_id]
        totals[item.loan_id] = (
            _money(principal + item.principal_due),
            _money(interest + item.interest_due),
        )

    loans = {
        loan.id: loan
        for loan in (
            await session.scalars(
                select(EmployeeLoan).where(EmployeeLoan.id.in_(totals)).with_for_update()
            )
        ).all()
    }
    for loan_id, (principal, interest) in totals.items():
        loan = loans.get(loan_id)
        if loan is None:
            raise IntegrityError("A loan instalment refers to a missing loan.")
        await record_audit(
            session,
            organization_id=loan.organization_id,
            actor_user_id=context.user.id,
            action="employee_loan.repayment_posted",
            entity_type="employee_loan",
            entity_id=loan.id,
            metadata={
                "loan_number": loan.loan_number,
                "payroll_run_id": str(run.id),
                "run_number": run.run_number,
                "principal": str(principal),
                "interest": str(interest),
            },
        )
        remaining = await session.scalar(
            select(func.count(LoanInstallment.id)).where(
                LoanInstallment.loan_id == loan.id,
                LoanInstallment.status != "deducted",
            )
        )
        if not remaining and loan.status == "disbursed":
            loan.status = "closed"
            loan.closed_at = now
            await record_audit(
                session,
                organization_id=loan.organization_id,
                actor_user_id=context.user.id,
                action="employee_loan.closed",
                entity_type="employee_loan",
                entity_id=loan.id,
                metadata={"loan_number": loan.loan_number, "payroll_run_id": str(run.id)},
            )
    await session.flush()
