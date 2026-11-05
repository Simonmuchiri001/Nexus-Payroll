"""Expense claim approval, reimbursement and accounting coverage."""

from __future__ import annotations

import httpx
import pytest

from .conftest import AuthenticatedSession, build_session, create_employee, create_member

API = "/api/v1"

pytestmark = pytest.mark.postgres


async def _create_category(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    *,
    code: str = "TRAVEL",
) -> dict[str, object]:
    accounts = await client.get(
        f"{API}/accounting/accounts",
        headers=session.headers,
        params={"company_id": session.company_id},
    )
    assert accounts.status_code == 200, accounts.text
    general_expense = next(account for account in accounts.json() if account["code"] == "5300")
    response = await client.post(
        f"{API}/expenses/categories",
        headers=session.headers,
        json={
            "company_id": session.company_id,
            "code": code,
            "name": "Business travel",
            "description": "Transport, accommodation and meals for approved business travel.",
            "expense_account_id": general_expense["id"],
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _finance_session(
    client: httpx.AsyncClient,
    admin_session: AuthenticatedSession,
    *,
    email: str,
    full_name: str,
    password: str,
) -> AuthenticatedSession:
    await create_member(
        client,
        admin_session,
        email=email,
        full_name=full_name,
        password=password,
        roles=[{"role_code": "finance_manager", "company_id": admin_session.company_id}],
    )
    return await build_session(
        client,
        admin_session.tenant,
        email=email,
        password=password,
    )


async def test_expense_claim_reimbursement_posts_balanced_journal_once(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    employee = await create_employee(client, admin_session)
    category = await _create_category(client, admin_session)
    approver = await _finance_session(
        client,
        admin_session,
        email="expense.approver@nexus.co.ke",
        full_name="Expense Approver",
        password="ExpenseApproval!2026",
    )
    payer = await _finance_session(
        client,
        admin_session,
        email="expense.payer@nexus.co.ke",
        full_name="Expense Payer",
        password="ExpensePayment!2026",
    )

    created = await client.post(
        f"{API}/expenses/claims",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "employee_id": employee["id"],
            "category_id": category["id"],
            "expense_date": "2026-11-03",
            "description": "Client workshop transport",
            "amount": "2500.00",
            "receipt_number": "RCT-2026-1103",
            "receipt_date": "2026-11-03",
            "receipt_merchant": "Nairobi Shuttle Co.",
        },
    )
    assert created.status_code == 201, created.text
    claim = created.json()
    assert claim["claim_number"].startswith("EXP-2026-")
    assert claim["status"] == "draft"
    assert claim["currency"] == "KES"
    assert claim["expense_account_code"] == "5300"
    assert claim["receipt_merchant"] == "Nairobi Shuttle Co."

    updated = await client.patch(
        f"{API}/expenses/claims/{claim['id']}",
        headers=admin_session.headers,
        json={"amount": "2750.00", "version": claim["version"]},
    )
    assert updated.status_code == 200, updated.text
    claim = updated.json()
    assert claim["amount"] == "2750.00"

    submitted = await client.post(
        f"{API}/expenses/claims/{claim['id']}/submit",
        headers=admin_session.headers,
        json={"version": claim["version"]},
    )
    assert submitted.status_code == 200, submitted.text
    claim = submitted.json()
    assert claim["status"] == "submitted"
    assert claim["submitted_at"] is not None

    self_approval = await client.post(
        f"{API}/expenses/claims/{claim['id']}/approve",
        headers=admin_session.headers,
        json={"version": claim["version"]},
    )
    assert self_approval.status_code == 409
    assert "requester" in self_approval.json()["error"]["message"].lower()

    approved = await client.post(
        f"{API}/expenses/claims/{claim['id']}/approve",
        headers=approver.headers,
        json={"version": claim["version"], "note": "Business purpose verified."},
    )
    assert approved.status_code == 200, approved.text
    claim = approved.json()
    assert claim["status"] == "approved"
    assert claim["decided_by_name"] == "Expense Approver"

    archive_blocked = await client.patch(
        f"{API}/expenses/categories/{category['id']}",
        headers=admin_session.headers,
        json={"status": "archived", "version": category["version"]},
    )
    assert archive_blocked.status_code == 409
    assert "claims" in archive_blocked.json()["error"]["message"].lower()

    approver_cannot_pay = await client.post(
        f"{API}/expenses/claims/{claim['id']}/reimburse",
        headers=approver.headers,
        json={
            "version": claim["version"],
            "paid_on": "2026-11-05",
            "reference": "BANK-EXP-0001",
        },
    )
    assert approver_cannot_pay.status_code == 409
    assert "approver" in approver_cannot_pay.json()["error"]["message"].lower()

    reimbursed = await client.post(
        f"{API}/expenses/claims/{claim['id']}/reimburse",
        headers=payer.headers,
        json={
            "version": claim["version"],
            "paid_on": "2026-11-05",
            "reference": "BANK-EXP-0001",
        },
    )
    assert reimbursed.status_code == 200, reimbursed.text
    claim = reimbursed.json()
    assert claim["status"] == "reimbursed"
    assert claim["amount"] == "2750.00"
    assert claim["paid_by_name"] == "Expense Payer"
    assert claim["journal_id"] is not None

    journal_response = await client.get(
        f"{API}/accounting/journals/{claim['journal_id']}",
        headers=payer.headers,
    )
    assert journal_response.status_code == 200, journal_response.text
    journal = journal_response.json()["journal"]
    assert journal["source_type"] == "expense_claim"
    assert journal["source_id"] == claim["id"]
    assert journal["total_debit"] == "2750.00"
    assert journal["total_credit"] == "2750.00"
    lines = journal_response.json()["lines"]
    assert {line["debit"] for line in lines} == {"0.00", "2750.00"}
    assert {line["credit"] for line in lines} == {"0.00", "2750.00"}
    assert any(line["description"].startswith("Business travel expense") for line in lines)
    assert any(line["description"].startswith("Cash reimbursed") for line in lines)

    repeated = await client.post(
        f"{API}/expenses/claims/{claim['id']}/reimburse",
        headers=payer.headers,
        json={
            "version": claim["version"],
            "paid_on": "2026-11-05",
            "reference": "BANK-EXP-0001-RETRY",
        },
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["journal_id"] == claim["journal_id"]
    assert repeated.json()["payment_reference"] == "BANK-EXP-0001"

    categories = await client.get(
        f"{API}/expenses/categories",
        headers=admin_session.headers,
        params={"company_id": admin_session.company_id},
    )
    assert categories.status_code == 200, categories.text
    archive_category = await client.patch(
        f"{API}/expenses/categories/{category['id']}",
        headers=admin_session.headers,
        json={"status": "archived", "version": category["version"]},
    )
    assert archive_category.status_code == 200, archive_category.text
    assert archive_category.json()["status"] == "archived"


async def test_expense_claim_validation_and_permission_boundary(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    employee = await create_employee(client, admin_session)
    category = await _create_category(client, admin_session, code="OFFICE")
    await create_member(
        client,
        admin_session,
        email="expense.viewer@nexus.co.ke",
        full_name="Expense Viewer",
        password="ExpenseViewer!2026",
        roles=[{"role_code": "auditor", "company_id": admin_session.company_id}],
    )
    viewer = await build_session(
        client,
        admin_session.tenant,
        email="expense.viewer@nexus.co.ke",
        password="ExpenseViewer!2026",
    )

    visible = await client.get(
        f"{API}/expenses/claims",
        headers=viewer.headers,
        params={"company_id": admin_session.company_id},
    )
    assert visible.status_code == 200, visible.text
    assert visible.json()["items"] == []

    forbidden = await client.post(
        f"{API}/expenses/claims",
        headers=viewer.headers,
        json={
            "company_id": admin_session.company_id,
            "employee_id": employee["id"],
            "category_id": category["id"],
            "expense_date": "2026-11-03",
            "description": "Stationery",
            "amount": "100.00",
        },
    )
    assert forbidden.status_code == 403

    invalid_receipt = await client.post(
        f"{API}/expenses/claims",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "employee_id": employee["id"],
            "category_id": category["id"],
            "expense_date": "2026-11-03",
            "description": "Stationery",
            "amount": "100.00",
            "receipt_date": "2026-11-02",
        },
    )
    assert invalid_receipt.status_code == 422

    old_expense = await client.post(
        f"{API}/expenses/claims",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "employee_id": employee["id"],
            "category_id": category["id"],
            "expense_date": "2025-12-31",
            "description": "Pre-hire stationery",
            "amount": "100.00",
        },
    )
    assert old_expense.status_code == 422

    duplicate_category = await client.post(
        f"{API}/expenses/categories",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "code": "OFFICE",
            "name": "Duplicate office",
            "expense_account_id": category["expense_account_id"],
        },
    )
    assert duplicate_category.status_code == 409
