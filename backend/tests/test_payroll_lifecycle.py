"""End-to-end payroll calculation, segregation of duties and accounting posting."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from nexus_api.accounting.models import Journal
from nexus_api.audit.models import AuditEvent
from nexus_api.database.session import SessionFactory
from nexus_api.payroll.models import PayrollRun

from .conftest import AuthenticatedSession, build_session, create_employee, create_member

pytestmark = pytest.mark.postgres
API = "/api/v1"


async def _create_fixed_period(
    client: httpx.AsyncClient, session: AuthenticatedSession
) -> tuple[str, str]:
    group_response = await client.post(
        f"{API}/payroll/pay-groups",
        headers=session.headers,
        json={
            "company_id": session.company_id,
            "code": "TESTMONTHLY",
            "name": "Test Monthly Payroll",
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


async def test_calculate_approve_finalize_and_post_payroll_is_reproducible(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    group_id, period_id = await _create_fixed_period(client, admin_session)
    employee = await create_employee(client, admin_session)
    assert employee["employee_number"] == "EMP-00001"

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
    assert run["status"] == "draft"
    assert run["run_number"] == "PR-202601-0001"
    assert run["engine_version"] == "KE-1.0.0"

    calculated_response = await client.post(
        f"{API}/payroll/runs/{run_id}/calculate", headers=admin_session.headers
    )
    assert calculated_response.status_code == 200, calculated_response.text
    calculated = calculated_response.json()
    expected_totals = {
        "employee_count": 1,
        "gross_total": "100000.00",
        "taxable_total": "91430.00",
        "paye_total": "19812.35",
        "employee_statutory_total": "28382.35",
        "employer_statutory_total": "5820.00",
        "deductions_total": "28382.35",
        "net_total": "71617.65",
        "employer_cost_total": "105820.00",
    }
    for field, expected in expected_totals.items():
        assert calculated[field] == expected
    assert calculated["status"] == "calculated"

    results = await client.get(
        f"{API}/payroll/runs/{run_id}/results", headers=admin_session.headers
    )
    assert results.status_code == 200
    result = results.json()[0]
    assert result["employee_number"] == "EMP-00001"
    assert result["employee_name"] == "Amina Otieno"
    expected_result = {
        "base_salary": "100000.00",
        "gross": "100000.00",
        "taxable_income": "91430.00",
        "paye": "19812.35",
        "nssf_employee": "4320.00",
        "shif_employee": "2750.00",
        "housing_levy_employee": "1500.00",
        "net_pay": "71617.65",
        "nssf_employer": "4320.00",
        "housing_levy_employer": "1500.00",
        "nita": "0.00",
        "employer_cost": "105820.00",
    }
    for field, expected in expected_result.items():
        assert result[field] == expected

    preparer_approval = await client.post(
        f"{API}/payroll/runs/{run_id}/approve", headers=admin_session.headers
    )
    assert preparer_approval.status_code == 409
    assert "separation of duties" in preparer_approval.json()["error"]["message"].lower()

    await create_member(
        client,
        admin_session,
        email="payroll.approver@nexus.co.ke",
        full_name="Payroll Approver",
        password="Approval!2026Nexus",
        roles=[{"role_code": "payroll_manager", "company_id": admin_session.company_id}],
    )
    approver = await build_session(
        client,
        admin_session.tenant,
        email="payroll.approver@nexus.co.ke",
        password="Approval!2026Nexus",
    )

    approved = await client.post(f"{API}/payroll/runs/{run_id}/approve", headers=approver.headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] != approved.json()["prepared_by"]

    immutable = await client.post(
        f"{API}/payroll/runs/{run_id}/calculate", headers=admin_session.headers
    )
    assert immutable.status_code == 409
    assert "immutable" in immutable.json()["error"]["message"].lower()

    finalized = await client.post(f"{API}/payroll/runs/{run_id}/finalize", headers=approver.headers)
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "finalized"

    posted = await client.post(f"{API}/payroll/runs/{run_id}/post", headers=approver.headers)
    assert posted.status_code == 200, posted.text
    posted_payload = posted.json()
    assert posted_payload["status"] == "posted"
    journal_id = posted_payload["journal_id"]
    assert journal_id

    reposted = await client.post(f"{API}/payroll/runs/{run_id}/post", headers=approver.headers)
    assert reposted.status_code == 200
    assert reposted.json()["journal_id"] == journal_id

    journal_response = await client.get(
        f"{API}/accounting/journals/{journal_id}", headers=approver.headers
    )
    assert journal_response.status_code == 200
    journal = journal_response.json()
    assert journal["journal"]["status"] == "posted"
    assert journal["journal"]["source_type"] == "payroll_run"
    assert journal["journal"]["source_id"] == run_id
    assert journal["journal"]["total_debit"] == "105820.00"
    assert journal["journal"]["total_credit"] == "105820.00"
    assert sum(Decimal(line["debit"]) for line in journal["lines"]) == Decimal("105820.00")
    assert sum(Decimal(line["credit"]) for line in journal["lines"]) == Decimal("105820.00")

    before_payment = await client.get(
        f"{API}/accounting/trial-balance",
        headers=approver.headers,
        params={"company_id": admin_session.company_id, "as_of": "2026-02-08"},
    )
    assert before_payment.status_code == 200
    assert before_payment.json()["total_debit"] == "0.00"
    assert before_payment.json()["total_credit"] == "0.00"
    assert before_payment.json()["balanced"] is True

    trial_balance = await client.get(
        f"{API}/accounting/trial-balance",
        headers=approver.headers,
        params={"company_id": admin_session.company_id, "as_of": "2026-02-28"},
    )
    assert trial_balance.status_code == 200
    balance = trial_balance.json()
    assert balance["balanced"] is True
    assert balance["total_debit"] == "105820.00"
    assert balance["total_credit"] == "105820.00"
    balances = {row["code"]: row for row in balance["rows"]}
    assert balances["5100"]["debit"] == "100000.00"
    assert balances["5200"]["debit"] == "5820.00"
    assert balances["2100"]["credit"] == "19812.35"
    assert balances["2110"]["credit"] == "8640.00"
    assert balances["2120"]["credit"] == "2750.00"
    assert balances["2130"]["credit"] == "3000.00"
    assert balances["2200"]["credit"] == "71617.65"

    async with SessionFactory() as database_session:
        stored_run = await database_session.get(PayrollRun, run_id)
        assert stored_run is not None
        assert stored_run.status == "posted"
        assert stored_run.rules_snapshot["version"] == "KE-2026.1"
        assert stored_run.rules_snapshot["checksum"] == (
            "9fb9c69a1e2e97fb78157efe679c8fea8656b17fb43ed1aa467e052b87b8a219"
        )
        journal_count = await database_session.scalar(
            select(func.count(Journal.id)).where(
                Journal.source_type == "payroll_run",
                Journal.source_id == run_id,
            )
        )
        assert journal_count == 1

        approval_events = (
            await database_session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.entity_id == run_id,
                    AuditEvent.action.in_(
                        [
                            "payroll_run.created",
                            "payroll_run.calculated",
                            "payroll_run.approved",
                            "payroll_run.finalized",
                            "payroll_run.posted",
                        ]
                    ),
                )
                .order_by(AuditEvent.created_at)
            )
        ).all()
        assert [event.action for event in approval_events] == [
            "payroll_run.created",
            "payroll_run.calculated",
            "payroll_run.approved",
            "payroll_run.finalized",
            "payroll_run.posted",
        ]
        preparer = str(calculated["prepared_by"])
        approver_id = str(approved.json()["approved_by"])
        assert preparer != approver_id
        actions = {event.action: event for event in approval_events}
        assert str(actions["payroll_run.calculated"].actor_user_id) == preparer
        assert str(actions["payroll_run.approved"].actor_user_id) == approver_id
        assert str(actions["payroll_run.finalized"].actor_user_id) == approver_id
