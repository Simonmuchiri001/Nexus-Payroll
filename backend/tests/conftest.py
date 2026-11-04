"""Integration test harness for the authoritative API.

The harness refuses to touch a database whose name does not end in ``_test``.
It rebuilds the public schema from the Alembic migration before the session and
truncates every application table between tests, so tests never inherit state.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.engine import URL, make_url

TEST_DATABASE_URL = os.environ.get(
    "NEXUS_TEST_DATABASE_URL",
    "postgresql+asyncpg://nexus:nexus@127.0.0.1:55432/nexus_test",
)
_parsed = make_url(TEST_DATABASE_URL)
if not _parsed.database or not _parsed.database.endswith("_test"):
    raise RuntimeError(
        "NEXUS_TEST_DATABASE_URL must point to a database whose name ends with '_test'."
    )

os.environ["NEXUS_ENVIRONMENT"] = "test"
os.environ["NEXUS_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["NEXUS_JWT_SECRET"] = "test-secret-4d9e1f72c3b8a56e0d2f7a1c9b4e6d8f"
os.environ["NEXUS_ALLOWED_HOSTS"] = '["testserver","localhost","127.0.0.1"]'
os.environ["NEXUS_MAX_LOGIN_ATTEMPTS"] = "20"
os.environ["NEXUS_LOGIN_WINDOW_SECONDS"] = "60"

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from nexus_api.bootstrap import BootstrapRequest, bootstrap_tenant  # noqa: E402
from nexus_api.database.metadata import Base  # noqa: E402
from nexus_api.database.session import SessionFactory, engine  # noqa: E402
from nexus_api.main import create_app  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Tenant:
    slug: str
    name: str
    admin_email: str
    admin_password: str
    organization_id: str


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    tenant: Tenant
    access_token: str
    refresh_token: str
    organization_id: str
    membership_id: str
    company_id: str
    headers: dict[str, str]


def _sync_url(url: str) -> URL:
    return make_url(url).set(drivername="postgresql+psycopg")


def _run_alembic(
    *arguments: str, search_path: str | None = None
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["NEXUS_DATABASE_URL"] = TEST_DATABASE_URL
    if search_path is None:
        environment.pop("PGOPTIONS", None)
    else:
        environment["PGOPTIONS"] = f"-c search_path={search_path}"
    return subprocess.run(  # noqa: S603 - fixed argument vector, no shell
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_alembic_succeeded(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0:
        raise RuntimeError(
            f"Alembic command failed with exit code {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(scope="session")
def postgres_database() -> None:
    """Rebuild the test database schema from a clean Alembic install."""

    sync_engine = create_engine(_sync_url(TEST_DATABASE_URL), isolation_level="AUTOCOMMIT")
    try:
        with sync_engine.connect() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        sync_engine.dispose()

    result = _run_alembic("upgrade", "head")
    _assert_alembic_succeeded(result)

    with sync_engine.connect() as connection:
        table_count = connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        )
    if not table_count or int(str(table_count)) < 10:
        raise RuntimeError("The migrated test database does not contain the enterprise schema.")


@pytest.fixture
async def clean_database(postgres_database: None) -> None:
    """Remove all application rows so each test starts from a known empty state."""

    table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def client(postgres_database: None) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=30,
    ) as http_client:
        yield http_client


@pytest.fixture
async def tenant(clean_database: None) -> Tenant:
    email = "admin@nexus.co.ke"
    password = "NexusAdmin!2026"
    request = BootstrapRequest(
        organization_slug="nexus-test",
        organization_name="Nexus Test Company",
        company_code="NEXUS",
        company_name="Nexus Test Company Limited",
        admin_email=email,
        admin_password=password,
        admin_full_name="Test Administrator",
    )
    async with SessionFactory() as session, session.begin():
        organization = await bootstrap_tenant(session, request)
    return Tenant(
        slug=request.organization_slug,
        name=request.organization_name,
        admin_email=email,
        admin_password=password,
        organization_id=str(organization.id),
    )


async def login(
    http_client: httpx.AsyncClient,
    *,
    tenant_slug: str,
    email: str,
    password: str,
) -> httpx.Response:
    return await http_client.post(
        "/api/v1/auth/login",
        json={
            "organization_slug": tenant_slug,
            "email": email,
            "password": password,
        },
    )


async def build_session(
    http_client: httpx.AsyncClient,
    tenant_record: Tenant,
    *,
    email: str,
    password: str,
) -> AuthenticatedSession:
    response = await login(
        http_client,
        tenant_slug=tenant_record.slug,
        email=email,
        password=password,
    )
    assert response.status_code == 200, response.text
    tokens = response.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me_response = await http_client.get("/api/v1/auth/me", headers=headers)
    assert me_response.status_code == 200, me_response.text
    me = me_response.json()
    return AuthenticatedSession(
        tenant=tenant_record,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        organization_id=me["organization_id"],
        membership_id=me["membership_id"],
        company_id=me["companies"][0]["id"],
        headers=headers,
    )


@pytest.fixture
async def admin_session(client: httpx.AsyncClient, tenant: Tenant) -> AuthenticatedSession:
    return await build_session(
        client,
        tenant,
        email=tenant.admin_email,
        password=tenant.admin_password,
    )


async def create_member(
    http_client: httpx.AsyncClient,
    admin: AuthenticatedSession,
    *,
    email: str,
    full_name: str,
    password: str,
    roles: list[dict[str, Any]],
    default_company_id: str | None = None,
) -> dict[str, Any]:
    response = await http_client.post(
        "/api/v1/users",
        headers=admin.headers,
        json={
            "email": email,
            "full_name": full_name,
            "password": password,
            "default_company_id": default_company_id or admin.company_id,
            "role_assignments": roles,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_employee(
    http_client: httpx.AsyncClient,
    session: AuthenticatedSession,
    *,
    base_salary: str = "100000.00",
) -> dict[str, Any]:
    response = await http_client.post(
        "/api/v1/employees",
        headers=session.headers,
        json={
            "company_id": session.company_id,
            "first_name": "Amina",
            "last_name": "Otieno",
            "job_title": "Finance Analyst",
            "hire_date": "2026-01-01",
            "work_email": "amina.otieno@nexus.co.ke",
            "initial_compensation": {
                "base_salary": base_salary,
                "housing_allowance": "0.00",
                "transport_allowance": "0.00",
                "other_allowance": "0.00",
                "currency": "KES",
                "effective_from": "2026-01-01",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()
