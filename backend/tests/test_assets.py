"""Fixed asset category, lifecycle, depreciation and disposal coverage."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from .conftest import AuthenticatedSession, build_session, create_member

API = "/api/v1"

pytestmark = pytest.mark.postgres


async def _accounts(client: httpx.AsyncClient, session: AuthenticatedSession) -> dict[str, Any]:
    response = await client.get(
        f"{API}/accounting/accounts",
        headers=session.headers,
        params={"company_id": session.company_id},
    )
    assert response.status_code == 200, response.text
    return {account["code"]: account for account in response.json()}


async def _create_account(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    *,
    company_id: str,
    code: str,
    name: str,
    account_type: str,
    normal_balance: str,
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/accounting/accounts",
        headers=session.headers,
        json={
            "company_id": company_id,
            "code": code,
            "name": name,
            "account_type": account_type,
            "normal_balance": normal_balance,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _asset_chart(
    client: httpx.AsyncClient, session: AuthenticatedSession, *, company_id: str | None = None
) -> dict[str, str]:
    company = company_id or session.company_id
    listing = await client.get(
        f"{API}/accounting/accounts",
        headers=session.headers,
        params={"company_id": company},
    )
    assert listing.status_code == 200, listing.text
    existing = {account["code"]: account["id"] for account in listing.json()}
    definitions = {
        "cost": ("1600", "Motor Vehicles", "asset", "debit"),
        "accumulated": (
            "1690",
            "Accumulated Depreciation - Motor Vehicles",
            "asset",
            "credit",
        ),
        "expense": ("5500", "Depreciation Expense", "expense", "debit"),
        "gain_loss": ("4600", "Gain or Loss on Disposal", "revenue", "credit"),
    }
    chart: dict[str, str] = {}
    for key, (code, name, account_type, normal_balance) in definitions.items():
        if code in existing:
            chart[key] = existing[code]
            continue
        chart[key] = (
            await _create_account(
                client,
                session,
                company_id=company,
                code=code,
                name=name,
                account_type=account_type,
                normal_balance=normal_balance,
            )
        )["id"]
    return chart


async def _create_category(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    chart: dict[str, str],
    *,
    company_id: str | None = None,
    code: str = "MOTOR",
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/assets/categories",
        headers=session.headers,
        json={
            "company_id": company_id or session.company_id,
            "code": code,
            "name": "Motor Vehicles",
            "description": "Vehicles used in operations",
            "cost_account_id": chart["cost"],
            "accumulated_depreciation_account_id": chart["accumulated"],
            "depreciation_expense_account_id": chart["expense"],
            "gain_loss_account_id": chart["gain_loss"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_asset(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    category_id: str,
    *,
    company_id: str | None = None,
    asset_number: str = "FA-0001",
    name: str = "Toyota Hilux",
    acquisition_date: str = "2026-01-15",
    in_service_date: str = "2026-02-01",
    cost: str = "120000.00",
    residual_value: str = "12000.00",
    useful_life_months: int = 12,
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/assets",
        headers=session.headers,
        json={
            "company_id": company_id or session.company_id,
            "category_id": category_id,
            "asset_number": asset_number,
            "name": name,
            "description": "Double cab pickup",
            "serial_reference": "KDJ-120-2026",
            "acquisition_date": acquisition_date,
            "in_service_date": in_service_date,
            "cost": cost,
            "residual_value": residual_value,
            "useful_life_months": useful_life_months,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _finance_approver(
    client: httpx.AsyncClient, admin: AuthenticatedSession
) -> AuthenticatedSession:
    await create_member(
        client,
        admin,
        email="asset.approver@nexus.co.ke",
        full_name="Asset Approver",
        password="AssetApproval!2026",
        roles=[{"role_code": "finance_manager", "company_id": admin.company_id}],
    )
    return await build_session(
        client,
        admin.tenant,
        email="asset.approver@nexus.co.ke",
        password="AssetApproval!2026",
    )


async def _submit(
    client: httpx.AsyncClient, session: AuthenticatedSession, asset: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/assets/{asset['id']}/submit",
        headers=session.headers,
        json={"version": asset["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _approve(
    client: httpx.AsyncClient,
    session: AuthenticatedSession,
    asset: dict[str, Any],
    funding_account_id: str,
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/assets/{asset['id']}/approve",
        headers=session.headers,
        json={
            "version": asset["version"],
            "funding_account_id": funding_account_id,
            "reference": "CAP-2026-0001",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _capitalized_asset(
    client: httpx.AsyncClient,
    admin: AuthenticatedSession,
    approver: AuthenticatedSession,
    *,
    asset_number: str,
    cost: str,
    residual_value: str,
    useful_life_months: int,
    acquisition_date: str = "2026-01-15",
    in_service_date: str = "2026-02-01",
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    accounts = await _accounts(client, admin)
    chart = await _asset_chart(client, admin)
    category = await _create_category(client, admin, chart, code=f"C{asset_number[-4:]}")
    asset = await _create_asset(
        client,
        admin,
        category["id"],
        asset_number=asset_number,
        cost=cost,
        residual_value=residual_value,
        useful_life_months=useful_life_months,
        acquisition_date=acquisition_date,
        in_service_date=in_service_date,
    )
    submitted = await _submit(client, admin, asset)
    approved = await _approve(client, approver, submitted, accounts["3000"]["id"])
    return approved, chart, accounts


async def test_asset_category_rejects_invalid_account_mapping_and_duplicate_code(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    accounts = await _accounts(client, admin_session)
    chart = await _asset_chart(client, admin_session)
    payload = {
        "company_id": admin_session.company_id,
        "code": "MOTOR",
        "name": "Motor Vehicles",
        "cost_account_id": accounts["4000"]["id"],
        "accumulated_depreciation_account_id": chart["accumulated"],
        "depreciation_expense_account_id": chart["expense"],
        "gain_loss_account_id": chart["gain_loss"],
    }

    invalid_cost = await client.post(
        f"{API}/assets/categories", headers=admin_session.headers, json=payload
    )
    assert invalid_cost.status_code == 422, invalid_cost.text
    assert "cost account" in invalid_cost.json()["error"]["message"].lower()

    payload["cost_account_id"] = chart["cost"]
    payload["accumulated_depreciation_account_id"] = chart["cost"]
    invalid_accumulated = await client.post(
        f"{API}/assets/categories", headers=admin_session.headers, json=payload
    )
    assert invalid_accumulated.status_code == 422, invalid_accumulated.text
    assert "different" in invalid_accumulated.json()["error"]["message"].lower()

    category = await _create_category(client, admin_session, chart)
    assert category["code"] == "MOTOR"
    assert category["is_active"] is True

    duplicate = await client.post(
        f"{API}/assets/categories",
        headers=admin_session.headers,
        json={
            **payload,
            "code": "motor",
            "cost_account_id": chart["cost"],
            "accumulated_depreciation_account_id": chart["accumulated"],
        },
    )
    assert duplicate.status_code == 409, duplicate.text

    asset = await _create_asset(client, admin_session, category["id"])
    submitted = await _submit(client, admin_session, asset)
    assert submitted["status"] == "submitted"

    archive = await client.patch(
        f"{API}/assets/categories/{category['id']}",
        headers=admin_session.headers,
        json={"version": category["version"], "is_active": False},
    )
    assert archive.status_code == 409, archive.text
    assert "archive blocked" in archive.json()["error"]["message"].lower()

    remap = await client.patch(
        f"{API}/assets/categories/{category['id']}",
        headers=admin_session.headers,
        json={"version": category["version"], "cost_account_id": accounts["1100"]["id"]},
    )
    assert remap.status_code == 409, remap.text
    assert "cannot change" in remap.json()["error"]["message"].lower()


async def test_asset_draft_validates_cost_residual_life_and_tenant_accounts(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    chart = await _asset_chart(client, admin_session)
    category = await _create_category(client, admin_session, chart)

    async def create(payload: dict[str, Any]) -> httpx.Response:
        return await client.post(
            f"{API}/assets",
            headers=admin_session.headers,
            json={
                "company_id": admin_session.company_id,
                "category_id": category["id"],
                "asset_number": "FA-0100",
                "name": "Toyota Hilux",
                "acquisition_date": "2026-01-15",
                "in_service_date": "2026-02-01",
                "cost": "120000.00",
                "residual_value": "12000.00",
                "useful_life_months": 12,
                **payload,
            },
        )

    assert (await create({"cost": "0.00"})).status_code == 422
    assert (await create({"cost": "-10.00"})).status_code == 422
    assert (await create({"residual_value": "130000.00"})).status_code == 422
    assert (await create({"useful_life_months": 0})).status_code == 422
    assert (await create({"in_service_date": "2026-01-01"})).status_code == 422

    other_company = await client.post(
        f"{API}/companies",
        headers=admin_session.headers,
        json={
            "code": "OTHER",
            "name": "Other Company Limited",
            "base_currency": "KES",
            "fiscal_year_start_month": 1,
        },
    )
    assert other_company.status_code == 201, other_company.text
    other_company_id = other_company.json()["id"]
    other_chart = await _asset_chart(client, admin_session, company_id=other_company_id)
    other_category = await _create_category(
        client,
        admin_session,
        other_chart,
        company_id=other_company_id,
        code="OTHERMOTOR",
    )

    cross_tenant = await client.post(
        f"{API}/assets",
        headers=admin_session.headers,
        json={
            "company_id": admin_session.company_id,
            "category_id": other_category["id"],
            "asset_number": "FA-0200",
            "name": "Cross tenant vehicle",
            "acquisition_date": "2026-01-15",
            "in_service_date": "2026-02-01",
            "cost": "50000.00",
            "residual_value": "0.00",
            "useful_life_months": 12,
        },
    )
    assert cross_tenant.status_code == 404, cross_tenant.text

    register = await client.get(
        f"{API}/assets",
        headers=admin_session.headers,
        params={"company_id": admin_session.company_id},
    )
    assert register.status_code == 200, register.text
    assert register.json()["total"] == 0


async def test_asset_capitalization_requires_separation_and_posts_once(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    approver = await _finance_approver(client, admin_session)
    accounts = await _accounts(client, admin_session)
    chart = await _asset_chart(client, admin_session)
    category = await _create_category(client, admin_session, chart)
    asset = await _create_asset(client, admin_session, category["id"])

    stale = await client.patch(
        f"{API}/assets/{asset['id']}",
        headers=admin_session.headers,
        json={"version": 99, "name": "Stale edit"},
    )
    assert stale.status_code == 409, stale.text

    updated = await client.patch(
        f"{API}/assets/{asset['id']}",
        headers=admin_session.headers,
        json={"version": asset["version"], "name": "Toyota Hilux Revised"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Toyota Hilux Revised"

    submitted = await _submit(client, admin_session, updated.json())
    assert submitted["status"] == "submitted"

    frozen = await client.patch(
        f"{API}/assets/{asset['id']}",
        headers=admin_session.headers,
        json={"version": submitted["version"], "cost": "150000.00"},
    )
    assert frozen.status_code == 409, frozen.text

    self_approval = await client.post(
        f"{API}/assets/{asset['id']}/approve",
        headers=admin_session.headers,
        json={
            "version": submitted["version"],
            "funding_account_id": accounts["3000"]["id"],
            "reference": "CAP-SELF",
        },
    )
    assert self_approval.status_code == 409, self_approval.text
    assert "separation of duties" in self_approval.json()["error"]["message"].lower()

    approved = await _approve(client, approver, submitted, accounts["3000"]["id"])
    assert approved["status"] == "active"
    assert approved["capitalization_journal_id"] is not None
    assert approved["accumulated_depreciation"] == "0.00"
    assert approved["net_book_value"] == "120000.00"

    journal_id = approved["capitalization_journal_id"]
    detail = await client.get(
        f"{API}/accounting/journals/{journal_id}", headers=admin_session.headers
    )
    assert detail.status_code == 200, detail.text
    journal = detail.json()
    assert journal["journal"]["status"] == "posted"
    assert journal["journal"]["source_type"] == "asset_capitalization"
    assert journal["journal"]["total_debit"] == "120000.00"
    assert journal["journal"]["total_credit"] == "120000.00"
    debits = [line for line in journal["lines"] if line["debit"] != "0.00"]
    credits = [line for line in journal["lines"] if line["credit"] != "0.00"]
    assert len(debits) == 1
    assert debits[0]["account_id"] == chart["cost"]
    assert len(credits) == 1
    assert credits[0]["account_id"] == accounts["3000"]["id"]

    retry = await client.post(
        f"{API}/assets/{asset['id']}/approve",
        headers=approver.headers,
        json={
            "version": approved["version"],
            "funding_account_id": accounts["3000"]["id"],
            "reference": "CAP-RETRY",
        },
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["capitalization_journal_id"] == journal_id

    journals = await client.get(
        f"{API}/accounting/journals",
        headers=admin_session.headers,
        params={"company_id": admin_session.company_id, "page_size": 100},
    )
    assert journals.status_code == 200, journals.text
    capitalization = [
        item for item in journals.json()["items"] if item["source_type"] == "asset_capitalization"
    ]
    assert len(capitalization) == 1


async def test_asset_depreciation_is_straight_line_rounded_and_idempotent(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    approver = await _finance_approver(client, admin_session)
    asset, chart, _ = await _capitalized_asset(
        client,
        admin_session,
        approver,
        asset_number="FA-3001",
        cost="120000.00",
        residual_value="12000.00",
        useful_life_months=12,
    )

    first = await client.post(
        f"{API}/assets/{asset['id']}/depreciate",
        headers=approver.headers,
        json={
            "period_start": "2026-02-01",
            "period_end": "2026-02-28",
            "reference": "DEP-2026-02",
        },
    )
    assert first.status_code == 201, first.text
    assert first.json()["amount"] == "9000.00"

    repeat = await client.post(
        f"{API}/assets/{asset['id']}/depreciate",
        headers=approver.headers,
        json={"period_start": "2026-02-01", "period_end": "2026-02-28"},
    )
    assert repeat.status_code == 201, repeat.text
    assert repeat.json()["id"] == first.json()["id"]

    schedule = await client.get(
        f"{API}/assets/{asset['id']}/depreciation", headers=approver.headers
    )
    assert schedule.status_code == 200, schedule.text
    assert len(schedule.json()) == 1

    non_calendar = await client.post(
        f"{API}/assets/{asset['id']}/depreciate",
        headers=approver.headers,
        json={"period_start": "2026-03-02", "period_end": "2026-03-31"},
    )
    assert non_calendar.status_code == 422, non_calendar.text

    pre_service = await client.post(
        f"{API}/assets/{asset['id']}/depreciate",
        headers=approver.headers,
        json={"period_start": "2026-01-01", "period_end": "2026-01-31"},
    )
    assert pre_service.status_code == 422, pre_service.text

    detail = await client.get(f"{API}/assets/{asset['id']}", headers=approver.headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["accumulated_depreciation"] == "9000.00"
    assert detail.json()["net_book_value"] == "111000.00"

    journal = await client.get(
        f"{API}/accounting/journals/{first.json()['journal_id']}", headers=approver.headers
    )
    assert journal.status_code == 200, journal.text
    lines = journal.json()["lines"]
    debits = [line for line in lines if line["debit"] != "0.00"]
    credits = [line for line in lines if line["credit"] != "0.00"]
    assert debits[0]["account_id"] == chart["expense"]
    assert credits[0]["account_id"] == chart["accumulated"]

    rounding_asset, _, _ = await _capitalized_asset(
        client,
        admin_session,
        approver,
        asset_number="FA-3002",
        cost="100000.00",
        residual_value="0.00",
        useful_life_months=3,
        acquisition_date="2026-10-01",
        in_service_date="2026-10-01",
    )
    amounts: list[str] = []
    for start, end in (
        ("2026-10-01", "2026-10-31"),
        ("2026-11-01", "2026-11-30"),
        ("2026-12-01", "2026-12-31"),
    ):
        response = await client.post(
            f"{API}/assets/{rounding_asset['id']}/depreciate",
            headers=approver.headers,
            json={"period_start": start, "period_end": end},
        )
        assert response.status_code == 201, response.text
        amounts.append(response.json()["amount"])
    assert amounts == ["33333.33", "33333.33", "33333.34"]
    assert sum(float(amount) for amount in amounts) == pytest.approx(100000.00)

    zero_base, _, _ = await _capitalized_asset(
        client,
        admin_session,
        approver,
        asset_number="FA-3003",
        cost="50000.00",
        residual_value="50000.00",
        useful_life_months=12,
    )
    fully_depreciated = await client.post(
        f"{API}/assets/{zero_base['id']}/depreciate",
        headers=approver.headers,
        json={"period_start": "2026-02-01", "period_end": "2026-02-28"},
    )
    assert fully_depreciated.status_code == 409, fully_depreciated.text


async def test_asset_disposal_posts_gain_and_loss_with_separation(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    approver = await _finance_approver(client, admin_session)
    accounts = await _accounts(client, admin_session)
    cash_account = accounts["1000"]["id"]

    loss_asset, chart, _ = await _capitalized_asset(
        client,
        admin_session,
        approver,
        asset_number="FA-4001",
        cost="120000.00",
        residual_value="12000.00",
        useful_life_months=12,
    )
    for start, end in (("2026-02-01", "2026-02-28"), ("2026-03-01", "2026-03-31")):
        response = await client.post(
            f"{API}/assets/{loss_asset['id']}/depreciate",
            headers=approver.headers,
            json={"period_start": start, "period_end": end},
        )
        assert response.status_code == 201, response.text

    disposal = await client.post(
        f"{API}/assets/{loss_asset['id']}/dispose",
        headers=admin_session.headers,
        json={
            "version": loss_asset["version"],
            "disposal_date": "2026-04-15",
            "proceeds_amount": "90000.00",
            "proceeds_account_id": cash_account,
            "reason": "Sold below book value after accident",
        },
    )
    assert disposal.status_code == 201, disposal.text
    assert disposal.json()["status"] == "pending"

    self_approval = await client.post(
        f"{API}/assets/{loss_asset['id']}/dispose/approve",
        headers=admin_session.headers,
        json={"version": disposal.json()["version"]},
    )
    assert self_approval.status_code == 409, self_approval.text
    assert "separation of duties" in self_approval.json()["error"]["message"].lower()

    approved = await client.post(
        f"{API}/assets/{loss_asset['id']}/dispose/approve",
        headers=approver.headers,
        json={"version": disposal.json()["version"], "note": "Approved loss disposal"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    loss_journal_id = approved.json()["journal_id"]
    assert loss_journal_id is not None

    loss_journal = await client.get(
        f"{API}/accounting/journals/{loss_journal_id}", headers=approver.headers
    )
    assert loss_journal.status_code == 200, loss_journal.text
    body = loss_journal.json()
    assert body["journal"]["source_type"] == "asset_disposal"
    assert body["journal"]["total_debit"] == "120000.00"
    assert body["journal"]["total_credit"] == "120000.00"
    by_account: dict[str, list[str]] = {}
    for line in body["lines"]:
        by_account.setdefault(line["account_id"], []).append(line["debit"])
        by_account[line["account_id"]].append(line["credit"])
    assert by_account[cash_account] == ["90000.00", "0.00"]
    assert by_account[chart["accumulated"]] == ["18000.00", "0.00"]
    assert by_account[chart["gain_loss"]] == ["12000.00", "0.00"]
    assert by_account[chart["cost"]] == ["0.00", "120000.00"]

    disposed = await client.get(f"{API}/assets/{loss_asset['id']}", headers=approver.headers)
    assert disposed.status_code == 200, disposed.text
    assert disposed.json()["status"] == "disposed"
    assert disposed.json()["disposal_journal_id"] == loss_journal_id

    retry = await client.post(
        f"{API}/assets/{loss_asset['id']}/dispose/approve",
        headers=approver.headers,
        json={"version": approved.json()["version"]},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["journal_id"] == loss_journal_id

    journals = await client.get(
        f"{API}/accounting/journals",
        headers=admin_session.headers,
        params={"company_id": admin_session.company_id, "page_size": 100},
    )
    assert journals.status_code == 200, journals.text
    disposal_journals = [
        item for item in journals.json()["items"] if item["source_type"] == "asset_disposal"
    ]
    assert len(disposal_journals) == 1

    gain_asset, gain_chart, _ = await _capitalized_asset(
        client,
        admin_session,
        approver,
        asset_number="FA-4002",
        cost="60000.00",
        residual_value="0.00",
        useful_life_months=12,
    )
    depreciation = await client.post(
        f"{API}/assets/{gain_asset['id']}/depreciate",
        headers=approver.headers,
        json={"period_start": "2026-02-01", "period_end": "2026-02-28"},
    )
    assert depreciation.status_code == 201, depreciation.text
    assert depreciation.json()["amount"] == "5000.00"

    gain_disposal = await client.post(
        f"{API}/assets/{gain_asset['id']}/dispose",
        headers=admin_session.headers,
        json={
            "version": gain_asset["version"],
            "disposal_date": "2026-03-20",
            "proceeds_amount": "70000.00",
            "proceeds_account_id": cash_account,
            "reason": "Sold above book value",
        },
    )
    assert gain_disposal.status_code == 201, gain_disposal.text
    gain_approved = await client.post(
        f"{API}/assets/{gain_asset['id']}/dispose/approve",
        headers=approver.headers,
        json={"version": gain_disposal.json()["version"]},
    )
    assert gain_approved.status_code == 200, gain_approved.text
    gain_journal = await client.get(
        f"{API}/accounting/journals/{gain_approved.json()['journal_id']}",
        headers=approver.headers,
    )
    assert gain_journal.status_code == 200, gain_journal.text
    gain_body = gain_journal.json()
    assert gain_body["journal"]["total_debit"] == "75000.00"
    assert gain_body["journal"]["total_credit"] == "75000.00"
    gain_lines: dict[str, list[str]] = {}
    for line in gain_body["lines"]:
        gain_lines.setdefault(line["account_id"], []).append(line["debit"])
        gain_lines[line["account_id"]].append(line["credit"])
    assert gain_lines[cash_account] == ["70000.00", "0.00"]
    assert gain_lines[gain_chart["accumulated"]] == ["5000.00", "0.00"]
    assert gain_lines[gain_chart["gain_loss"]] == ["0.00", "15000.00"]
    assert gain_lines[gain_chart["cost"]] == ["0.00", "60000.00"]


async def test_asset_permissions_and_disposal_rejection(
    client: httpx.AsyncClient, admin_session: AuthenticatedSession
) -> None:
    approver = await _finance_approver(client, admin_session)
    await create_member(
        client,
        admin_session,
        email="asset.auditor@nexus.co.ke",
        full_name="Asset Auditor",
        password="AssetAudit!2026",
        roles=[{"role_code": "auditor", "company_id": admin_session.company_id}],
    )
    auditor = await build_session(
        client,
        admin_session.tenant,
        email="asset.auditor@nexus.co.ke",
        password="AssetAudit!2026",
    )
    me = await client.get(f"{API}/auth/me", headers=auditor.headers)
    assert "asset.view" in me.json()["permissions"]
    assert "asset.manage" not in me.json()["permissions"]

    chart = await _asset_chart(client, admin_session)
    category_payload = {
        "company_id": admin_session.company_id,
        "code": "AUDIT",
        "name": "Audited assets",
        "cost_account_id": chart["cost"],
        "accumulated_depreciation_account_id": chart["accumulated"],
        "depreciation_expense_account_id": chart["expense"],
        "gain_loss_account_id": chart["gain_loss"],
    }
    denied = await client.post(
        f"{API}/assets/categories", headers=auditor.headers, json=category_payload
    )
    assert denied.status_code == 403, denied.text

    listing = await client.get(
        f"{API}/assets", headers=auditor.headers, params={"company_id": admin_session.company_id}
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 0

    asset, _, accounts = await _capitalized_asset(
        client,
        admin_session,
        approver,
        asset_number="FA-5001",
        cost="30000.00",
        residual_value="0.00",
        useful_life_months=12,
    )
    disposal = await client.post(
        f"{API}/assets/{asset['id']}/dispose",
        headers=admin_session.headers,
        json={
            "version": asset["version"],
            "disposal_date": "2026-02-20",
            "proceeds_amount": "10000.00",
            "proceeds_account_id": accounts["1000"]["id"],
            "reason": "No longer required",
        },
    )
    assert disposal.status_code == 201, disposal.text
    rejected = await client.post(
        f"{API}/assets/{asset['id']}/dispose/reject",
        headers=approver.headers,
        json={"version": disposal.json()["version"], "note": "Proceeds too low"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    restored = await client.get(f"{API}/assets/{asset['id']}", headers=approver.headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "active"

    new_disposal = await client.post(
        f"{API}/assets/{asset['id']}/dispose",
        headers=admin_session.headers,
        json={
            "version": restored.json()["version"],
            "disposal_date": "2026-03-01",
            "proceeds_amount": "35000.00",
            "proceeds_account_id": accounts["1000"]["id"],
            "reason": "Revised sale price accepted",
        },
    )
    assert new_disposal.status_code == 201, new_disposal.text
    assert new_disposal.json()["status"] == "pending"
