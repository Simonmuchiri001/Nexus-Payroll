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


def test_expense_client_manages_categories_claims_and_reimbursement() -> None:
    api = FakeApi()
    client = _client(api)
    client.login("nexus-demo", "admin@nexus.co.ke", "NexusDemo!2026")

    categories = client.list_expense_categories(COMPANY_ID)
    assert len(categories) == 1
    category = categories[0]
    assert category.code == "TRAVEL"
    assert category.expense_account_code == "5000"

    claims = client.list_expense_claims(COMPANY_ID)
    assert claims.total == 1
    claim = claims.items[0]
    assert claim.claim_number == "EXP-2026-0001"
    assert claim.status == "draft"
    assert claim.receipt_merchant == "Nairobi Shuttle Co."

    created_category = client.create_expense_category(
        company_id=COMPANY_ID,
        code="OFFICE",
        name="Office supplies",
        expense_account_id=str(category.expense_account_id),
        description="Stationery and supplies",
    )
    assert created_category.code == "OFFICE"

    created_claim = client.create_expense_claim(
        company_id=COMPANY_ID,
        employee_id=str(claim.employee_id),
        category_id=str(category.id),
        expense_date=date(2026, 11, 3),
        description="Client workshop transport",
        amount="2750.00",
        receipt_number="RCT-2026-1103",
        receipt_date=date(2026, 11, 3),
        receipt_merchant="Nairobi Shuttle Co.",
    )
    assert created_claim.amount == 2750

    updated_claim = client.update_expense_claim(
        str(claim.id),
        version=claim.version,
        employee_id=str(claim.employee_id),
        category_id=str(category.id),
        expense_date=date(2026, 11, 3),
        description="Client workshop transport revised",
        amount="2800.00",
        receipt_number="RCT-2026-1103",
        receipt_date=date(2026, 11, 3),
        receipt_merchant="Nairobi Shuttle Co.",
    )
    assert updated_claim.description == "Client workshop transport revised"

    submitted = client.expense_action(str(claim.id), "submit", version=claim.version)
    assert submitted.status == "submitted"
    approved = client.expense_action(str(claim.id), "approve", version=submitted.version)
    assert approved.status == "approved"
    reimbursed = client.reimburse_expense_claim(
        str(claim.id),
        version=approved.version,
        paid_on=date(2026, 11, 5),
        reference="BANK-EXP-0001",
    )
    assert reimbursed.status == "reimbursed"
    assert reimbursed.journal_id is not None


def test_asset_client_manages_categories_register_and_lifecycle() -> None:
    api = FakeApi()
    client = _client(api)
    client.login("nexus-demo", "admin@nexus.co.ke", "NexusDemo!2026")

    categories = client.list_asset_categories(COMPANY_ID)
    assert len(categories) == 1
    category = categories[0]
    assert category.code == "MOTOR"
    assert category.cost_account_id is not None
    assert category.gain_loss_account_id is not None

    created_category = client.create_asset_category(
        company_id=COMPANY_ID,
        code="PLANT",
        name="Plant and machinery",
        cost_account_id=str(category.cost_account_id),
        accumulated_depreciation_account_id=str(category.accumulated_depreciation_account_id),
        depreciation_expense_account_id=str(category.depreciation_expense_account_id),
        gain_loss_account_id=str(category.gain_loss_account_id),
        description="Production equipment",
    )
    assert created_category.code == "PLANT"

    updated_category = client.update_asset_category(
        str(category.id),
        version=category.version,
        name="Motor vehicles (revised)",
        cost_account_id=str(category.cost_account_id),
        accumulated_depreciation_account_id=str(category.accumulated_depreciation_account_id),
        depreciation_expense_account_id=str(category.depreciation_expense_account_id),
        gain_loss_account_id=str(category.gain_loss_account_id),
        description=category.description,
        is_active=False,
    )
    assert updated_category.is_active is False

    register = client.list_assets(COMPANY_ID)
    assert register.total == 1
    asset = register.items[0]
    assert asset.asset_number == "FA-0001"
    assert asset.category_code == "MOTOR"
    assert asset.cost == 120000
    assert asset.accumulated_depreciation == 9000
    assert asset.net_book_value == 111000
    assert asset.status == "active"

    created = client.create_asset(
        company_id=COMPANY_ID,
        category_id=str(asset.category_id),
        asset_number="FA-0002",
        name="Isuzu D-Max",
        description="Field vehicle",
        serial_reference="KCX-900-2026",
        acquisition_date=date(2026, 5, 2),
        in_service_date=date(2026, 5, 5),
        cost="80000.00",
        residual_value="8000.00",
        useful_life_months=60,
    )
    assert created.status == "draft"
    assert created.asset_number == "FA-0002"

    updated = client.update_asset(
        str(asset.id),
        version=asset.version,
        name="Toyota Hilux revised",
        description=asset.description,
        serial_reference=asset.serial_reference,
        acquisition_date=asset.acquisition_date,
        in_service_date=asset.in_service_date,
        cost="120000.00",
        residual_value="12000.00",
        useful_life_months=12,
    )
    assert updated.name == "Toyota Hilux revised"

    submitted = client.submit_asset(str(asset.id), version=asset.version)
    assert submitted.status == "submitted"
    approved = client.approve_asset(
        str(asset.id),
        version=submitted.version,
        funding_account_id=str(category.cost_account_id),
        reference="CAP-2026-0001",
        note="Approved for use",
    )
    assert approved.status == "active"
    assert approved.capitalization_journal_id is not None
    approval_body = json.loads(api.requests[-1].content)
    assert approval_body["version"] == submitted.version
    assert approval_body["reference"] == "CAP-2026-0001"

    schedule = client.list_asset_depreciation(str(asset.id))
    assert len(schedule) == 1
    assert schedule[0].amount == 9000

    posted = client.depreciate_asset(
        str(asset.id),
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
        reference="DEP-2026-02",
    )
    assert posted.period_start == date(2026, 2, 1)
    assert posted.journal_id == schedule[0].journal_id

    disposal = client.dispose_asset(
        str(asset.id),
        version=approved.version,
        disposal_date=date(2026, 4, 15),
        proceeds_amount="90000.00",
        proceeds_account_id=str(category.cost_account_id),
        reason="Sold after replacement",
    )
    assert disposal.status == "pending"
    assert disposal.proceeds_amount == 90000

    decided = client.asset_disposal_action(
        str(asset.id), "approve", version=approved.version, note="Approved disposal"
    )
    assert decided.status == "approved"
    assert decided.journal_id is not None
    disposal_body = json.loads(api.requests[-1].content)
    assert disposal_body["version"] == approved.version


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
