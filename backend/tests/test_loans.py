"""Employee loan and advance workflow coverage."""

from __future__ import annotations

import httpx
import pytest

from .conftest import AuthenticatedSession, build_session, create_employee, create_member

API = "/api/v1"

pytestmark = pytest.mark.postgres


async def _create_payroll_period(
    client: httpx.AsyncClient, session: AuthenticatedSession
) -> tuple[str, str]:
    group_response = await client.post(
        f"{API}/payroll/pay-groups",
        headers=session.headers,
        json={
            "company_id": session.company_id,
            "code": "LOANMONTHLY",
            "name": "Loan Monthly Payroll",
            "currency": "KES",
        },
    )
    assert group_response.status_code == 201, group_response.text
    group_id = group_response.json()["id"]

    period_response = await client.post(
        f"{API}/payroll/periods",
        headers=session.headers,
        json={
            "pay_group_id": group_id,
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "payment_date": "2026-02-09",
        },
    )
    assert period_response.status_code == 201, period_response.text
    return group_id, period_response.json()["id"]


async def test_loan_application_approval_and_disbursement(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    employee = await create_employee(client, admin_session)
    employee_id = employee["id"]

    loan_response = await client.post(
        f"{API}/loans",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "employee_id": employee_id,
            "kind": "advance",
            "principal_amount": "10000.00",
            "interest_rate_annual": "0.00",
            "term_months": 3,
            "purpose": "Emergency medical costs today",
        },
    )
    assert loan_response.status_code == 201, loan_response.text
    loan = loan_response.json()
    assert loan["status"] == "pending"
    assert loan["loan_number"].startswith("ADV-")
    assert loan["outstanding_principal"] == "10000.00"
    assert loan["installments_paid"] == 0
    assert loan["installments_total"] == 0

    await create_member(
        client,
        admin_session,
        email="loan.approver@nexus.co.ke",
        full_name="Loan Approver",
        password="LoanApproval!2026",
        roles=[
            {"role_code": "payroll_manager", "company_id": admin_session.company_id},
            {"role_code": "finance_manager", "company_id": admin_session.company_id},
        ],
    )
    approver_session = await build_session(
        client,
        admin_session.tenant,
        email="loan.approver@nexus.co.ke",
        password="LoanApproval!2026",
    )

    self_approval = await client.post(
        f"{API}/loans/{loan['id']}/approve",
        headers=admin_session.headers,
        json={"version": loan["version"]},
    )
    assert self_approval.status_code == 409
    assert "separation of duties" in self_approval.json()["error"]["message"].lower()

    approved = await client.post(
        f"{API}/loans/{loan['id']}/approve",
        headers=approver_session.headers,
        json={"version": loan["version"], "note": "Approved for medical costs."},
    )
    assert approved.status_code == 200, approved.text
    approved_payload = approved.json()
    assert approved_payload["loan"]["status"] == "approved"
    assert approved_payload["loan"]["decided_by"] is not None
    assert approved_payload["loan"]["decision_note"] == "Approved for medical costs."

    disbursed = await client.post(
        f"{API}/loans/{loan['id']}/disburse",
        headers=approver_session.headers,
        json={
            "version": approved_payload["loan"]["version"],
            "disbursed_on": "2026-01-20",
            "first_due_date": "2026-01-20",
            "reference": "DISB-2026-0001",
        },
    )
    assert disbursed.status_code == 200, disbursed.text
    disbursed_payload = disbursed.json()
    assert disbursed_payload["loan"]["status"] == "disbursed"
    assert disbursed_payload["loan"]["journal_id"] is not None
    assert disbursed_payload["loan"]["total_repayable"] == "10000.00"
    assert disbursed_payload["loan"]["outstanding_principal"] == "10000.00"
    assert len(disbursed_payload["installments"]) == 3
    assert disbursed_payload["installments"][0]["principal_due"] == "3333.33"
    assert disbursed_payload["installments"][0]["amount_due"] == "3333.33"
    assert all(
        installment["status"] == "scheduled" for installment in disbursed_payload["installments"]
    )

    journal_response = await client.get(
        f"{API}/accounting/journals/{disbursed_payload['loan']['journal_id']}",
        headers=approver_session.headers,
    )
    assert journal_response.status_code == 200, journal_response.text
    journal_payload = journal_response.json()["journal"]
    assert journal_payload["status"] == "posted"
    assert journal_payload["total_debit"] == "10000.00"
    assert journal_payload["total_credit"] == "10000.00"


async def test_approved_payroll_with_loan_deduction_and_balanced_posting(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    group_id, period_id = await _create_payroll_period(client, admin_session)
    employee = await create_employee(client, admin_session)
    employee_id = employee["id"]

    loan_response = await client.post(
        f"{API}/loans",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "employee_id": employee_id,
            "kind": "loan",
            "principal_amount": "12000.00",
            "interest_rate_annual": "0.00",
            "term_months": 1,
            "purpose": "Planned business expansion",
        },
    )
    assert loan_response.status_code == 201, loan_response.text
    loan = loan_response.json()

    await create_member(
        client,
        admin_session,
        email="loan.approver@nexus.co.ke",
        full_name="Loan Approver",
        password="LoanApproval!2026",
        roles=[
            {"role_code": "payroll_manager", "company_id": admin_session.company_id},
            {"role_code": "finance_manager", "company_id": admin_session.company_id},
        ],
    )
    approver_session = await build_session(
        client,
        admin_session.tenant,
        email="loan.approver@nexus.co.ke",
        password="LoanApproval!2026",
    )
    approved = await client.post(
        f"{API}/loans/{loan['id']}/approve",
        headers=approver_session.headers,
        json={"version": loan["version"]},
    )
    assert approved.status_code == 200, approved.text
    approved_payload = approved.json()

    disbursed = await client.post(
        f"{API}/loans/{loan['id']}/disburse",
        headers=approver_session.headers,
        json={
            "version": approved_payload["loan"]["version"],
            "disbursed_on": "2026-01-20",
            "first_due_date": "2026-01-20",
            "reference": "DISB-2026-0002",
        },
    )
    assert disbursed.status_code == 200, disbursed.text
    disbursed_payload = disbursed.json()
    assert disbursed_payload["loan"]["status"] == "disbursed"

    run_response = await client.post(
        f"{API}/payroll/runs",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "pay_group_id": group_id,
            "payroll_period_id": period_id,
        },
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()
    run_id = run["id"]

    calculated = await client.post(
        f"{API}/payroll/runs/{run_id}/calculate", headers=admin_session.headers
    )
    assert calculated.status_code == 200, calculated.text
    calculated_payload = calculated.json()
    assert calculated_payload["net_total"] == "59617.65"
    assert calculated_payload["loan_deduction_total"] == "12000.00"
    assert calculated_payload["deductions_total"] == "40382.35"

    results = await client.get(
        f"{API}/payroll/runs/{run_id}/results", headers=admin_session.headers
    )
    assert results.status_code == 200
    result = results.json()[0]
    assert result["gross"] == "100000.00"
    assert result["net_pay"] == "59617.65"
    assert result["loan_deduction"] == "12000.00"
    assert result["loan_principal"] == "12000.00"
    assert result["loan_interest"] == "0.00"

    approved_run = await client.post(
        f"{API}/payroll/runs/{run_id}/approve", headers=approver_session.headers
    )
    assert approved_run.status_code == 200, approved_run.text

    finalised = await client.post(
        f"{API}/payroll/runs/{run_id}/finalize", headers=approver_session.headers
    )
    assert finalised.status_code == 200, finalised.text
    closed_loan = await client.get(f"{API}/loans/{loan['id']}", headers=approver_session.headers)
    assert closed_loan.status_code == 200, closed_loan.text
    closed_payload = closed_loan.json()
    assert closed_payload["loan"]["status"] == "closed"
    assert closed_payload["loan"]["outstanding_principal"] == "0.00"
    assert closed_payload["installments"][0]["status"] == "deducted"
    assert closed_payload["installments"][0]["payroll_run_id"] == run_id
    assert closed_payload["installments"][0]["deducted_at"] is not None

    posted = await client.post(
        f"{API}/payroll/runs/{run_id}/post", headers=approver_session.headers
    )
    assert posted.status_code == 200, posted.text
    posted_payload = posted.json()
    assert posted_payload["status"] == "posted"
    assert posted_payload["loan_deduction_total"] == "12000.00"
    assert posted_payload["journal_id"] is not None

    payroll_journal = await client.get(
        f"{API}/accounting/journals/{posted_payload['journal_id']}",
        headers=approver_session.headers,
    )
    assert payroll_journal.status_code == 200, payroll_journal.text
    payroll_journal_payload = payroll_journal.json()["journal"]
    assert payroll_journal_payload["status"] == "posted"
    assert payroll_journal_payload["total_debit"] == payroll_journal_payload["total_credit"]


async def test_payroll_calculation_rejects_unaffordable_loan(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    employee = await create_employee(client, admin_session, base_salary="60000.00")
    employee_id = employee["id"]

    loan_response = await client.post(
        f"{API}/loans",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "employee_id": employee_id,
            "kind": "loan",
            "principal_amount": "90000.00",
            "interest_rate_annual": "0.00",
            "term_months": 1,
            "purpose": "Very large emergency medical costs",
        },
    )
    assert loan_response.status_code == 201, loan_response.text
    loan = loan_response.json()

    await create_member(
        client,
        admin_session,
        email="loan.approver@nexus.co.ke",
        full_name="Loan Approver",
        password="LoanApproval!2026",
        roles=[
            {"role_code": "payroll_manager", "company_id": admin_session.company_id},
            {"role_code": "finance_manager", "company_id": admin_session.company_id},
        ],
    )
    approver_session = await build_session(
        client,
        admin_session.tenant,
        email="loan.approver@nexus.co.ke",
        password="LoanApproval!2026",
    )
    approved = await client.post(
        f"{API}/loans/{loan['id']}/approve",
        headers=approver_session.headers,
        json={"version": loan["version"]},
    )
    assert approved.status_code == 200, approved.text
    approved_payload = approved.json()

    disbursed = await client.post(
        f"{API}/loans/{loan['id']}/disburse",
        headers=approver_session.headers,
        json={
            "version": approved_payload["loan"]["version"],
            "disbursed_on": "2026-01-20",
            "first_due_date": "2026-01-20",
            "reference": "DISB-2026-0003",
        },
    )
    assert disbursed.status_code == 200, disbursed.text
    disbursed_payload = disbursed.json()
    assert disbursed_payload["loan"]["status"] == "disbursed"

    group_id, period_id = await _create_payroll_period(client, admin_session)
    run_response = await client.post(
        f"{API}/payroll/runs",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "pay_group_id": group_id,
            "payroll_period_id": period_id,
        },
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()

    calculated = await client.post(
        f"{API}/payroll/runs/{run['id']}/calculate", headers=admin_session.headers
    )
    assert calculated.status_code == 422
    assert calculated.json()["error"]["code"] == "validation_failed"
