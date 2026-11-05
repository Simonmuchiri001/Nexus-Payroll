"""Expense category administration, claim approval and reimbursement services."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.accounting.models import Account
from nexus_api.api.deps import PaginationParams
from nexus_api.audit.service import record_audit
from nexus_api.core.errors import (
    ConflictError,
    IntegrityError,
    NotFoundError,
    ValidationFailureError,
)
from nexus_api.employees.models import Employee
from nexus_api.expenses.models import ExpenseCategory, ExpenseClaim
from nexus_api.expenses.schemas import (
    ExpenseCategoryCreate,
    ExpenseCategoryResponse,
    ExpenseCategoryStatus,
    ExpenseCategoryUpdate,
    ExpenseClaimCreate,
    ExpenseClaimPage,
    ExpenseClaimResponse,
    ExpenseClaimStatus,
    ExpenseClaimUpdate,
    ExpenseDecision,
    ExpenseReimbursement,
)
from nexus_api.identity.models import User
from nexus_api.identity.permissions import PermissionCode
from nexus_api.identity.service import AuthContext, validate_company_access
from nexus_api.organizations.models import Company


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _employee_name(employee: Employee) -> str:
    return " ".join(
        part for part in (employee.first_name, employee.middle_name, employee.last_name) if part
    )


async def _user_names(session: AsyncSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not user_ids:
        return {}
    rows = await session.execute(select(User.id, User.full_name).where(User.id.in_(user_ids)))
    return {user_id: name for user_id, name in rows.all()}


def _category_response(category: ExpenseCategory, account: Account) -> ExpenseCategoryResponse:
    return ExpenseCategoryResponse(
        id=category.id,
        organization_id=category.organization_id,
        company_id=category.company_id,
        code=category.code,
        name=category.name,
        description=category.description,
        expense_account_id=category.expense_account_id,
        expense_account_code=account.code,
        expense_account_name=account.name,
        status=cast(ExpenseCategoryStatus, category.status),
        created_at=category.created_at,
        version=category.version,
    )


def _claim_response(
    claim: ExpenseClaim,
    employee: Employee,
    category: ExpenseCategory,
    account: Account,
    user_names: dict[uuid.UUID, str],
) -> ExpenseClaimResponse:
    return ExpenseClaimResponse(
        id=claim.id,
        organization_id=claim.organization_id,
        company_id=claim.company_id,
        employee_id=claim.employee_id,
        employee_number=employee.employee_number,
        employee_name=_employee_name(employee),
        category_id=claim.category_id,
        category_code=category.code,
        category_name=category.name,
        expense_account_id=category.expense_account_id,
        expense_account_code=account.code,
        expense_account_name=account.name,
        claim_number=claim.claim_number,
        expense_date=claim.expense_date,
        description=claim.description,
        amount=claim.amount,
        currency=claim.currency,
        receipt_number=claim.receipt_number,
        receipt_date=claim.receipt_date,
        receipt_merchant=claim.receipt_merchant,
        status=cast(ExpenseClaimStatus, claim.status),
        requested_by=claim.requested_by,
        requested_by_name=user_names.get(claim.requested_by, "Unknown user"),
        submitted_at=claim.submitted_at,
        decided_by=claim.decided_by,
        decided_by_name=user_names.get(claim.decided_by) if claim.decided_by else None,
        decided_at=claim.decided_at,
        decision_note=claim.decision_note,
        paid_by=claim.paid_by,
        paid_by_name=user_names.get(claim.paid_by) if claim.paid_by else None,
        paid_on=claim.paid_on,
        paid_at=claim.paid_at,
        payment_reference=claim.payment_reference,
        journal_id=claim.journal_id,
        cancelled_by=claim.cancelled_by,
        cancelled_by_name=user_names.get(claim.cancelled_by) if claim.cancelled_by else None,
        cancelled_at=claim.cancelled_at,
        created_at=claim.created_at,
        version=claim.version,
    )


async def _expense_account(
    session: AsyncSession, company_id: uuid.UUID, account_id: uuid.UUID
) -> Account:
    account = await session.scalar(
        select(Account).where(Account.id == account_id, Account.company_id == company_id)
    )
    if account is None:
        raise NotFoundError("Expense account not found for this company.")
    if account.account_type != "expense":
        raise ValidationFailureError("Expense categories must map to an expense account.")
    if not account.is_active:
        raise ConflictError("The selected expense account is inactive.")
    return account


async def _locked_category(
    session: AsyncSession, context: AuthContext, category_id: uuid.UUID
) -> ExpenseCategory:
    category = await session.scalar(
        select(ExpenseCategory)
        .where(
            ExpenseCategory.id == category_id,
            ExpenseCategory.organization_id == context.organization.id,
        )
        .with_for_update()
    )
    if category is None:
        raise NotFoundError("Expense category not found.")
    await validate_company_access(
        session, context, category.company_id, PermissionCode.EXPENSE_MANAGE
    )
    return category


def _check_version(record: ExpenseCategory | ExpenseClaim, version: int | None) -> None:
    if version is not None and version != record.version:
        raise ConflictError(
            "This expense record was changed by another user. Reload it and try again."
        )


async def list_categories(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    status: str | None = None,
) -> list[ExpenseCategoryResponse]:
    await validate_company_access(session, context, company_id, PermissionCode.EXPENSE_VIEW)
    filters = [ExpenseCategory.company_id == company_id]
    if status:
        filters.append(ExpenseCategory.status == status)
    rows = (
        await session.execute(
            select(ExpenseCategory, Account)
            .join(Account, Account.id == ExpenseCategory.expense_account_id)
            .where(*filters)
            .order_by(ExpenseCategory.name, ExpenseCategory.code)
        )
    ).all()
    return [_category_response(category, account) for category, account in rows]


async def create_category(
    session: AsyncSession, context: AuthContext, payload: ExpenseCategoryCreate
) -> ExpenseCategoryResponse:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.EXPENSE_MANAGE
    )
    existing = await session.scalar(
        select(ExpenseCategory.id).where(
            ExpenseCategory.company_id == company.id,
            ExpenseCategory.code == payload.code.upper(),
        )
    )
    if existing is not None:
        raise ConflictError("An expense category with that code already exists.")
    account = await _expense_account(session, company.id, payload.expense_account_id)
    category = ExpenseCategory(
        organization_id=context.organization.id,
        company_id=company.id,
        code=payload.code.upper(),
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        expense_account_id=account.id,
        status="active",
    )
    session.add(category)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="expense_category.created",
        entity_type="expense_category",
        entity_id=category.id,
        metadata={
            "company_id": str(company.id),
            "code": category.code,
            "expense_account_code": account.code,
        },
    )
    return _category_response(category, account)


async def update_category(
    session: AsyncSession,
    context: AuthContext,
    category_id: uuid.UUID,
    payload: ExpenseCategoryUpdate,
) -> ExpenseCategoryResponse:
    category = await _locked_category(session, context, category_id)
    _check_version(category, payload.version)
    account = await session.get(Account, category.expense_account_id)
    if account is None:
        raise IntegrityError("The expense account linked to this category is missing.")

    if payload.name is not None:
        category.name = payload.name.strip()
    if payload.expense_account_id is not None:
        account = await _expense_account(session, category.company_id, payload.expense_account_id)
        category.expense_account_id = account.id
    if "description" in payload.model_fields_set:
        category.description = payload.description.strip() if payload.description else None
    if payload.status is not None:
        if payload.status == "archived":
            open_claims = await session.scalar(
                select(func.count(ExpenseClaim.id)).where(
                    ExpenseClaim.category_id == category.id,
                    ExpenseClaim.status.in_(("draft", "submitted", "approved")),
                )
            )
            if open_claims:
                raise ConflictError(
                    "Resolve all draft, submitted and approved claims before archiving "
                    "this category."
                )
        category.status = payload.status

    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="expense_category.updated",
        entity_type="expense_category",
        entity_id=category.id,
        metadata={
            "code": category.code,
            "status": category.status,
            "expense_account_code": account.code,
        },
    )
    return _category_response(category, account)


async def _next_claim_number(session: AsyncSession, company_id: uuid.UUID, year: int) -> str:
    await session.execute(select(Company.id).where(Company.id == company_id).with_for_update())
    prefix = f"EXP-{year}-"
    count = await session.scalar(
        select(func.count(ExpenseClaim.id)).where(
            ExpenseClaim.company_id == company_id,
            ExpenseClaim.claim_number.like(f"{prefix}%"),
        )
    )
    return f"{prefix}{(count or 0) + 1:04d}"


async def _claim_employee(
    session: AsyncSession,
    context: AuthContext,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
) -> Employee:
    employee = await session.scalar(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.company_id == company_id,
            Employee.organization_id == context.organization.id,
        )
    )
    if employee is None:
        raise NotFoundError("Employee not found for this company.")
    return employee


async def _claim_category(
    session: AsyncSession,
    context: AuthContext,
    company_id: uuid.UUID,
    category_id: uuid.UUID,
) -> tuple[ExpenseCategory, Account]:
    category = await session.scalar(
        select(ExpenseCategory).where(
            ExpenseCategory.id == category_id,
            ExpenseCategory.company_id == company_id,
            ExpenseCategory.organization_id == context.organization.id,
        )
    )
    if category is None:
        raise NotFoundError("Expense category not found for this company.")
    account = await _expense_account(session, company_id, category.expense_account_id)
    return category, account


def _validate_claim_dates(employee: Employee, expense_date: date) -> None:
    if expense_date < employee.hire_date:
        raise ValidationFailureError("An expense cannot predate the employee's hire date.")
    if employee.termination_date is not None and expense_date > employee.termination_date:
        raise ValidationFailureError("An expense cannot postdate the employee's termination date.")


def _validate_receipt_date(receipt_date: date | None, expense_date: date) -> None:
    if receipt_date is not None and receipt_date < expense_date:
        raise ValidationFailureError("The receipt date must not precede the expense date.")


async def _locked_claim(
    session: AsyncSession,
    context: AuthContext,
    claim_id: uuid.UUID,
    permission: PermissionCode,
) -> ExpenseClaim:
    claim = await session.scalar(
        select(ExpenseClaim)
        .where(
            ExpenseClaim.id == claim_id,
            ExpenseClaim.organization_id == context.organization.id,
        )
        .with_for_update()
    )
    if claim is None:
        raise NotFoundError("Expense claim not found.")
    await validate_company_access(session, context, claim.company_id, permission)
    return claim


async def create_claim(
    session: AsyncSession, context: AuthContext, payload: ExpenseClaimCreate
) -> ExpenseClaimResponse:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.EXPENSE_SUBMIT
    )
    employee = await _claim_employee(session, context, company.id, payload.employee_id)
    _validate_claim_dates(employee, payload.expense_date)
    _validate_receipt_date(payload.receipt_date, payload.expense_date)
    category, _account = await _claim_category(session, context, company.id, payload.category_id)
    if category.status != "active":
        raise ConflictError("Claims can only be submitted against an active category.")

    claim = ExpenseClaim(
        organization_id=context.organization.id,
        company_id=company.id,
        employee_id=employee.id,
        category_id=category.id,
        claim_number=await _next_claim_number(session, company.id, date.today().year),
        expense_date=payload.expense_date,
        description=payload.description.strip(),
        amount=_money(payload.amount),
        currency=company.base_currency,
        receipt_number=payload.receipt_number.strip() if payload.receipt_number else None,
        receipt_date=payload.receipt_date,
        receipt_merchant=payload.receipt_merchant.strip() if payload.receipt_merchant else None,
        status="draft",
        requested_by=context.user.id,
    )
    session.add(claim)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="expense_claim.created",
        entity_type="expense_claim",
        entity_id=claim.id,
        metadata={
            "claim_number": claim.claim_number,
            "company_id": str(company.id),
            "employee_id": str(employee.id),
            "amount": str(claim.amount),
            "category_code": category.code,
        },
    )
    return await _claim_payload(session, claim)


async def update_claim(
    session: AsyncSession,
    context: AuthContext,
    claim_id: uuid.UUID,
    payload: ExpenseClaimUpdate,
) -> ExpenseClaimResponse:
    claim = await _locked_claim(session, context, claim_id, PermissionCode.EXPENSE_SUBMIT)
    _check_version(claim, payload.version)
    if claim.status != "draft":
        raise ConflictError("Only a draft expense claim can be edited.")
    if claim.requested_by != context.user.id and not context.has_permission(
        PermissionCode.EXPENSE_MANAGE, company_id=claim.company_id
    ):
        raise ConflictError("Only the requester or an expense manager can edit this claim.")

    employee = await _claim_employee(
        session,
        context,
        claim.company_id,
        payload.employee_id or claim.employee_id,
    )
    category, _account = await _claim_category(
        session,
        context,
        claim.company_id,
        payload.category_id or claim.category_id,
    )
    if category.status != "active":
        raise ConflictError("Claims can only use an active expense category.")

    expense_date = payload.expense_date or claim.expense_date
    _validate_claim_dates(employee, expense_date)

    receipt_date = claim.receipt_date
    if "receipt_date" in payload.model_fields_set:
        receipt_date = payload.receipt_date
    _validate_receipt_date(receipt_date, expense_date)

    claim.employee_id = employee.id
    claim.category_id = category.id
    claim.expense_date = expense_date
    if payload.description is not None:
        claim.description = payload.description.strip()
    if payload.amount is not None:
        claim.amount = _money(payload.amount)
    if "receipt_number" in payload.model_fields_set:
        claim.receipt_number = payload.receipt_number.strip() if payload.receipt_number else None
    if "receipt_date" in payload.model_fields_set:
        claim.receipt_date = payload.receipt_date
    if "receipt_merchant" in payload.model_fields_set:
        claim.receipt_merchant = (
            payload.receipt_merchant.strip() if payload.receipt_merchant else None
        )

    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="expense_claim.updated",
        entity_type="expense_claim",
        entity_id=claim.id,
        metadata={
            "claim_number": claim.claim_number,
            "amount": str(claim.amount),
            "category_code": category.code,
        },
    )
    return await _claim_payload(session, claim)


async def submit_claim(
    session: AsyncSession, context: AuthContext, claim_id: uuid.UUID, payload: ExpenseDecision
) -> ExpenseClaimResponse:
    claim = await _locked_claim(session, context, claim_id, PermissionCode.EXPENSE_SUBMIT)
    _check_version(claim, payload.version)
    if claim.status != "draft":
        raise ConflictError("Only a draft expense claim can be submitted.")
    if claim.requested_by != context.user.id and not context.has_permission(
        PermissionCode.EXPENSE_MANAGE, company_id=claim.company_id
    ):
        raise ConflictError("Only the requester or an expense manager can submit this claim.")

    employee = await session.get(Employee, claim.employee_id)
    if employee is None:
        raise IntegrityError("The employee linked to this claim is missing.")
    _validate_claim_dates(employee, claim.expense_date)
    category, _account = await _claim_category(
        session, context, claim.company_id, claim.category_id
    )
    if category.status != "active":
        raise ConflictError("The expense category must be active before the claim is submitted.")

    claim.status = "submitted"
    claim.submitted_at = datetime.now(UTC)
    if payload.note and payload.note.strip():
        claim.decision_note = payload.note.strip()
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="expense_claim.submitted",
        entity_type="expense_claim",
        entity_id=claim.id,
        metadata={"claim_number": claim.claim_number, "amount": str(claim.amount)},
    )
    return await _claim_payload(session, claim)


async def approve_claim(
    session: AsyncSession, context: AuthContext, claim_id: uuid.UUID, payload: ExpenseDecision
) -> ExpenseClaimResponse:
    claim = await _locked_claim(session, context, claim_id, PermissionCode.EXPENSE_APPROVE)
    _check_version(claim, payload.version)
    if claim.status != "submitted":
        raise ConflictError("Only a submitted expense claim can be approved.")
    if claim.requested_by == context.user.id:
        raise ConflictError("Separation of duties: the requester cannot approve a claim.")
    claim.decided_by = context.user.id
    claim.decided_at = datetime.now(UTC)
    claim.decision_note = payload.note.strip() if payload.note else None
    claim.status = "approved"
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="expense_claim.approved",
        entity_type="expense_claim",
        entity_id=claim.id,
        metadata={"claim_number": claim.claim_number, "amount": str(claim.amount)},
    )
    return await _claim_payload(session, claim)


async def reject_claim(
    session: AsyncSession, context: AuthContext, claim_id: uuid.UUID, payload: ExpenseDecision
) -> ExpenseClaimResponse:
    claim = await _locked_claim(session, context, claim_id, PermissionCode.EXPENSE_APPROVE)
    _check_version(claim, payload.version)
    if claim.status != "submitted":
        raise ConflictError("Only a submitted expense claim can be rejected.")
    if claim.requested_by == context.user.id:
        raise ConflictError("Separation of duties: the requester cannot reject a claim.")
    note = payload.note.strip() if payload.note else ""
    if not note:
        raise ValidationFailureError("A rejection note is required.")
    claim.decided_by = context.user.id
    claim.decided_at = datetime.now(UTC)
    claim.decision_note = note
    claim.status = "rejected"
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="expense_claim.rejected",
        entity_type="expense_claim",
        entity_id=claim.id,
        metadata={"claim_number": claim.claim_number, "note": note},
    )
    return await _claim_payload(session, claim)


async def cancel_claim(
    session: AsyncSession, context: AuthContext, claim_id: uuid.UUID, payload: ExpenseDecision
) -> ExpenseClaimResponse:
    claim = await _locked_claim(session, context, claim_id, PermissionCode.EXPENSE_SUBMIT)
    _check_version(claim, payload.version)
    if claim.status not in ("draft", "submitted"):
        raise ConflictError("Only a draft or submitted expense claim can be cancelled.")
    if claim.requested_by != context.user.id and not context.has_permission(
        PermissionCode.EXPENSE_APPROVE, company_id=claim.company_id
    ):
        raise ConflictError("Only the requester or an approver can cancel this claim.")
    claim.cancelled_by = context.user.id
    claim.cancelled_at = datetime.now(UTC)
    if payload.note and payload.note.strip():
        claim.decision_note = payload.note.strip()
    claim.status = "cancelled"
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="expense_claim.cancelled",
        entity_type="expense_claim",
        entity_id=claim.id,
        metadata={"claim_number": claim.claim_number},
    )
    return await _claim_payload(session, claim)


async def reimburse_claim(
    session: AsyncSession,
    context: AuthContext,
    claim_id: uuid.UUID,
    payload: ExpenseReimbursement,
) -> ExpenseClaimResponse:
    from nexus_api.accounting.service import post_expense_reimbursement

    claim = await _locked_claim(session, context, claim_id, PermissionCode.EXPENSE_PAY)
    _check_version(claim, payload.version)
    if claim.status == "reimbursed":
        return await _claim_payload(session, claim)
    if claim.status != "approved":
        raise ConflictError("Only an approved expense claim can be reimbursed.")
    if claim.requested_by == context.user.id:
        raise ConflictError("Separation of duties: the requester cannot reimburse a claim.")
    if claim.decided_by == context.user.id:
        raise ConflictError("Separation of duties: the approver cannot reimburse the same claim.")
    if payload.paid_on < claim.expense_date:
        raise ValidationFailureError("The reimbursement date must not precede the expense date.")

    employee = await session.get(Employee, claim.employee_id)
    if employee is None:
        raise IntegrityError("The employee linked to this claim is missing.")
    category, account = await _claim_category(session, context, claim.company_id, claim.category_id)
    if not account.is_active:
        raise ConflictError("The expense account mapped to this category is inactive.")

    journal = await post_expense_reimbursement(
        session, context, claim, category, payload.paid_on, payload.reference.strip()
    )
    claim.paid_by = context.user.id
    claim.paid_on = payload.paid_on
    claim.paid_at = datetime.now(UTC)
    claim.payment_reference = payload.reference.strip()
    claim.journal_id = journal.id
    claim.status = "reimbursed"
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="expense_claim.reimbursed",
        entity_type="expense_claim",
        entity_id=claim.id,
        metadata={
            "claim_number": claim.claim_number,
            "amount": str(claim.amount),
            "journal_id": str(journal.id),
            "payment_reference": claim.payment_reference,
        },
    )
    return await _claim_payload(session, claim)


async def _claim_payload(session: AsyncSession, claim: ExpenseClaim) -> ExpenseClaimResponse:
    row = (
        await session.execute(
            select(ExpenseClaim, Employee, ExpenseCategory, Account)
            .join(Employee, Employee.id == ExpenseClaim.employee_id)
            .join(ExpenseCategory, ExpenseCategory.id == ExpenseClaim.category_id)
            .join(Account, Account.id == ExpenseCategory.expense_account_id)
            .where(ExpenseClaim.id == claim.id)
        )
    ).one_or_none()
    if row is None:
        raise IntegrityError("The expense claim's linked records could not be loaded.")
    loaded_claim, employee, category, account = row
    user_names = await _user_names(
        session,
        {
            user_id
            for user_id in (
                loaded_claim.requested_by,
                loaded_claim.decided_by,
                loaded_claim.paid_by,
                loaded_claim.cancelled_by,
            )
            if user_id is not None
        },
    )
    return _claim_response(loaded_claim, employee, category, account, user_names)


async def get_claim(
    session: AsyncSession, context: AuthContext, claim_id: uuid.UUID
) -> ExpenseClaimResponse:
    claim = await session.scalar(
        select(ExpenseClaim).where(
            ExpenseClaim.id == claim_id,
            ExpenseClaim.organization_id == context.organization.id,
        )
    )
    if claim is None:
        raise NotFoundError("Expense claim not found.")
    await validate_company_access(session, context, claim.company_id, PermissionCode.EXPENSE_VIEW)
    return await _claim_payload(session, claim)


async def list_claims(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    pagination: PaginationParams,
    employee_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    status: str | None = None,
) -> ExpenseClaimPage:
    await validate_company_access(session, context, company_id, PermissionCode.EXPENSE_VIEW)
    filters = [ExpenseClaim.company_id == company_id]
    if employee_id is not None:
        filters.append(ExpenseClaim.employee_id == employee_id)
    if category_id is not None:
        filters.append(ExpenseClaim.category_id == category_id)
    if status:
        filters.append(ExpenseClaim.status == status)

    total = await session.scalar(select(func.count(ExpenseClaim.id)).where(*filters))
    rows = (
        await session.execute(
            select(ExpenseClaim, Employee, ExpenseCategory, Account)
            .join(Employee, Employee.id == ExpenseClaim.employee_id)
            .join(ExpenseCategory, ExpenseCategory.id == ExpenseClaim.category_id)
            .join(Account, Account.id == ExpenseCategory.expense_account_id)
            .where(*filters)
            .order_by(ExpenseClaim.created_at.desc(), ExpenseClaim.claim_number.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()
    user_names = await _user_names(
        session,
        {
            user_id
            for claim, _employee, _category, _account in rows
            for user_id in (claim.requested_by, claim.decided_by, claim.paid_by, claim.cancelled_by)
            if user_id is not None
        },
    )
    items = [
        _claim_response(claim, employee, category, account, user_names)
        for claim, employee, category, account in rows
    ]
    return ExpenseClaimPage(
        items=items,
        total=total or 0,
        page=pagination.page,
        page_size=pagination.page_size,
    )
