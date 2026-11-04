"""Cross-tenant and cross-company access boundaries."""

from __future__ import annotations

import httpx
import pytest

from nexus_api.bootstrap import BootstrapRequest, bootstrap_tenant
from nexus_api.database.session import SessionFactory

from .conftest import (
    AuthenticatedSession,
    Tenant,
    build_session,
    create_employee,
    create_member,
)

pytestmark = pytest.mark.postgres
API = "/api/v1"


async def _bootstrap_second_tenant() -> Tenant:
    request = BootstrapRequest(
        organization_slug="nexus-second",
        organization_name="Second Tenant",
        company_code="SECOND",
        company_name="Second Tenant Limited",
        admin_email="admin@second-tenant.co.ke",
        admin_password="SecondAdmin!2026",
        admin_full_name="Second Administrator",
    )
    async with SessionFactory() as session, session.begin():
        organization = await bootstrap_tenant(session, request)
    return Tenant(
        slug=request.organization_slug,
        name=request.organization_name,
        admin_email=request.admin_email,
        admin_password=request.admin_password,
        organization_id=str(organization.id),
    )


async def test_second_tenant_cannot_read_first_tenant_records(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    employee = await create_employee(client, admin_session)
    first_users = await client.get(f"{API}/users", headers=admin_session.headers)
    assert first_users.status_code == 200
    first_membership_id = first_users.json()["items"][0]["id"]

    second_tenant = await _bootstrap_second_tenant()
    second = await build_session(
        client,
        second_tenant,
        email=second_tenant.admin_email,
        password=second_tenant.admin_password,
    )
    assert second.organization_id != admin_session.organization_id

    companies = await client.get(f"{API}/companies", headers=second.headers)
    assert companies.status_code == 200
    assert [company["id"] for company in companies.json()] == [second.company_id]

    employee_read = await client.get(f"{API}/employees/{employee['id']}", headers=second.headers)
    assert employee_read.status_code == 404

    first_org_trial_balance = await client.get(
        f"{API}/accounting/trial-balance",
        headers=second.headers,
        params={"company_id": admin_session.company_id},
    )
    assert first_org_trial_balance.status_code == 404

    cross_tenant_user = await client.get(
        f"{API}/users/{first_membership_id}", headers=second.headers
    )
    assert cross_tenant_user.status_code == 404

    second_users = await client.get(f"{API}/users", headers=second.headers)
    assert second_users.status_code == 200
    assert second_users.json()["total"] == 1
    assert second_users.json()["items"][0]["email"] == second_tenant.admin_email


async def test_company_scoped_member_cannot_access_another_company(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    second_company_response = await client.post(
        f"{API}/companies",
        headers=admin_session.headers,
        json={
            "code": "OTHER",
            "name": "Other Company",
            "legal_name": "Other Company Limited",
            "country_code": "KE",
            "base_currency": "KES",
            "timezone": "Africa/Nairobi",
            "fiscal_year_start_month": 1,
        },
    )
    assert second_company_response.status_code == 201, second_company_response.text

    await create_member(
        client,
        admin_session,
        email="scoped@nexus.co.ke",
        full_name="Scoped Manager",
        password="Scoped!2026Nexus",
        roles=[{"role_code": "payroll_manager", "company_id": admin_session.company_id}],
    )
    scoped = await build_session(
        client,
        admin_session.tenant,
        email="scoped@nexus.co.ke",
        password="Scoped!2026Nexus",
    )

    companies = await client.get(f"{API}/companies", headers=scoped.headers)
    assert companies.status_code == 200
    assert [company["id"] for company in companies.json()] == [admin_session.company_id]

    denied = await client.get(
        f"{API}/employees",
        headers=scoped.headers,
        params={"company_id": second_company_response.json()["id"]},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"


async def test_unauthenticated_clients_cannot_read_tenant_data(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    for path in ("/companies", "/users", "/payroll/rule-sets"):
        response = await client.get(f"{API}{path}")
        assert response.status_code == 401, path
