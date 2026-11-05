"""REST client contracts: authentication rotation and structured failures."""

from __future__ import annotations

import json
from datetime import date

import pytest

from fake_api import COMPANY_ID, FakeApi
from nexus_desktop.api.client import (
    ApiError,
    AuthenticationExpired,
    NexusClient,
)
from nexus_desktop.api.models import TokenPair


def _client(api: FakeApi) -> NexusClient:
    return NexusClient(
        "http://nexus.test/api/v1",
        transport=api.transport(),
    )


def test_login_rotates_on_401_and_revokes_on_logout() -> None:
    api = FakeApi()
    changes: list[TokenPair | None] = []
    client = NexusClient(
        "http://nexus.test/api/v1",
        transport=api.transport(),
        on_tokens_changed=changes.append,
    )
    tokens = client.login("nexus-demo", "ADMIN@NEXUS.CO.KE", "NexusDemo!2026")
    assert tokens.access_token == "access-1"
    assert api.login_calls == 1
    login_body = json.loads(api.requests[0].content)
    assert login_body["email"] == "admin@nexus.co.ke"
    assert login_body["organization_slug"] == "nexus-demo"

    api.reject_access_tokens.add("access-1")
    page = client.list_employees(COMPANY_ID)
    assert page.total == 1
    assert api.refresh_calls == 1
    assert changes[-1] is not None
    assert isinstance(changes[-1], TokenPair)
    assert changes[-1].access_token == "access-2"

    client.logout()
    assert api.revoked is True
    assert client.is_authenticated is False


def test_loan_client_lists_and_reads_repayment_schedule() -> None:
    api = FakeApi()
    client = _client(api)
    client.login("nexus-demo", "admin@nexus.co.ke", "NexusDemo!2026")

    page = client.list_loans(COMPANY_ID)
    assert page.total == 1
    loan = page.items[0]
    assert loan.loan_number == "LN-2026-0001"
    assert loan.employee_name == "Achieng Otieno"

    detail = client.loan_detail(str(loan.id))
    assert detail.loan.status == "disbursed"
    assert len(detail.installments) == 2
    assert detail.installments[0].status == "deducted"
    assert detail.installments[1].status == "scheduled"

    approved = client.loan_action(str(loan.id), "approve", version=loan.version)
    assert approved.loan.status == "approved"
    assert approved.installments == []


def test_benefit_client_manages_plans_and_enrolments() -> None:
    api = FakeApi()
    client = _client(api)
    client.login("nexus-demo", "admin@nexus.co.ke", "NexusDemo!2026")

    plans = client.list_benefit_plans(COMPANY_ID)
    assert len(plans) == 1
    assert plans[0].code == "MED"
    assert plans[0].employee_contribution == 2500
    assert plans[0].employer_contribution == 5000

    enrollments = client.list_benefit_enrollments(COMPANY_ID)
    assert enrollments.total == 1
    enrollment = enrollments.items[0]
    assert enrollment.employee_name == "Achieng Otieno"
    assert enrollment.plan_code == "MED"
    assert enrollment.status == "active"

    created = client.create_benefit_plan(
        company_id=COMPANY_ID,
        code="PENSION",
        name="Staff pension",
        category="pension",
        employee_contribution="3000.00",
        employer_contribution="6000.00",
        description="Voluntary pension scheme",
    )
    assert created.code == "PENSION"

    updated = client.update_benefit_plan(
        str(plans[0].id),
        version=plans[0].version,
        name="Comprehensive medical cover (revised)",
        category="medical",
        employee_contribution="2600.00",
        employer_contribution="5000.00",
        description="Revised scheme",
        status="archived",
    )
    assert updated.status == "archived"
    assert updated.employee_contribution == 2600

    ended = client.end_benefit_enrollment(
        str(enrollment.id),
        version=enrollment.version,
        effective_to=date(2026, 10, 31),
        note="Employee opted out.",
    )
    assert ended.status == "ended"
    assert ended.effective_to == date(2026, 10, 31)
    assert ended.end_note == "Employee opted out."


def test_structured_api_error_envelope_is_preserved() -> None:
    api = FakeApi()
    api.error_overrides["/roles"] = (
        403,
        "permission_denied",
        "You do not have permission to view roles.",
    )
    client = _client(api)
    client.login("nexus-demo", "admin@nexus.co.ke", "NexusDemo!2026")

    with pytest.raises(ApiError) as raised:
        client.list_roles()

    assert raised.value.status_code == 403
    assert raised.value.code == "permission_denied"
    assert raised.value.message == "You do not have permission to view roles."


def test_unrecoverable_session_notifies_once_and_clears_tokens() -> None:
    api = FakeApi()
    api.reject_access_tokens.add("access-1")
    api.reject_refresh_tokens.add("refresh-1")
    notified: list[bool] = []
    client = _client(api)
    client.set_session(
        TokenPair(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_in=900,
            refresh_expires_in=2_592_000,
        )
    )
    client.set_auth_expired_callback(lambda: notified.append(True))

    with pytest.raises(AuthenticationExpired):
        client.list_roles()

    assert notified == [True]
    assert client.is_authenticated is False
