"""Authenticated application shell, navigation and session lifecycle."""

from __future__ import annotations

from urllib.parse import urlparse

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from nexus_desktop.config import DesktopPreferences
from nexus_desktop.session import SessionVault
from nexus_desktop.state import ApplicationSession
from nexus_desktop.tasks import TaskRunner
from nexus_desktop.ui.pages.access import AccessPage
from nexus_desktop.ui.pages.accounting import AccountingPage
from nexus_desktop.ui.pages.base import BasePage
from nexus_desktop.ui.pages.benefits import BenefitsPage
from nexus_desktop.ui.pages.dashboard import OverviewPage
from nexus_desktop.ui.pages.employees import EmployeesPage
from nexus_desktop.ui.pages.expenses import ExpensesPage
from nexus_desktop.ui.pages.leave import LeavePage
from nexus_desktop.ui.pages.loans import LoansPage
from nexus_desktop.ui.pages.payroll import PayrollPage


class MainWindow(QMainWindow):
    signed_out = pyqtSignal()
    theme_changed = pyqtSignal(str)
    authentication_expired = pyqtSignal()

    def __init__(
        self,
        session: ApplicationSession,
        preferences: DesktopPreferences,
        vault: SessionVault,
    ) -> None:
        super().__init__()
        self.session = session
        self._preferences = preferences
        self._vault = vault
        self._runner = TaskRunner(self)
        self._pages: dict[str, BasePage] = {}
        self._page_indexes: dict[str, int] = {}
        self._page_titles: dict[str, str] = {}
        self._nav_rows: dict[str, int] = {}
        self._current_page = ""
        self._closing = False
        self.setWindowTitle("Nexus Enterprise")
        self.resize(1380, 900)
        self.setMinimumSize(1120, 740)
        self._build()
        self.session.client.set_auth_expired_callback(self.authentication_expired.emit)
        self.authentication_expired.connect(self._handle_auth_expired)
        if self._nav_rows:
            self.navigation.setCurrentRow(min(self._nav_rows.values()))

    def _build(self) -> None:
        shell = QWidget()
        shell.setObjectName("AppShell")
        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(shell)

        root.addWidget(self._build_sidebar())
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_top_bar())
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)
        root.addWidget(content, 1)

        self._status = QStatusBar()
        self._status.setSizeGripEnabled(False)
        self.setStatusBar(self._status)
        self._status.showMessage("Signed in")
        self._register_pages()

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(238)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 22, 0, 18)
        layout.setSpacing(0)

        identity = QHBoxLayout()
        identity.setContentsMargins(20, 0, 18, 0)
        identity.setSpacing(10)
        mark = QLabel("N")
        mark.setProperty("role", "brand-mark")
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        names = QVBoxLayout()
        names.setSpacing(0)
        brand = QLabel("NEXUS")
        brand.setProperty("role", "brand")
        caption = QLabel("Enterprise operations")
        caption.setProperty("role", "brand-caption")
        names.addWidget(brand)
        names.addWidget(caption)
        identity.addWidget(mark)
        identity.addLayout(names)
        identity.addStretch(1)
        layout.addLayout(identity)
        layout.addSpacing(22)

        tenant = QFrame()
        tenant.setProperty("card", "true")
        tenant.setStyleSheet(
            'QFrame[card="true"] { background: rgba(255,255,255,0.05); '
            "border: 1px solid rgba(255,255,255,0.09); border-radius: 8px; }"
        )
        tenant_layout = QVBoxLayout(tenant)
        tenant_layout.setContentsMargins(12, 10, 12, 10)
        tenant_layout.setSpacing(2)
        organisation = QLabel(self.session.me.organization_name)
        organisation.setProperty("role", "brand")
        organisation.setStyleSheet("font-size: 13px; font-weight: 600;")
        organisation.setWordWrap(True)
        self.sidebar_company = QLabel(self.session.company.name)
        self.sidebar_company.setProperty("role", "brand-caption")
        self.sidebar_company.setWordWrap(True)
        tenant_layout.addWidget(organisation)
        tenant_layout.addWidget(self.sidebar_company)
        tenant_container = QHBoxLayout()
        tenant_container.setContentsMargins(12, 0, 12, 0)
        tenant_container.addWidget(tenant)
        layout.addLayout(tenant_container)
        layout.addSpacing(18)

        section = QLabel("WORKSPACE")
        section.setProperty("role", "eyebrow")
        section.setStyleSheet("padding-left: 24px;")
        layout.addWidget(section)
        layout.addSpacing(4)

        self.navigation = QListWidget()
        self.navigation.setObjectName("Navigation")
        self.navigation.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.currentRowChanged.connect(self._navigate_to_current)
        layout.addWidget(self.navigation, 1)
        layout.addSpacing(10)

        api_host = urlparse(self.session.api_url).netloc or self.session.api_url
        environment = QLabel(f"API  ·  {api_host}")
        environment.setProperty("role", "brand-caption")
        environment.setStyleSheet("padding-left: 24px;")
        environment.setWordWrap(True)
        layout.addWidget(environment)
        layout.addSpacing(8)

        user_box = QVBoxLayout()
        user_box.setContentsMargins(24, 0, 20, 0)
        user_box.setSpacing(1)
        user_name = QLabel(self.session.me.user.full_name)
        user_name.setProperty("role", "brand")
        user_name.setStyleSheet("font-size: 12px; font-weight: 600;")
        user_name.setWordWrap(True)
        user_email = QLabel(self.session.email)
        user_email.setProperty("role", "brand-caption")
        user_email.setWordWrap(True)
        user_box.addWidget(user_name)
        user_box.addWidget(user_email)
        layout.addLayout(user_box)
        layout.addSpacing(10)

        self.sign_out_button = QPushButton("Sign out")
        self.sign_out_button.setProperty("role", "ghost")
        self.sign_out_button.setStyleSheet(
            "QPushButton { color: #EDE9DF; text-align: left; padding-left: 24px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.07); }"
        )
        self.sign_out_button.clicked.connect(self._confirm_sign_out)
        layout.addWidget(self.sign_out_button)
        return sidebar

    def _build_top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(66)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(30, 0, 28, 0)
        layout.setSpacing(14)
        self.page_title = QLabel("")
        self.page_title.setProperty("role", "section-title")
        layout.addWidget(self.page_title)
        layout.addStretch(1)

        company_label = QLabel("Company")
        company_label.setProperty("role", "muted")
        self.company_selector = QComboBox()
        self.company_selector.setMinimumWidth(210)
        for company in self.session.me.companies:
            self.company_selector.addItem(f"{company.name}  ·  {company.code}", str(company.id))
        current_index = self.company_selector.findData(self.session.selected_company_id)
        self.company_selector.setCurrentIndex(max(current_index, 0))
        self.company_selector.currentIndexChanged.connect(self._company_changed)
        layout.addWidget(company_label)
        layout.addWidget(self.company_selector)

        self.theme_button = QPushButton("Light" if self._preferences.theme == "dark" else "Dark")
        self.theme_button.setToolTip("Switch appearance")
        self.theme_button.setProperty("role", "ghost")
        self.theme_button.clicked.connect(self._toggle_theme)
        layout.addWidget(self.theme_button)

        user_chip = QLabel(self.session.me.user.full_name)
        user_chip.setProperty("role", "chip")
        layout.addWidget(user_chip)
        return bar

    def _register_pages(self) -> None:
        definitions: list[tuple[str, str, type[BasePage], str | None]] = [
            ("overview", "Overview", OverviewPage, None),
            ("people", "People", EmployeesPage, "employee.view"),
            ("leave", "Leave", LeavePage, "leave.view"),
            ("loans", "Loans", LoansPage, "loan.view"),
            ("benefits", "Benefits", BenefitsPage, "benefit.view"),
            ("expenses", "Expenses", ExpensesPage, "expense.view"),
            ("payroll", "Payroll", PayrollPage, "payroll.view"),
            ("accounting", "Accounting", AccountingPage, "accounting.view"),
            ("access", "Access", AccessPage, "user.view"),
        ]
        for key, title, page_type, permission in definitions:
            if permission is not None and not self.session.can(permission):
                continue
            page = page_type(self.session)
            index = self.stack.addWidget(page)
            self._pages[key] = page
            self._page_indexes[key] = index
            self._page_titles[key] = title
            page.status_changed.connect(self._show_status)
            page.navigate_requested.connect(self._navigate_to)

            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.navigation.addItem(item)
            self._nav_rows[key] = self.navigation.count() - 1

    def _show_status(self, message: str) -> None:
        self._status.showMessage(message, 8000)

    def _navigate_to_current(self) -> None:
        item = self.navigation.currentItem()
        if item is None:
            return
        key = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not key:
            return
        index = self._page_indexes.get(key)
        if index is None:
            return
        self._current_page = key
        self.stack.setCurrentIndex(index)
        self.page_title.setText(self._page_titles.get(key, ""))
        page = self._pages.get(key)
        if page is not None:
            page.refresh()

    def _navigate_to(self, key: str) -> None:
        row = self._nav_rows.get(key)
        if row is None:
            return
        if self.navigation.currentRow() == row:
            self._navigate_to_current()
        else:
            self.navigation.setCurrentRow(row)

    def _company_changed(self) -> None:
        company_id = self.company_selector.currentData()
        if not company_id:
            return
        self.session.select_company(str(company_id))
        self.sidebar_company.setText(self.session.company.name)
        self._status.showMessage(f"Switched to {self.session.company.name}", 6000)
        page = self._pages.get(self._current_page)
        if page is not None:
            page.refresh()

    def _toggle_theme(self) -> None:
        new_theme = "light" if self._preferences.theme == "dark" else "dark"
        self._preferences.theme = new_theme
        self._preferences.sync()
        self.theme_button.setText("Light" if new_theme == "dark" else "Dark")
        self.theme_changed.emit(new_theme)

    def _confirm_sign_out(self) -> None:
        answer = QMessageBox.question(
            self,
            "Sign out",
            "Sign out of this Nexus workspace?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._sign_out()

    def _sign_out(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.session.client.set_auth_expired_callback(None)
        self.sign_out_button.setEnabled(False)
        self.company_selector.setEnabled(False)
        self.navigation.setEnabled(False)
        self._status.showMessage("Signing out…")
        self._clear_stored_session()
        self._runner.submit(
            self.session.client.logout,
            on_success=lambda _result: self._finish_sign_out(),
            on_failure=lambda _error: self._finish_sign_out(),
        )

    def _handle_auth_expired(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._clear_stored_session()
        QMessageBox.information(
            self,
            "Session ended",
            "Your session ended or was revoked. Sign in again to continue.",
        )
        self._finish_sign_out()

    def _clear_stored_session(self) -> None:
        self._vault.clear(
            api_url=self.session.api_url,
            organization_slug=self.session.organization_slug,
            email=self.session.email,
        )

    def _finish_sign_out(self) -> None:
        self.session.client.set_auth_expired_callback(None)
        self.signed_out.emit()

    def closeEvent(self, event: QCloseEvent | None) -> None:
        if not self._closing:
            self.session.client.set_auth_expired_callback(None)
            self.session.client.close()
        super().closeEvent(event)
