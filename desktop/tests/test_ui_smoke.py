"""Offscreen desktop smoke tests against a deterministic HTTP transport."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PyQt6.QtCore import QDate, QSettings
from pytestqt.qtbot import QtBot

from fake_api import COMPANY_ID, FakeApi
from nexus_desktop.api.client import NexusClient
from nexus_desktop.api.models import MeResponse, TokenPair
from nexus_desktop.config import DesktopPreferences
from nexus_desktop.session import InMemorySessionVault
from nexus_desktop.state import ApplicationSession
from nexus_desktop.ui.common import StatusBadge
from nexus_desktop.ui.login import LoginWindow
from nexus_desktop.ui.main_window import MainWindow
from nexus_desktop.ui.pages.access import AccessPage
from nexus_desktop.ui.pages.accounting import AccountingPage
from nexus_desktop.ui.pages.benefits import BenefitsPage
from nexus_desktop.ui.pages.employees import EmployeesPage
from nexus_desktop.ui.pages.expenses import ExpensesPage
from nexus_desktop.ui.pages.leave import LeavePage
from nexus_desktop.ui.pages.loans import LoansPage
from nexus_desktop.ui.pages.payroll import PayrollPage, PayrollSetupDialog


def _preferences(tmp_path: Path) -> DesktopPreferences:
    settings = QSettings(str(tmp_path / "desktop.ini"), QSettings.Format.IniFormat)
    return DesktopPreferences(settings)


def _session(client: NexusClient) -> ApplicationSession:
    return ApplicationSession(
        client=client,
        me=client.me(),
        api_url="http://nexus.test/api/v1",
        organization_slug="nexus-demo",
        email="admin@nexus.co.ke",
        selected_company_id=COMPANY_ID,
    )


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def client(api: FakeApi) -> Iterator[NexusClient]:
    client = NexusClient("http://nexus.test/api/v1", transport=api.transport())
    client.set_session(
        TokenPair(
            access_token="access-2",
            refresh_token="refresh-2",
            expires_in=900,
            refresh_expires_in=2_592_000,
        )
    )
    yield client
    client.close()


def _nav_titles(window: MainWindow) -> list[str]:
    return [window.navigation.item(index).text() for index in range(window.navigation.count())]


def test_login_window_emits_authenticated_session(
    qtbot: QtBot,
    api: FakeApi,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def build_client(api_url: str, *, on_tokens_changed: object = None) -> NexusClient:
        callback = on_tokens_changed if callable(on_tokens_changed) else None
        return NexusClient(
            api_url,
            transport=api.transport(),
            on_tokens_changed=callback,
        )

    monkeypatch.setattr("nexus_desktop.ui.login.NexusClient", build_client)
    preferences = _preferences(tmp_path)
    vault = InMemorySessionVault()
    login = LoginWindow(preferences=preferences, vault=vault)
    qtbot.addWidget(login)
    login.show()
    login.organization_input.setText("nexus-demo")
    login.email_input.setText("admin@nexus.co.ke")
    login.password_input.setText("NexusDemo!2026")

    with qtbot.waitSignal(login.authenticated, timeout=8_000) as signal:
        login.sign_in_button.click()

    session = signal.args[0]
    assert isinstance(session, ApplicationSession)
    assert session.me.user.full_name == "Amina Admin"
    assert session.selected_company_id == COMPANY_ID
    assert (
        vault.load(
            api_url=session.api_url,
            organization_slug=session.organization_slug,
            email=session.email,
        )
        == "refresh-1"
    )
    session.client.close()


def test_main_window_loads_every_page_and_switches_theme(
    qtbot: QtBot,
    client: NexusClient,
    api: FakeApi,
    tmp_path: Path,
) -> None:
    session = _session(client)
    preferences = _preferences(tmp_path)
    window = MainWindow(session, preferences, InMemorySessionVault())
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(
        lambda: window.statusBar().currentMessage() == "Overview ready",
        timeout=8_000,
    )
    assert _nav_titles(window) == [
        "Overview",
        "People",
        "Leave",
        "Loans",
        "Benefits",
        "Expenses",
        "Payroll",
        "Accounting",
        "Access",
    ]

    window.navigation.setCurrentRow(1)
    employees = window.stack.currentWidget()
    assert isinstance(employees, EmployeesPage)
    qtbot.waitUntil(lambda: employees.table.rowCount() == 1, timeout=8_000)
    assert employees.table.item(0, 0).text() == "Achieng Otieno"

    window.navigation.setCurrentRow(2)
    leave = window.stack.currentWidget()
    assert isinstance(leave, LeavePage)
    qtbot.waitUntil(lambda: leave.requests_table.rowCount() == 1, timeout=8_000)
    assert leave.requests_table.item(0, 0).text() == "Achieng Otieno"
    assert leave.requests_table.item(0, 1).text() == "Annual leave"
    assert leave.request_year.currentData() == QDate.currentDate().year()
    assert leave.types_table.rowCount() == 1
    assert leave.types_table.item(0, 0).text() == "ANNUAL"
    assert leave.holidays_table.rowCount() == 1
    assert leave.attendance_table.rowCount() == 1
    attendance_status = leave.attendance_table.cellWidget(0, 2)
    assert attendance_status is not None
    badge = attendance_status.findChild(StatusBadge)
    assert badge is not None
    assert badge.text() == "Present"

    window.navigation.setCurrentRow(3)
    loans = window.stack.currentWidget()
    assert isinstance(loans, LoansPage)
    qtbot.waitUntil(lambda: loans.loans_table.rowCount() == 1, timeout=8_000)
    assert loans.loans_table.item(0, 0).text() == "Achieng Otieno"
    assert loans.loans_table.item(0, 1).text() == "LN-2026-0001"
    qtbot.waitUntil(lambda: loans.schedule_table.rowCount() == 2, timeout=8_000)
    assert loans.detail_status.text() == "Disbursed"
    assert loans.detail_status.isVisible()
    assert loans.principal_metric.text() == "KES 60,000.00"
    assert loans.progress_metric.text() == "4/12 instalments"
    assert loans.disburse_button.isEnabled() is False

    window.navigation.setCurrentRow(4)
    benefits = window.stack.currentWidget()
    assert isinstance(benefits, BenefitsPage)
    qtbot.waitUntil(lambda: benefits.plans_table.rowCount() == 1, timeout=8_000)
    qtbot.waitUntil(lambda: benefits.enrollments_table.rowCount() == 1, timeout=8_000)
    assert benefits.plans_table.item(0, 0).text() == "MED"
    assert benefits.plans_table.item(0, 1).text() == "Comprehensive medical cover"
    assert benefits.plans_table.item(0, 3).text() == "2500.00"
    assert benefits.enrollments_table.item(0, 0).text() == "Achieng Otieno"
    assert benefits.enrollments_table.item(0, 4).text() == "2500.00"
    assert benefits.new_plan_button.isEnabled() is True
    assert benefits.end_enrollment_button.isEnabled() is True

    window.navigation.setCurrentRow(5)
    expenses = window.stack.currentWidget()
    assert isinstance(expenses, ExpensesPage)
    qtbot.waitUntil(lambda: expenses.categories_table.rowCount() == 1, timeout=8_000)
    qtbot.waitUntil(lambda: expenses.claims_table.rowCount() == 1, timeout=8_000)
    assert expenses.categories_table.item(0, 0).text() == "TRAVEL"
    assert expenses.categories_table.item(0, 2).text() == "5000 · Payroll expense"
    assert expenses.claims_table.item(0, 0).text() == "EXP-2026-0001"
    assert expenses.claims_table.item(0, 1).text() == "Achieng Otieno"
    assert expenses.claims_table.item(0, 4).text() == "KES 2,750.00"
    assert expenses.new_claim_button.isEnabled() is True
    assert expenses.approve_claim_button.isEnabled() is False

    window.navigation.setCurrentRow(6)
    payroll = window.stack.currentWidget()
    assert isinstance(payroll, PayrollPage)
    qtbot.waitUntil(lambda: payroll.runs_table.rowCount() == 1, timeout=8_000)
    qtbot.waitUntil(lambda: payroll.results_table.rowCount() == 1, timeout=8_000)
    assert payroll.runs_table.item(0, 0).text() == "PR-202610-0001"
    assert payroll.results_table.item(0, 0).text() == "Achieng Otieno"

    window.navigation.setCurrentRow(7)
    accounting = window.stack.currentWidget()
    assert isinstance(accounting, AccountingPage)
    qtbot.waitUntil(lambda: accounting.balance_badge.text() == "Balanced", timeout=8_000)
    assert accounting.accounts_table.rowCount() == 2
    assert accounting.journals_table.rowCount() == 1

    window.navigation.setCurrentRow(8)
    access = window.stack.currentWidget()
    assert isinstance(access, AccessPage)
    qtbot.waitUntil(lambda: access.table.rowCount() == 1, timeout=8_000)
    assert access.table.item(0, 1).text() == "admin@nexus.co.ke"
    assert "Organisation Administrator" in access.table.item(0, 3).text()

    assert preferences.theme == "light"
    with qtbot.waitSignal(window.theme_changed, timeout=2_000) as theme_signal:
        window.theme_button.click()
    assert theme_signal.args == ["dark"]
    assert preferences.theme == "dark"

    assert api.revoked is False
    window.close()


def test_payroll_setup_creates_group_then_period(
    qtbot: QtBot,
    client: NexusClient,
) -> None:
    session = _session(client)
    dialog = PayrollSetupDialog(session, [])
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.create_period_button.isEnabled() is False
    dialog.group_name.setText("Monthly Payroll")
    dialog.group_code.setText("MONTHLY")

    dialog.create_group_button.click()
    qtbot.waitUntil(lambda: dialog.group_combo.count() == 1, timeout=8_000)
    assert dialog.create_period_button.isEnabled() is True
    assert dialog.status_label.text() == "Created pay group MONTHLY."

    dialog.create_period_button.click()
    qtbot.waitUntil(
        lambda: dialog.status_label.text().startswith("Opened period"),
        timeout=8_000,
    )


def test_navigation_respects_permissions(
    qtbot: QtBot,
    client: NexusClient,
    tmp_path: Path,
) -> None:
    session = _session(client)
    restricted_me = MeResponse.model_validate(
        session.me.model_dump() | {"permissions": ["employee.view", "payroll.view"]}
    )
    restricted = ApplicationSession(
        client=client,
        me=restricted_me,
        api_url=session.api_url,
        organization_slug=session.organization_slug,
        email=session.email,
        selected_company_id=session.selected_company_id,
    )
    window = MainWindow(restricted, _preferences(tmp_path), InMemorySessionVault())
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(
        lambda: window.statusBar().currentMessage() == "Overview ready",
        timeout=8_000,
    )
    assert _nav_titles(window) == ["Overview", "People", "Payroll"]
    window.close()
