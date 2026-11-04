"""Authentication, current-session and token-rotation API tests."""

from __future__ import annotations

import httpx
import pytest

from .conftest import AuthenticatedSession, Tenant, login

pytestmark = pytest.mark.postgres
API = "/api/v1"


async def test_health_endpoints_report_database_readiness(client: httpx.AsyncClient) -> None:
    live = await client.get(f"{API}/health/live")
    ready = await client.get(f"{API}/health/ready")

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "database": True, "version": "0.1.0"}


async def test_login_me_and_logout(client: httpx.AsyncClient, tenant: Tenant) -> None:
    response = await login(
        client,
        tenant_slug=tenant.slug,
        email=tenant.admin_email,
        password=tenant.admin_password,
    )

    assert response.status_code == 200
    tokens = response.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 900
    assert len(tokens["refresh_token"]) >= 32

    me = await client.get(
        f"{API}/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    payload = me.json()
    assert payload["user"]["email"] == tenant.admin_email
    assert payload["organization_id"] == tenant.organization_id
    assert "payroll.approve" in payload["permissions"]
    assert len(payload["companies"]) == 1

    logout = await client.post(
        f"{API}/auth/logout",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert logout.status_code == 200

    refresh = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 401


async def test_invalid_credentials_use_uniform_error_envelope(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    response = await login(
        client,
        tenant_slug=tenant.slug,
        email=tenant.admin_email,
        password="WrongPassword!2026",
    )

    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "authentication_required"
    assert error["message"] == "Invalid organisation, email or password."
    assert response.json()["request_id"]


async def test_reserved_or_malformed_email_is_rejected(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    response = await login(
        client,
        tenant_slug=tenant.slug,
        email="admin@nexus.local",
        password=tenant.admin_password,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


async def test_refresh_rotation_detects_reuse_and_revokes_family(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    first_login = await login(
        client,
        tenant_slug=tenant.slug,
        email=tenant.admin_email,
        password=tenant.admin_password,
    )
    original = first_login.json()["refresh_token"]

    rotated = await client.post(f"{API}/auth/refresh", json={"refresh_token": original})
    assert rotated.status_code == 200
    replacement = rotated.json()["refresh_token"]

    reused = await client.post(f"{API}/auth/refresh", json={"refresh_token": original})
    assert reused.status_code == 401
    assert "reuse" in reused.json()["error"]["message"].lower()

    family_revoked = await client.post(f"{API}/auth/refresh", json={"refresh_token": replacement})
    assert family_revoked.status_code == 401


async def test_protected_route_requires_a_bearer_token(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{API}/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


async def test_session_reports_accessible_company(
    client: httpx.AsyncClient, tenant: Tenant, admin_session: AuthenticatedSession
) -> None:
    assert admin_session.organization_id == tenant.organization_id
    me = await client.get(f"{API}/auth/me", headers=admin_session.headers)

    assert me.status_code == 200
    company = me.json()["companies"][0]
    assert company["id"] == admin_session.company_id
    assert company["base_currency"] == "KES"
    assert company["country_code"] == "KE"


def test_status_codes_are_documented() -> None:
    """Guard against accidental changes to the public error contract."""

    from nexus_api.core.errors import (
        AuthenticationError,
        ConflictError,
        NotFoundError,
        PermissionDeniedError,
        ValidationFailureError,
    )

    assert AuthenticationError.status_code == 401
    assert PermissionDeniedError.status_code == 403
    assert NotFoundError.status_code == 404
    assert ConflictError.status_code == 409
    assert ValidationFailureError.status_code == 422
