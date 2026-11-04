"""User administration, role scoping and separation-of-duties prerequisites."""

from __future__ import annotations

import httpx
import pytest

from .conftest import (
    AuthenticatedSession,
    build_session,
    create_member,
    login,
)

pytestmark = pytest.mark.postgres
API = "/api/v1"


async def test_admin_can_list_roles_and_create_a_company_scoped_approver(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    roles = await client.get(f"{API}/roles", headers=admin_session.headers)
    assert roles.status_code == 200
    assert {role["code"] for role in roles.json()} == {
        "auditor",
        "finance_manager",
        "hr_manager",
        "org_admin",
        "payroll_manager",
        "payroll_officer",
    }

    member = await create_member(
        client,
        admin_session,
        email="grace.approver@nexus.co.ke",
        full_name="Grace Approver",
        password="Approve!2026Nexus",
        roles=[{"role_code": "payroll_manager", "company_id": admin_session.company_id}],
    )

    assert member["status"] == "active"
    assert member["role_assignments"][0]["role_code"] == "payroll_manager"
    assert member["role_assignments"][0]["company_id"] == admin_session.company_id

    detail = await client.get(f"{API}/users/{member['id']}", headers=admin_session.headers)
    assert detail.status_code == 200
    assert detail.json()["email"] == "grace.approver@nexus.co.ke"

    listing = await client.get(
        f"{API}/users",
        headers=admin_session.headers,
        params={"q": "grace"},
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    approver = await build_session(
        client,
        admin_session.tenant,
        email="grace.approver@nexus.co.ke",
        password="Approve!2026Nexus",
    )
    me = await client.get(f"{API}/auth/me", headers=approver.headers)
    assert "payroll.approve" in me.json()["permissions"]
    assert "user.manage" not in me.json()["permissions"]


async def test_duplicate_email_and_weak_password_are_rejected(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    await create_member(
        client,
        admin_session,
        email="duplicate@nexus.co.ke",
        full_name="Duplicate User",
        password="Duplicate!2026X",
        roles=[{"role_code": "auditor"}],
    )

    duplicate = await client.post(
        f"{API}/users",
        headers=admin_session.headers,
        json={
            "email": "duplicate@nexus.co.ke",
            "full_name": "Another User",
            "password": "Another!2026X",
            "default_company_id": admin_session.company_id,
            "role_assignments": [{"role_code": "auditor"}],
        },
    )
    assert duplicate.status_code == 409

    weak = await client.post(
        f"{API}/users",
        headers=admin_session.headers,
        json={
            "email": "weak@nexus.co.ke",
            "full_name": "Weak Password",
            "password": "alllowercase",
            "default_company_id": admin_session.company_id,
            "role_assignments": [{"role_code": "auditor"}],
        },
    )
    assert weak.status_code == 422
    assert weak.json()["error"]["code"] == "validation_failed"


async def test_unknown_and_foreign_scoped_roles_are_rejected(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    unknown = await client.post(
        f"{API}/users",
        headers=admin_session.headers,
        json={
            "email": "unknown.role@nexus.co.ke",
            "full_name": "Unknown Role",
            "password": "Unknown!2026X",
            "default_company_id": admin_session.company_id,
            "role_assignments": [{"role_code": "not_a_role"}],
        },
    )
    assert unknown.status_code == 422

    foreign = await client.post(
        f"{API}/users",
        headers=admin_session.headers,
        json={
            "email": "foreign.scope@nexus.co.ke",
            "full_name": "Foreign Scope",
            "password": "Foreign!2026X",
            "default_company_id": admin_session.company_id,
            "role_assignments": [
                {
                    "role_code": "payroll_manager",
                    "company_id": "00000000-0000-0000-0000-000000000001",
                }
            ],
        },
    )
    assert foreign.status_code == 422


async def test_role_assignments_can_be_added_and_removed(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    member = await create_member(
        client,
        admin_session,
        email="officer@nexus.co.ke",
        full_name="Payroll Officer",
        password="Officer!2026Nexus",
        roles=[{"role_code": "payroll_officer", "company_id": admin_session.company_id}],
    )
    url = f"{API}/users/{member['id']}/role-assignments"

    added = await client.post(
        url,
        headers=admin_session.headers,
        json={"role_code": "auditor", "company_id": admin_session.company_id},
    )
    assert added.status_code == 201
    assert {assignment["role_code"] for assignment in added.json()["role_assignments"]} == {
        "auditor",
        "payroll_officer",
    }

    duplicate = await client.post(
        url,
        headers=admin_session.headers,
        json={"role_code": "auditor", "company_id": admin_session.company_id},
    )
    assert duplicate.status_code == 409

    auditor_assignment = next(
        assignment
        for assignment in added.json()["role_assignments"]
        if assignment["role_code"] == "auditor"
    )
    removed = await client.delete(
        f"{url}/{auditor_assignment['id']}",
        headers=admin_session.headers,
    )
    assert removed.status_code == 200
    assert [assignment["role_code"] for assignment in removed.json()["role_assignments"]] == [
        "payroll_officer"
    ]


async def test_last_administrator_cannot_be_suspended_or_stripped(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    memberships = await client.get(f"{API}/users", headers=admin_session.headers)
    admin_membership = next(
        item for item in memberships.json()["items"] if item["id"] == admin_session.membership_id
    )
    admin_assignment = next(
        assignment
        for assignment in admin_membership["role_assignments"]
        if assignment["role_code"] == "org_admin"
    )

    self_suspend = await client.patch(
        f"{API}/users/{admin_session.membership_id}",
        headers=admin_session.headers,
        json={"status": "suspended"},
    )
    assert self_suspend.status_code == 409

    remove_last_admin = await client.delete(
        f"{API}/users/{admin_session.membership_id}/role-assignments/{admin_assignment['id']}",
        headers=admin_session.headers,
    )
    assert remove_last_admin.status_code == 409


async def test_suspending_a_member_revokes_access_and_refresh(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    member = await create_member(
        client,
        admin_session,
        email="suspended@nexus.co.ke",
        full_name="Suspended Member",
        password="Suspend!2026X",
        roles=[{"role_code": "auditor"}],
    )
    member_session = await build_session(
        client,
        admin_session.tenant,
        email="suspended@nexus.co.ke",
        password="Suspend!2026X",
    )

    suspended = await client.patch(
        f"{API}/users/{member['id']}",
        headers=admin_session.headers,
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"

    denied = await client.get(f"{API}/auth/me", headers=member_session.headers)
    assert denied.status_code == 401
    refused_refresh = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": member_session.refresh_token}
    )
    assert refused_refresh.status_code == 401

    reactivated = await client.patch(
        f"{API}/users/{member['id']}",
        headers=admin_session.headers,
        json={"status": "active"},
    )
    assert reactivated.status_code == 200
    relogin = await login(
        client,
        tenant_slug=admin_session.tenant.slug,
        email="suspended@nexus.co.ke",
        password="Suspend!2026X",
    )
    assert relogin.status_code == 200


async def test_payroll_manager_cannot_administer_users(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    await create_member(
        client,
        admin_session,
        email="no.admin@nexus.co.ke",
        full_name="No Admin",
        password="NoAdmin!2026X",
        roles=[{"role_code": "payroll_manager", "company_id": admin_session.company_id}],
    )
    member_session = await build_session(
        client,
        admin_session.tenant,
        email="no.admin@nexus.co.ke",
        password="NoAdmin!2026X",
    )

    listing = await client.get(f"{API}/users", headers=member_session.headers)
    roles = await client.get(f"{API}/roles", headers=member_session.headers)

    assert listing.status_code == 403
    assert roles.status_code == 403
