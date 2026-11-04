"""Leave entitlement, approval workflow, holiday and attendance invariants."""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest

from .conftest import AuthenticatedSession, build_session, create_employee, create_member

pytestmark = pytest.mark.postgres
API = "/api/v1"


async def _create_leave_type(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    *,
    code: str = "ANNUAL",
    name: str = "Annual leave",
    entitlement: str = "21.00",
    carryover: str = "5.00",
    requires_balance: bool = True,
) -> dict[str, object]:
    response = await client.post(
        f"{API}/leave/types",
        headers=session.headers,
        json={
            "company_id": session.company_id,
            "code": code,
            "name": name,
            "description": "Paid annual leave",
            "is_paid": True,
            "requires_balance": requires_balance,
            "annual_entitlement_days": entitlement,
            "max_carryover_days": carryover,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_approver(
    client: httpx.AsyncClient, admin: AuthenticatedSession
) -> AuthenticatedSession:
    await create_member(
        client,
        admin,
        email="leave.approver@nexus.co.ke",
        full_name="Leave Approver",
        password="Approve!2026Nexus",
        roles=[{"role_code": "hr_manager", "company_id": admin.company_id}],
    )
    return await build_session(
        client,
        admin.tenant,
        email="leave.approver@nexus.co.ke",
        password="Approve!2026Nexus",
    )


async def _balance(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    *,
    employee_id: str,
    leave_year: int = 2026,
) -> dict[str, object]:
    response = await client.get(
        f"{API}/leave/balances",
        headers=session.headers,
        params={"employee_id": employee_id, "leave_year": leave_year},
    )
    assert response.status_code == 200, response.text
    items = response.json()
    assert len(items) == 1
    return items[0]


async def test_leave_request_approval_creates_attendance_and_moves_balance(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    leave_type = await _create_leave_type(client, admin_session)
    holiday = await client.post(
        f"{API}/leave/holidays",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "holiday_date": "2026-01-05",
            "name": "Company Founders Day",
        },
    )
    assert holiday.status_code == 201, holiday.text
    employee = await create_employee(client, admin_session)
    approver = await _create_approver(client, admin_session)

    initialized = await client.post(
        f"{API}/leave/balances/initialize",
        headers=admin_session.headers,
        json={"employee_id": employee["id"], "leave_year": 2026},
    )
    assert initialized.status_code == 200, initialized.text
    assert initialized.json()[0]["entitled_days"] == "21.00"
    assert initialized.json()[0]["available_days"] == "21.00"

    request_response = await client.post(
        f"{API}/leave/requests",
        headers=admin_session.headers,
        json={
            "employee_id": employee["id"],
            "leave_type_id": leave_type["id"],
            "start_date": "2026-01-05",
            "end_date": "2026-01-09",
            "reason": "Family travel",
        },
    )
    assert request_response.status_code == 201, request_response.text
    leave_request = request_response.json()
    assert leave_request["requested_days"] == "4.00"
    assert leave_request["balance_tracked"] is True
    assert leave_request["status"] == "pending"
    assert leave_request["employee_name"] == "Amina Otieno"
    assert leave_request["requested_by_name"] == "Test Administrator"

    pending_balance = await _balance(client, admin_session, employee_id=employee["id"])
    assert pending_balance["pending_days"] == "4.00"
    assert pending_balance["available_days"] == "17.00"

    self_approval = await client.post(
        f"{API}/leave/requests/{leave_request['id']}/approve",
        headers=admin_session.headers,
        json={"note": "Self approval"},
    )
    assert self_approval.status_code == 409, self_approval.text
    assert "cannot decide" in self_approval.json()["error"]["message"]

    approved_response = await client.post(
        f"{API}/leave/requests/{leave_request['id']}/approve",
        headers=approver.headers,
        json={"note": "Approved after review"},
    )
    assert approved_response.status_code == 200, approved_response.text
    approved = approved_response.json()
    assert approved["status"] == "approved"
    assert approved["decided_by_name"] == "Leave Approver"

    used_balance = await _balance(client, admin_session, employee_id=employee["id"])
    assert used_balance["used_days"] == "4.00"
    assert used_balance["pending_days"] == "0.00"
    assert used_balance["available_days"] == "17.00"

    attendance = await client.get(
        f"{API}/attendance",
        headers=admin_session.headers,
        params={
            "company_id": admin_session.company_id,
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        },
    )
    assert attendance.status_code == 200, attendance.text
    records = attendance.json()["items"]
    assert len(records) == 4
    assert {record["work_date"] for record in records} == {
        "2026-01-06",
        "2026-01-07",
        "2026-01-08",
        "2026-01-09",
    }
    assert all(record["source"] == "leave" for record in records)

    holiday_delete = await client.delete(
        f"{API}/leave/holidays/{holiday.json()['id']}", headers=admin_session.headers
    )
    assert holiday_delete.status_code == 409, holiday_delete.text
    assert "pending or approved leave" in holiday_delete.json()["error"]["message"]

    manual_collision = await client.post(
        f"{API}/attendance",
        headers=admin_session.headers,
        json={
            "employee_id": employee["id"],
            "work_date": "2026-01-06",
            "status": "present",
        },
    )
    assert manual_collision.status_code == 409, manual_collision.text

    rejection = await client.post(
        f"{API}/leave/requests",
        headers=admin_session.headers,
        json={
            "employee_id": employee["id"],
            "leave_type_id": leave_type["id"],
            "start_date": "2026-02-02",
            "end_date": "2026-02-06",
            "reason": "Personal matters",
        },
    )
    assert rejection.status_code == 201
    rejected_response = await client.post(
        f"{API}/leave/requests/{rejection.json()['id']}/reject",
        headers=approver.headers,
        json={"note": "Coverage unavailable"},
    )
    assert rejected_response.status_code == 200
    assert rejected_response.json()["status"] == "rejected"
    after_rejection = await _balance(client, admin_session, employee_id=employee["id"])
    assert after_rejection["pending_days"] == "0.00"
    assert after_rejection["used_days"] == "4.00"

    future = await client.post(
        f"{API}/leave/requests",
        headers=admin_session.headers,
        json={
            "employee_id": employee["id"],
            "leave_type_id": leave_type["id"],
            "start_date": "2026-12-07",
            "end_date": "2026-12-11",
            "reason": "December break",
        },
    )
    assert future.status_code == 201
    future_approved = await client.post(
        f"{API}/leave/requests/{future.json()['id']}/approve",
        headers=approver.headers,
        json={},
    )
    assert future_approved.status_code == 200
    cancelled = await client.post(
        f"{API}/leave/requests/{future.json()['id']}/cancel",
        headers=admin_session.headers,
        json={"note": "Plans changed"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    final_balance = await _balance(client, admin_session, employee_id=employee["id"])
    assert final_balance["used_days"] == "4.00"
    assert final_balance["pending_days"] == "0.00"

    december_attendance = await client.get(
        f"{API}/attendance",
        headers=admin_session.headers,
        params={
            "company_id": admin_session.company_id,
            "start_date": "2026-12-01",
            "end_date": "2026-12-31",
        },
    )
    assert december_attendance.status_code == 200
    assert december_attendance.json()["items"] == []


async def test_leave_request_boundaries_reject_overlap_weekends_and_insufficient_balance(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    leave_type = await _create_leave_type(
        client, admin_session, entitlement="3.00", carryover="0.00"
    )
    employee = await create_employee(client, admin_session)
    initialized = await client.post(
        f"{API}/leave/balances/initialize",
        headers=admin_session.headers,
        json={"employee_id": employee["id"], "leave_year": 2026},
    )
    assert initialized.status_code == 200

    weekend = await client.post(
        f"{API}/leave/requests",
        headers=admin_session.headers,
        json={
            "employee_id": employee["id"],
            "leave_type_id": leave_type["id"],
            "start_date": "2026-01-10",
            "end_date": "2026-01-11",
            "reason": "Weekend break",
        },
    )
    assert weekend.status_code == 422, weekend.text

    cross_year = await client.post(
        f"{API}/leave/requests",
        headers=admin_session.headers,
        json={
            "employee_id": employee["id"],
            "leave_type_id": leave_type["id"],
            "start_date": "2026-12-28",
            "end_date": "2027-01-02",
            "reason": "Cross year break",
        },
    )
    assert cross_year.status_code == 422, cross_year.text

    insufficient = await client.post(
        f"{API}/leave/requests",
        headers=admin_session.headers,
        json={
            "employee_id": employee["id"],
            "leave_type_id": leave_type["id"],
            "start_date": "2026-01-12",
            "end_date": "2026-01-14",
            "reason": "Three days against a three-day balance",
        },
    )
    assert insufficient.status_code == 201, insufficient.text
    assert insufficient.json()["requested_days"] == "3.00"

    overlap = await client.post(
        f"{API}/leave/requests",
        headers=admin_session.headers,
        json={
            "employee_id": employee["id"],
            "leave_type_id": leave_type["id"],
            "start_date": "2026-01-14",
            "end_date": "2026-01-16",
            "reason": "Overlapping request",
        },
    )
    assert overlap.status_code == 409, overlap.text

    exhausted = await client.post(
        f"{API}/leave/requests",
        headers=admin_session.headers,
        json={
            "employee_id": employee["id"],
            "leave_type_id": leave_type["id"],
            "start_date": "2026-01-19",
            "end_date": "2026-01-20",
            "reason": "Two more days",
        },
    )
    assert exhausted.status_code == 409, exhausted.text
    assert "Insufficient" in exhausted.json()["error"]["message"]

    archived = await client.patch(
        f"{API}/leave/types/{leave_type['id']}",
        headers=admin_session.headers,
        json={"status": "archived", "version": leave_type["version"]},
    )
    assert archived.status_code == 409, archived.text
    assert "pending" in archived.json()["error"]["message"]


async def test_attendance_register_and_leave_administration_permissions(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    leave_type = await _create_leave_type(client, admin_session)
    employee = await create_employee(client, admin_session)

    created = await client.post(
        f"{API}/attendance",
        headers=admin_session.headers,
        json={
            "employee_id": employee["id"],
            "work_date": "2026-01-07",
            "status": "present",
            "note": "On site",
        },
    )
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["source"] == "manual"

    duplicate = await client.post(
        f"{API}/attendance",
        headers=admin_session.headers,
        json={
            "employee_id": employee["id"],
            "work_date": "2026-01-07",
            "status": "remote",
        },
    )
    assert duplicate.status_code == 409, duplicate.text

    updated = await client.patch(
        f"{API}/attendance/{record['id']}",
        headers=admin_session.headers,
        json={"status": "remote", "note": "Working remotely", "version": record["version"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "remote"

    listing = await client.get(
        f"{API}/attendance",
        headers=admin_session.headers,
        params={
            "company_id": admin_session.company_id,
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        },
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    await create_member(
        client,
        admin_session,
        email="leave.viewer@nexus.co.ke",
        full_name="Leave Viewer",
        password="Viewer!2026Nexus",
        roles=[{"role_code": "auditor"}],
    )
    viewer = await build_session(
        client,
        admin_session.tenant,
        email="leave.viewer@nexus.co.ke",
        password="Viewer!2026Nexus",
    )
    visible = await client.get(
        f"{API}/leave/types",
        headers=viewer.headers,
        params={"company_id": admin_session.company_id},
    )
    assert visible.status_code == 200
    denied = await client.post(
        f"{API}/leave/types",
        headers=viewer.headers,
        json={
            "company_id": admin_session.company_id,
            "code": "SICK",
            "name": "Sick leave",
            "annual_entitlement_days": "10.00",
            "max_carryover_days": "0.00",
        },
    )
    assert denied.status_code == 403, denied.text

    unknown_approval = await client.post(
        f"{API}/leave/requests/{uuid.uuid4()}/approve",
        headers=admin_session.headers,
        json={},
    )
    assert unknown_approval.status_code == 404, unknown_approval.text

    assert Decimal(leave_type["annual_entitlement_days"]) == Decimal("21.00")


async def test_leave_type_without_balance_tracks_neither_pending_nor_used_days(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    leave_type = await _create_leave_type(
        client,
        admin_session,
        code="UNPAID",
        name="Unpaid leave",
        entitlement="0.00",
        carryover="0.00",
        requires_balance=False,
    )
    employee = await create_employee(client, admin_session)
    approver = await _create_approver(client, admin_session)

    initialized = await client.post(
        f"{API}/leave/balances/initialize",
        headers=admin_session.headers,
        json={"employee_id": employee["id"], "leave_year": 2026},
    )
    assert initialized.status_code == 200, initialized.text
    assert initialized.json() == []

    request_response = await client.post(
        f"{API}/leave/requests",
        headers=admin_session.headers,
        json={
            "employee_id": employee["id"],
            "leave_type_id": leave_type["id"],
            "start_date": "2026-12-02",
            "end_date": "2026-12-03",
            "reason": "Unpaid personal leave",
        },
    )
    assert request_response.status_code == 201, request_response.text
    leave_request = request_response.json()
    assert leave_request["requested_days"] == "2.00"
    assert leave_request["balance_tracked"] is False

    balances = await client.get(
        f"{API}/leave/balances",
        headers=admin_session.headers,
        params={"employee_id": employee["id"], "leave_year": 2026},
    )
    assert balances.status_code == 200
    assert balances.json() == []

    approved = await client.post(
        f"{API}/leave/requests/{leave_request['id']}/approve",
        headers=approver.headers,
        json={},
    )
    assert approved.status_code == 200, approved.text

    attendance = await client.get(
        f"{API}/attendance",
        headers=admin_session.headers,
        params={
            "company_id": admin_session.company_id,
            "start_date": "2026-12-01",
            "end_date": "2026-12-31",
        },
    )
    assert attendance.status_code == 200
    assert {record["work_date"] for record in attendance.json()["items"]} == {
        "2026-12-02",
        "2026-12-03",
    }

    cancelled = await client.post(
        f"{API}/leave/requests/{leave_request['id']}/cancel",
        headers=admin_session.headers,
        json={"note": "No longer required"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    after_cancel = await client.get(
        f"{API}/attendance",
        headers=admin_session.headers,
        params={
            "company_id": admin_session.company_id,
            "start_date": "2026-12-01",
            "end_date": "2026-12-31",
        },
    )
    assert after_cancel.status_code == 200
    assert after_cancel.json()["items"] == []


async def test_carryover_is_capped_into_the_next_leave_year(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    await _create_leave_type(client, admin_session, entitlement="10.00", carryover="2.00")
    employee = await create_employee(client, admin_session)
    first = await client.post(
        f"{API}/leave/balances/initialize",
        headers=admin_session.headers,
        json={"employee_id": employee["id"], "leave_year": 2026},
    )
    assert first.status_code == 200, first.text
    assert first.json()[0]["carried_over_days"] == "0.00"

    second = await client.post(
        f"{API}/leave/balances/initialize",
        headers=admin_session.headers,
        json={"employee_id": employee["id"], "leave_year": 2027},
    )
    assert second.status_code == 200, second.text
    assert second.json()[0]["entitled_days"] == "10.00"
    assert second.json()[0]["carried_over_days"] == "2.00"
    assert second.json()[0]["available_days"] == "12.00"

    third = await client.post(
        f"{API}/leave/balances/initialize",
        headers=admin_session.headers,
        json={"employee_id": employee["id"], "leave_year": 2028},
    )
    assert third.status_code == 200
    assert third.json()[0]["carried_over_days"] == "2.00"
