"""Payroll attendance summary and unpaid deduction coverage."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from .conftest import AuthenticatedSession, build_session, create_employee, create_member

pytestmark = pytest.mark.postgres

API = "/api/v1"


async def _payroll_setup(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    *,
    period_start: str = "2026-01-01",
    period_end: str = "2026-01-31",
    payment_date: str = "2026-02-09",
) -> tuple[str, str]:
    group = await client.post(
        f"{API}/payroll/pay-groups",
        headers=session.headers,
        json={
            "company_id": session.company_id,
            "code": "ATTEND",
            "name": "Attendance Payroll",
            "currency": "KES",
        },
    )
    assert group.status_code == 201, group.text
    group_id = group.json()["id"]
    period = await client.post(
        f"{API}/payroll/periods",
        headers=session.headers,
        json={
            "pay_group_id": group_id,
            "period_start": period_start,
            "period_end": period_end,
            "payment_date": payment_date,
        },
    )
    assert period.status_code == 201, period.text
    return group_id, period.json()["id"]


async def _calculate(
    client: httpx.AsyncClient, session: AuthenticatedSession, group_id: str, period_id: str
):
    from sqlalchemy import select

    from nexus_api.database.session import SessionFactory
    from nexus_api.payroll.models import PayrollResult, PayslipLine

    run = await client.post(
        f"{API}/payroll/runs",
        headers=session.headers,
        json={
            "company_id": session.company_id,
            "pay_group_id": group_id,
            "payroll_period_id": period_id,
        },
    )
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]
    calculated = await client.post(
        f"{API}/payroll/runs/{run_id}/calculate", headers=session.headers
    )
    assert calculated.status_code == 200, calculated.text
    results = await client.get(f"{API}/payroll/runs/{run_id}/results", headers=session.headers)
    assert results.status_code == 200, results.text
    assert len(results.json()) == 1
    result = results.json()[0]
    async with SessionFactory() as db_session:
        stored = (
            await db_session.scalars(
                select(PayrollResult).where(PayrollResult.payroll_run_id == run_id)
            )
        ).first()
        assert stored is not None
        result["calculation_snapshot"] = dict(stored.calculation_snapshot)
        lines = (
            await db_session.scalars(
                select(PayslipLine)
                .where(PayslipLine.payroll_result_id == stored.id)
                .order_by(PayslipLine.sequence)
            )
        ).all()
        result["payslip_lines"] = [
            {"code": line.code, "amount": str(line.amount)} for line in lines
        ]
    return run_id, calculated.json(), result


async def _leave_type(
    client: httpx.AsyncClient, session: AuthenticatedSession, *, code: str, is_paid: bool
):
    response = await client.post(
        f"{API}/leave/types",
        headers=session.headers,
        json={
            "company_id": session.company_id,
            "code": code,
            "name": f"{code} leave",
            "is_paid": is_paid,
            "requires_balance": False,
            "annual_entitlement_days": "30.00",
            "max_carryover_days": "0.00",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _approver(client: httpx.AsyncClient, admin: AuthenticatedSession):
    await create_member(
        client,
        admin,
        email="att.approver@nexus.co.ke",
        full_name="Attendance Approver",
        password="Approve!2026Nexus",
        roles=[{"role_code": "hr_manager", "company_id": admin.company_id}],
    )
    return await build_session(
        client,
        admin.tenant,
        email="att.approver@nexus.co.ke",
        password="Approve!2026Nexus",
    )


async def _approve_unpaid_leave(
    client: httpx.AsyncClient,
    admin: AuthenticatedSession,
    approver: AuthenticatedSession,
    *,
    employee_id: str,
    leave_type_id: str,
    start: str,
    end: str,
):
    created = await client.post(
        f"{API}/leave/requests",
        headers=admin.headers,
        json={
            "employee_id": employee_id,
            "leave_type_id": leave_type_id,
            "start_date": start,
            "end_date": end,
            "reason": "Unpaid time off for test",
        },
    )
    assert created.status_code == 201, created.text
    request = created.json()
    approved = await client.post(
        f"{API}/leave/requests/{request['id']}/approve",
        headers=approver.headers,
        json={},
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


async def _manual_attendance(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    *,
    employee_id: str,
    day: str,
    status: str,
):
    response = await client.post(
        f"{API}/attendance",
        headers=session.headers,
        json={"employee_id": employee_id, "work_date": day, "status": status},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_unpaid_leave_reduces_gross_and_statutory_on_payable(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    group_id, period_id = await _payroll_setup(client, admin_session)
    employee = await create_employee(client, admin_session, base_salary="31000.00")
    approver = await _approver(client, admin_session)
    unpaid_type = await _leave_type(client, admin_session, code="UNPAID", is_paid=False)

    # Fri 2026-01-02 (working day) unpaid; Mon 2026-01-05 manual absent.
    await _approve_unpaid_leave(
        client,
        admin_session,
        approver,
        employee_id=employee["id"],
        leave_type_id=unpaid_type["id"],
        start="2026-01-02",
        end="2026-01-02",
    )
    await _manual_attendance(
        client, admin_session, employee_id=employee["id"], day="2026-01-05", status="absent"
    )

    _run_id, _calculated, result = await _calculate(client, admin_session, group_id, period_id)

    # Jan 2026: 31 calendar days, 22 working days (Mon-Fri, no holidays).
    assert result["days_in_period"] == 31
    assert result["working_days"] == 22
    assert result["unpaid_days"] == 2
    assert result["paid_days"] == 20
    # daily = 31000/31 = 1000.00; 2 days => 2000.00
    assert Decimal(str(result["unpaid_deduction"])) == Decimal("2000.00")
    assert Decimal(str(result["gross"])) == Decimal("29000.00")

    snapshot = result["calculation_snapshot"]
    assert snapshot["attendance"]["unpaid_days"] == 2
    assert snapshot["attendance"]["paid_days"] == 20
    assert snapshot["calendar"]["working_days"] == 22
    assert "unpaid" in snapshot

    codes = {line["code"] for line in result["payslip_lines"]}
    assert "UNPAID" in codes
    unpaid_line = next(line for line in result["payslip_lines"] if line["code"] == "UNPAID")
    assert Decimal(str(unpaid_line["amount"])) == Decimal("2000.00")


async def test_absent_and_unpaid_cap_at_gross(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    group_id, period_id = await _payroll_setup(client, admin_session)
    employee = await create_employee(client, admin_session, base_salary="3100.00")
    # Mark every working weekday in Jan 2026 absent via direct attendance.
    # The API requires one call per day; cover all Mon-Fri.
    from datetime import timedelta

    cursor = date(2026, 1, 1)
    while cursor <= date(2026, 1, 31):
        if cursor.weekday() < 5:
            await _manual_attendance(
                client,
                admin_session,
                employee_id=employee["id"],
                day=cursor.isoformat(),
                status="absent",
            )
        cursor += timedelta(days=1)

    _run_id, _calculated, result = await _calculate(client, admin_session, group_id, period_id)
    assert result["unpaid_days"] == result["working_days"] == 22
    assert Decimal(str(result["unpaid_deduction"])) <= Decimal("3100.00")
    # Calendar proration: 22 working days at 100/day = 2200 unpaid; the 9
    # weekend days remain payable, so gross is 900 rather than zero.
    assert Decimal(str(result["unpaid_deduction"])) == Decimal("2200.00")
    assert Decimal(str(result["gross"])) == Decimal("900.00")
    assert Decimal(str(result["net_pay"])) >= Decimal("0.00")


async def test_paid_leave_and_missing_attendance_do_not_deduct(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    group_id, period_id = await _payroll_setup(client, admin_session)
    employee = await create_employee(client, admin_session, base_salary="31000.00")
    approver = await _approver(client, admin_session)
    paid_type = await _leave_type(client, admin_session, code="PAID", is_paid=True)

    await _approve_unpaid_leave(
        client,
        admin_session,
        approver,
        employee_id=employee["id"],
        leave_type_id=paid_type["id"],
        start="2026-01-02",
        end="2026-01-02",
    )
    # No other attendance rows: missing = paid.
    _run_id, _calculated, result = await _calculate(client, admin_session, group_id, period_id)
    assert result["unpaid_days"] == 0
    assert Decimal(str(result["unpaid_deduction"])) == Decimal("0.00")
    assert Decimal(str(result["gross"])) == Decimal("31000.00")


async def test_attendance_snapshot_is_stored_and_run_is_immutable(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    group_id, period_id = await _payroll_setup(client, admin_session)
    employee = await create_employee(client, admin_session, base_salary="31000.00")
    await _manual_attendance(
        client, admin_session, employee_id=employee["id"], day="2026-01-02", status="absent"
    )

    run_id, calculated, result = await _calculate(client, admin_session, group_id, period_id)
    assert calculated["unpaid_deduction_total"] == "1000.00"
    assert result["calculation_snapshot"]["attendance"]["unpaid_days"] == 1

    # Approve the run, then change attendance; the stored result must not move.
    # The preparer is admin; approver is a different member.
    await create_member(
        client,
        admin_session,
        email="pay.approver@nexus.co.ke",
        full_name="Payroll Approver",
        password="Approve!2026Nexus",
        roles=[{"role_code": "payroll_manager", "company_id": admin_session.company_id}],
    )
    pay_approver = await build_session(
        client,
        admin_session.tenant,
        email="pay.approver@nexus.co.ke",
        password="Approve!2026Nexus",
    )
    approved = await client.post(
        f"{API}/payroll/runs/{run_id}/approve", headers=pay_approver.headers
    )
    assert approved.status_code == 200, approved.text

    listing = await client.get(
        f"{API}/attendance",
        headers=admin_session.headers,
        params={
            "company_id": admin_session.company_id,
            "start_date": "2026-01-05",
            "end_date": "2026-01-05",
        },
    )
    assert listing.status_code == 200, listing.text
    # Add another absent day AFTER approval; re-fetch results must be unchanged.
    await _manual_attendance(
        client, admin_session, employee_id=employee["id"], day="2026-01-05", status="absent"
    )
    again = await client.get(f"{API}/payroll/runs/{run_id}/results", headers=admin_session.headers)
    assert again.status_code == 200, again.text
    assert Decimal(str(again.json()[0]["unpaid_deduction"])) == Decimal("1000.00")
    assert Decimal(str(again.json()[0]["net_pay"])) == Decimal(str(result["net_pay"]))


async def test_unpaid_deduction_is_zero_without_attendance(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    group_id, period_id = await _payroll_setup(client, admin_session)
    await create_employee(client, admin_session, base_salary="31000.00")
    _run_id, calculated, result = await _calculate(client, admin_session, group_id, period_id)
    assert result["unpaid_days"] == 0
    assert result["unpaid_deduction"] == "0.00"
    assert result["working_days"] == 22
    assert result["paid_days"] == 22
    assert calculated["unpaid_deduction_total"] == "0.00"
    assert all(line["code"] != "UNPAID" for line in result["payslip_lines"])
