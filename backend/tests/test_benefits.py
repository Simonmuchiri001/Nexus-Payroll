"""Benefit plan, enrolment and payroll contribution coverage."""

from __future__ import annotations

import httpx
import pytest

from .conftest import AuthenticatedSession, build_session, create_employee, create_member

API = "/api/v1"

pytestmark = pytest.mark.postgres


async def _create_plan(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    *,
    code: str = "MED",
    employee_contribution: str = "2000.00",
    employer_contribution: str = "5000.00",
) -> dict[str, object]:
    response = await client.post(
        f"{API}/benefits/plans",
        headers=session.headers,
        json={
            "company_id": session.company_id,
            "code": code,
            "name": "Comprehensive medical cover",
            "category": "medical",
            "employee_contribution": employee_contribution,
            "employer_contribution": employer_contribution,
            "description": "Voluntary staff medical scheme",
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _enroll(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    *,
    employee_id: str,
    plan_id: str,
    effective_from: str,
) -> dict[str, object]:
    response = await client.post(
        f"{API}/benefits/enrollments",
        headers=session.headers,
        json={
            "company_id": session.company_id,
            "employee_id": employee_id,
            "plan_id": plan_id,
            "effective_from": effective_from,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _create_period(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    *,
    code: str,
    start: str,
    end: str,
    payment: str,
) -> tuple[str, str]:
    group_response = await client.post(
        f"{API}/payroll/pay-groups",
        headers=session.headers,
        json={
            "company_id": session.company_id,
            "code": code,
            "name": "Benefit Monthly Payroll",
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
            "period_start": start,
            "period_end": end,
            "payment_date": payment,
        },
    )
    assert period_response.status_code == 201, period_response.text
    return group_id, period_response.json()["id"]


async def _create_run(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    *,
    group_id: str,
    period_id: str,
) -> dict[str, object]:
    response = await client.post(
        f"{API}/payroll/runs",
        headers=session.headers,
        json={
            "company_id": session.company_id,
            "pay_group_id": group_id,
            "payroll_period_id": period_id,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _approver_session(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> AuthenticatedSession:
    await create_member(
        client,
        admin_session,
        email="benefit.approver@nexus.co.ke",
        full_name="Benefit Approver",
        password="BenefitApproval!2026",
        roles=[
            {"role_code": "payroll_manager", "company_id": admin_session.company_id},
            {"role_code": "finance_manager", "company_id": admin_session.company_id},
        ],
    )
    return await build_session(
        client,
        admin_session.tenant,
        email="benefit.approver@nexus.co.ke",
        password="BenefitApproval!2026",
    )


async def test_benefit_enrolment_deduction_and_balanced_posting(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    employee = await create_employee(client, admin_session)
    plan = await _create_plan(client, admin_session)
    enrollment = await _enroll(
        client,
        admin_session,
        employee_id=employee["id"],
        plan_id=str(plan["id"]),
        effective_from="2026-01-01",
    )
    assert enrollment["status"] == "active"
    assert enrollment["employee_contribution"] == "2000.00"
    assert enrollment["employer_contribution"] == "5000.00"
    assert enrollment["plan_code"] == "MED"
    assert enrollment["employee_name"] == "Amina Otieno"

    duplicate = await client.post(
        f"{API}/benefits/enrollments",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "employee_id": employee["id"],
            "plan_id": plan["id"],
            "effective_from": "2026-02-01",
        },
    )
    assert duplicate.status_code == 409

    archive_blocked = await client.patch(
        f"{API}/benefits/plans/{plan['id']}",
        headers=admin_session.headers,
        json={"status": "archived", "version": plan["version"]},
    )
    assert archive_blocked.status_code == 409
    assert "active enrolments" in archive_blocked.json()["error"]["message"].lower()

    group_id, period_id = await _create_period(
        client,
        admin_session,
        code="BENEFITMONTHLY",
        start="2026-01-01",
        end="2026-01-31",
        payment="2026-02-09",
    )
    run = await _create_run(client, admin_session, group_id=group_id, period_id=period_id)
    calculated = await client.post(
        f"{API}/payroll/runs/{run['id']}/calculate", headers=admin_session.headers
    )
    assert calculated.status_code == 200, calculated.text
    calculated_payload = calculated.json()
    assert calculated_payload["net_total"] == "69617.65"
    assert calculated_payload["deductions_total"] == "30382.35"
    assert calculated_payload["benefit_employee_total"] == "2000.00"
    assert calculated_payload["benefit_employer_total"] == "5000.00"
    assert calculated_payload["employer_cost_total"] == "110820.00"

    results = await client.get(
        f"{API}/payroll/runs/{run['id']}/results", headers=admin_session.headers
    )
    assert results.status_code == 200
    result = results.json()[0]
    assert result["benefit_employee_deduction"] == "2000.00"
    assert result["benefit_employer_contribution"] == "5000.00"
    assert result["net_pay"] == "69617.65"
    assert result["employer_cost"] == "110820.00"

    approver = await _approver_session(client, admin_session)
    approved = await client.post(
        f"{API}/payroll/runs/{run['id']}/approve", headers=approver.headers
    )
    assert approved.status_code == 200, approved.text
    finalized = await client.post(
        f"{API}/payroll/runs/{run['id']}/finalize", headers=approver.headers
    )
    assert finalized.status_code == 200, finalized.text
    posted = await client.post(f"{API}/payroll/runs/{run['id']}/post", headers=approver.headers)
    assert posted.status_code == 200, posted.text
    posted_payload = posted.json()
    assert posted_payload["status"] == "posted"
    assert posted_payload["benefit_employee_total"] == "2000.00"
    assert posted_payload["benefit_employer_total"] == "5000.00"

    journal_response = await client.get(
        f"{API}/accounting/journals/{posted_payload['journal_id']}",
        headers=approver.headers,
    )
    assert journal_response.status_code == 200, journal_response.text
    journal = journal_response.json()["journal"]
    assert journal["total_debit"] == "110820.00"
    assert journal["total_credit"] == "110820.00"
    lines = journal_response.json()["lines"]
    benefit_expense = next(
        line for line in lines if line["description"] == "Employer benefit contributions"
    )
    benefit_payable = next(
        line for line in lines if line["description"] == "Employee benefits payable"
    )
    assert benefit_expense["debit"] == "5000.00"
    assert benefit_expense["credit"] == "0.00"
    assert benefit_payable["debit"] == "0.00"
    assert benefit_payable["credit"] == "7000.00"


async def test_benefit_proration_and_ended_enrolment_excluded(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    employee = await create_employee(client, admin_session)
    plan = await _create_plan(
        client,
        admin_session,
        code="PENSION",
        employee_contribution="3100.00",
        employer_contribution="6200.00",
    )
    enrollment = await _enroll(
        client,
        admin_session,
        employee_id=employee["id"],
        plan_id=str(plan["id"]),
        effective_from="2026-01-16",
    )

    group_id, period_id = await _create_period(
        client,
        admin_session,
        code="PRORATED",
        start="2026-01-01",
        end="2026-01-31",
        payment="2026-02-09",
    )
    run = await _create_run(client, admin_session, group_id=group_id, period_id=period_id)
    calculated = await client.post(
        f"{API}/payroll/runs/{run['id']}/calculate", headers=admin_session.headers
    )
    assert calculated.status_code == 200, calculated.text
    payload = calculated.json()
    assert payload["benefit_employee_total"] == "1600.00"
    assert payload["benefit_employer_total"] == "3200.00"
    assert payload["net_total"] == "70017.65"
    assert payload["deductions_total"] == "29982.35"
    assert payload["employer_cost_total"] == "109020.00"

    ended = await client.post(
        f"{API}/benefits/enrollments/{enrollment['id']}/end",
        headers=admin_session.headers,
        json={
            "effective_to": "2026-01-31",
            "version": enrollment["version"],
            "note": "Employee opted out after the January cycle.",
        },
    )
    assert ended.status_code == 200, ended.text
    ended_payload = ended.json()
    assert ended_payload["status"] == "ended"
    assert ended_payload["effective_to"] == "2026-01-31"
    assert ended_payload["ended_by"] is not None
    assert ended_payload["end_note"] == "Employee opted out after the January cycle."

    # Ending the enrolment must not retroactively erase contributions for the
    # January period it still overlaps.
    recalculated = await client.post(
        f"{API}/payroll/runs/{run['id']}/calculate", headers=admin_session.headers
    )
    assert recalculated.status_code == 200, recalculated.text
    recalculated_payload = recalculated.json()
    assert recalculated_payload["benefit_employee_total"] == "1600.00"
    assert recalculated_payload["benefit_employer_total"] == "3200.00"
    assert recalculated_payload["net_total"] == "70017.65"

    archived = await client.patch(
        f"{API}/benefits/plans/{plan['id']}",
        headers=admin_session.headers,
        json={"status": "archived", "version": plan["version"]},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"

    # Archiving a plan after its enrolments end must not rewrite history for a
    # period that the enrolment actually covered.
    after_archive = await client.post(
        f"{API}/payroll/runs/{run['id']}/calculate", headers=admin_session.headers
    )
    assert after_archive.status_code == 200, after_archive.text
    assert after_archive.json()["benefit_employee_total"] == "1600.00"
    assert after_archive.json()["benefit_employer_total"] == "3200.00"

    february_response = await client.post(
        f"{API}/payroll/periods",
        headers=admin_session.headers,
        json={
            "pay_group_id": group_id,
            "period_start": "2026-02-01",
            "period_end": "2026-02-28",
            "payment_date": "2026-03-09",
        },
    )
    assert february_response.status_code == 201, february_response.text
    february_run = await _create_run(
        client,
        admin_session,
        group_id=group_id,
        period_id=february_response.json()["id"],
    )
    february_calculated = await client.post(
        f"{API}/payroll/runs/{february_run['id']}/calculate",
        headers=admin_session.headers,
    )
    assert february_calculated.status_code == 200, february_calculated.text
    february_payload = february_calculated.json()
    assert february_payload["benefit_employee_total"] == "0.00"
    assert february_payload["benefit_employer_total"] == "0.00"
    assert february_payload["net_total"] == "71617.65"


async def test_benefit_permissions_boundary(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    await create_member(
        client,
        admin_session,
        email="benefit.hr@nexus.co.ke",
        full_name="Benefit HR Manager",
        password="BenefitHuman!2026",
        roles=[{"role_code": "hr_manager", "company_id": admin_session.company_id}],
    )
    hr_session = await build_session(
        client,
        admin_session.tenant,
        email="benefit.hr@nexus.co.ke",
        password="BenefitHuman!2026",
    )
    await create_member(
        client,
        admin_session,
        email="benefit.officer@nexus.co.ke",
        full_name="Benefit Payroll Officer",
        password="BenefitOfficer!2026",
        roles=[{"role_code": "payroll_officer", "company_id": admin_session.company_id}],
    )
    officer_session = await build_session(
        client,
        admin_session.tenant,
        email="benefit.officer@nexus.co.ke",
        password="BenefitOfficer!2026",
    )

    visible = await client.get(
        f"{API}/benefits/plans",
        headers=officer_session.headers,
        params={"company_id": admin_session.company_id},
    )
    assert visible.status_code == 200, visible.text
    assert visible.json() == []

    forbidden = await client.post(
        f"{API}/benefits/plans",
        headers=officer_session.headers,
        json={
            "company_id": admin_session.company_id,
            "code": "DENIED",
            "name": "Denied plan",
            "category": "other",
            "employee_contribution": "100.00",
            "employer_contribution": "0.00",
        },
    )
    assert forbidden.status_code == 403

    created = await _create_plan(client, hr_session, code="HRPLAN")
    assert created["code"] == "HRPLAN"
    updated = await client.patch(
        f"{API}/benefits/plans/{created['id']}",
        headers=hr_session.headers,
        json={
            "name": "Comprehensive medical cover (revised)",
            "employee_contribution": "2500.00",
            "version": created["version"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["employee_contribution"] == "2500.00"
    assert updated.json()["employer_contribution"] == "5000.00"
