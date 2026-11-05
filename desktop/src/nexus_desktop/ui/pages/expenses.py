"""Expense categories and employee reimbursement claims workspace."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nexus_desktop.api.client import ApiError, NetworkError
from nexus_desktop.api.models import (
    Account,
    Employee,
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimPage,
)
from nexus_desktop.state import ApplicationSession
from nexus_desktop.ui.common import (
    PageHeader,
    configure_table,
    date_label,
    money,
    status_widget,
    text_item,
)
from nexus_desktop.ui.pages.base import BasePage

PAGE_SIZE = 25


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _amount_editor(value: Decimal | str = "0.00", currency: str = "") -> QDoubleSpinBox:
    editor = QDoubleSpinBox()
    editor.setRange(0.01, 1_000_000_000.0)
    editor.setDecimals(2)
    editor.setSingleStep(100.0)
    editor.setGroupSeparatorShown(True)
    if currency:
        editor.setSuffix(f" {currency}")
    editor.setValue(float(value))
    return editor


def _amount(value: QDoubleSpinBox) -> str:
    return f"{value.value():.2f}"


@dataclass(frozen=True, slots=True)
class CategoryInput:
    code: str
    name: str
    expense_account_id: str
    description: str | None


@dataclass(frozen=True, slots=True)
class ClaimInput:
    employee_id: str
    category_id: str
    expense_date: date
    description: str
    amount: str
    receipt_number: str | None
    receipt_date: date | None
    receipt_merchant: str | None


@dataclass(frozen=True, slots=True)
class ReimbursementInput:
    paid_on: date
    reference: str


@dataclass(frozen=True, slots=True)
class ExpenseData:
    accounts: list[Account]
    categories: list[ExpenseCategory]
    employees: list[Employee]
    claims: ExpenseClaimPage


class ExpenseCategoryDialog(QDialog):
    def __init__(
        self,
        accounts: list[Account],
        category: ExpenseCategory | None = None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._accounts = {str(account.id): account for account in accounts}
        self._category = category
        self.setWindowTitle("New expense category" if category is None else "Edit expense category")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel(
            "New expense category" if self._category is None else "Edit expense category"
        )
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "Every category posts reimbursement to an expense account. Archived categories "
            "cannot receive new claims."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.code = QLineEdit(self._category.code if self._category else "")
        self.code.setPlaceholderText("e.g. TRAVEL")
        self.code.setMaxLength(24)
        self.code.setEnabled(self._category is None)
        self.name = QLineEdit(self._category.name if self._category else "")
        self.name.setPlaceholderText("Category name")
        self.account = QComboBox()
        for account in self._accounts.values():
            self.account.addItem(f"{account.code} · {account.name}", str(account.id))
        if self._category is not None:
            index = self.account.findData(str(self._category.expense_account_id))
            if index >= 0:
                self.account.setCurrentIndex(index)
        self.description = QTextEdit(
            self._category.description if self._category and self._category.description else ""
        )
        self.description.setPlaceholderText("Optional category guidance")
        self.description.setFixedHeight(72)
        form.addRow("Code", self.code)
        form.addRow("Name", self.name)
        form.addRow("Expense account", self.account)
        form.addRow("Description", self.description)
        root.addLayout(form)

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()
        root.addWidget(self.error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setText("Save category")
            save.setProperty("role", "primary")
            save.setEnabled(bool(self._accounts))
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self._category is None and len(self.code.text().strip()) < 2:
            self.error.setText("Enter a category code of at least two characters.")
            self.error.show()
            return
        if len(self.name.text().strip()) < 2:
            self.error.setText("Enter a category name.")
            self.error.show()
            return
        if self.account.currentData() is None:
            self.error.setText("Select an expense account.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> CategoryInput:
        return CategoryInput(
            code=self.code.text().strip().upper(),
            name=self.name.text().strip(),
            expense_account_id=str(self.account.currentData()),
            description=self.description.toPlainText().strip() or None,
        )


class ExpenseClaimDialog(QDialog):
    def __init__(
        self,
        employees: list[Employee],
        categories: list[ExpenseCategory],
        currency: str,
        claim: ExpenseClaim | None = None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._employees = {str(employee.id): employee for employee in employees}
        self._categories = {str(category.id): category for category in categories}
        self._currency = currency
        self._claim = claim
        self.setWindowTitle("New expense claim" if claim is None else "Edit expense claim")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel("New expense claim" if self._claim is None else "Edit expense claim")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "Claims remain editable as drafts. Submit only after the receipt details and "
            "business purpose have been checked."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.employee = QComboBox()
        for employee in self._employees.values():
            if employee.status not in {"active", "probation"}:
                continue
            self.employee.addItem(
                f"{employee.employee_number} · {employee.full_name}", str(employee.id)
            )
        self.category = QComboBox()
        for category in self._categories.values():
            if category.status == "active":
                self.category.addItem(f"{category.code} · {category.name}", str(category.id))
        self.expense_date = QDateEdit(
            _qdate(self._claim.expense_date) if self._claim else QDate.currentDate()
        )
        self.expense_date.setCalendarPopup(True)
        self.expense_date.setDisplayFormat("dd MMM yyyy")
        self.amount = _amount_editor(
            self._claim.amount if self._claim else "1000.00", self._currency
        )
        self.description = QTextEdit(self._claim.description if self._claim else "")
        self.description.setPlaceholderText("Business purpose and expense details")
        self.description.setFixedHeight(74)
        self.receipt_number = QLineEdit(self._claim.receipt_number or "" if self._claim else "")
        self.receipt_number.setPlaceholderText("Receipt or invoice number")
        self.receipt_date_enabled = QCheckBox("Receipt date recorded")
        self.receipt_date = QDateEdit(
            _qdate(self._claim.receipt_date)
            if self._claim and self._claim.receipt_date
            else QDate.currentDate()
        )
        self.receipt_date.setCalendarPopup(True)
        self.receipt_date.setDisplayFormat("dd MMM yyyy")
        self.receipt_date.setEnabled(bool(self._claim and self._claim.receipt_date))
        self.receipt_date_enabled.setChecked(bool(self._claim and self._claim.receipt_date))
        self.receipt_date_enabled.toggled.connect(self.receipt_date.setEnabled)
        self.receipt_merchant = QLineEdit(self._claim.receipt_merchant or "" if self._claim else "")
        self.receipt_merchant.setPlaceholderText("Merchant or supplier")
        if self._claim:
            employee_index = self.employee.findData(str(self._claim.employee_id))
            category_index = self.category.findData(str(self._claim.category_id))
            if employee_index >= 0:
                self.employee.setCurrentIndex(employee_index)
            if category_index >= 0:
                self.category.setCurrentIndex(category_index)
        form.addRow("Employee", self.employee)
        form.addRow("Category", self.category)
        form.addRow("Expense date", self.expense_date)
        form.addRow("Amount", self.amount)
        form.addRow("Description", self.description)
        form.addRow("Receipt number", self.receipt_number)
        form.addRow("", self.receipt_date_enabled)
        form.addRow("Receipt date", self.receipt_date)
        form.addRow("Merchant", self.receipt_merchant)
        root.addLayout(form)

        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()
        root.addWidget(self.error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setText("Save draft")
            save.setProperty("role", "primary")
            save.setEnabled(bool(self._employees) and bool(self._categories))
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self.employee.currentData() is None or self.category.currentData() is None:
            self.error.setText("Select an employee and an active expense category.")
            self.error.show()
            return
        employee = self._employees.get(str(self.employee.currentData()))
        expense_date = self.expense_date.date().toPyDate()
        if employee is not None and expense_date < employee.hire_date:
            self.error.setText("The expense date cannot precede the employee's hire date.")
            self.error.show()
            return
        if (
            employee is not None
            and employee.termination_date is not None
            and expense_date > employee.termination_date
        ):
            self.error.setText("The expense date cannot follow the employee's termination date.")
            self.error.show()
            return
        if (
            self.receipt_date_enabled.isChecked()
            and self.receipt_date.date() < self.expense_date.date()
        ):
            self.error.setText("The receipt date must not precede the expense date.")
            self.error.show()
            return
        if len(self.description.toPlainText().strip()) < 3:
            self.error.setText("Enter a business purpose of at least three characters.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> ClaimInput:
        return ClaimInput(
            employee_id=str(self.employee.currentData()),
            category_id=str(self.category.currentData()),
            expense_date=self.expense_date.date().toPyDate(),
            description=self.description.toPlainText().strip(),
            amount=_amount(self.amount),
            receipt_number=self.receipt_number.text().strip() or None,
            receipt_date=(
                self.receipt_date.date().toPyDate()
                if self.receipt_date_enabled.isChecked()
                else None
            ),
            receipt_merchant=self.receipt_merchant.text().strip() or None,
        )


class ReimbursementDialog(QDialog):
    def __init__(
        self, claim: ExpenseClaim, currency: str, *, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._claim = claim
        self.setWindowTitle("Reimburse expense claim")
        self.setModal(True)
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel(f"Reimburse {claim.claim_number}")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(f"{claim.employee_name} · {money(claim.amount, currency)}")
        note.setProperty("role", "muted")
        root.addWidget(note)
        form = QFormLayout()
        form.setSpacing(10)
        self.paid_on = QDateEdit(QDate.currentDate())
        self.paid_on.setCalendarPopup(True)
        self.paid_on.setDisplayFormat("dd MMM yyyy")
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("Bank or payment reference")
        form.addRow("Paid on", self.paid_on)
        form.addRow("Reference", self.reference)
        root.addLayout(form)
        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.hide()
        root.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setText("Reimburse")
            save.setProperty("role", "primary")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self.paid_on.date() < _qdate(self._claim.expense_date):
            self.error.setText("The payment date must not precede the expense date.")
            self.error.show()
            return
        if len(self.reference.text().strip()) < 2:
            self.error.setText("Enter the payment reference.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> ReimbursementInput:
        return ReimbursementInput(
            paid_on=self.paid_on.date().toPyDate(),
            reference=self.reference.text().strip(),
        )


class ExpensesPage(BasePage):
    def __init__(self, session: ApplicationSession) -> None:
        super().__init__(session)
        self._accounts: dict[str, Account] = {}
        self._categories: dict[str, ExpenseCategory] = {}
        self._employees: dict[str, Employee] = {}
        self._claims: dict[str, ExpenseClaim] = {}
        self._page = 1
        self._total = 0
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 26, 32, 30)
        root.setSpacing(16)
        header = QHBoxLayout()
        header.addWidget(
            PageHeader(
                "Expenses",
                "Receipt-backed claims, approval controls and cash reimbursement.",
            ),
            1,
        )
        self.new_category_button = QPushButton("New category")
        self.new_category_button.setProperty("role", "primary")
        self.new_category_button.clicked.connect(self._new_category)
        header.addWidget(self.new_category_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header)
        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_categories_tab(), "Categories")
        self.tabs.addTab(self._build_claims_tab(), "Claims")
        root.addWidget(self.tabs, 1)
        self._update_actions()

    def _build_categories_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Status"))
        self.category_status_filter = QComboBox()
        self.category_status_filter.addItem("All categories", "")
        self.category_status_filter.addItem("Active", "active")
        self.category_status_filter.addItem("Archived", "archived")
        self.category_status_filter.currentIndexChanged.connect(self._render_categories)
        toolbar.addWidget(self.category_status_filter)
        toolbar.addStretch(1)
        self.category_count = QLabel("")
        self.category_count.setProperty("role", "muted")
        toolbar.addWidget(self.category_count)
        refresh = QPushButton("Refresh")
        refresh.setProperty("role", "ghost")
        refresh.clicked.connect(self.refresh)
        toolbar.addWidget(refresh)
        layout.addLayout(toolbar)
        self.categories_table = QTableWidget()
        configure_table(
            self.categories_table,
            ["Code", "Name", "Expense account", "Description", "Status"],
            stretch_column=1,
        )
        self.categories_table.itemSelectionChanged.connect(self._update_actions)
        layout.addWidget(self.categories_table, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.edit_category_button = QPushButton("Edit category")
        self.edit_category_button.clicked.connect(self._edit_category)
        self.toggle_category_button = QPushButton("Archive")
        self.toggle_category_button.setProperty("role", "ghost")
        self.toggle_category_button.clicked.connect(self._toggle_category)
        actions.addWidget(self.edit_category_button)
        actions.addWidget(self.toggle_category_button)
        layout.addLayout(actions)
        return tab

    def _build_claims_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Status"))
        self.claim_status_filter = QComboBox()
        self.claim_status_filter.addItem("All claims", "")
        for status in ("draft", "submitted", "approved", "rejected", "reimbursed", "cancelled"):
            self.claim_status_filter.addItem(status.title(), status)
        self.claim_status_filter.currentIndexChanged.connect(self._filter_claims)
        toolbar.addWidget(self.claim_status_filter)
        toolbar.addStretch(1)
        self.claim_count = QLabel("")
        self.claim_count.setProperty("role", "muted")
        toolbar.addWidget(self.claim_count)
        self.new_claim_button = QPushButton("New claim")
        self.new_claim_button.setProperty("role", "primary")
        self.new_claim_button.clicked.connect(self._new_claim)
        toolbar.addWidget(self.new_claim_button)
        layout.addLayout(toolbar)
        self.claims_table = QTableWidget()
        configure_table(
            self.claims_table,
            ["Claim", "Employee", "Category", "Expense date", "Amount", "Receipt", "Status"],
            stretch_column=1,
        )
        self.claims_table.itemSelectionChanged.connect(self._update_actions)
        layout.addWidget(self.claims_table, 1)
        actions = QHBoxLayout()
        self.previous_page_button = QPushButton("Previous")
        self.previous_page_button.setProperty("role", "ghost")
        self.previous_page_button.clicked.connect(self._previous_page)
        self.next_page_button = QPushButton("Next")
        self.next_page_button.setProperty("role", "ghost")
        self.next_page_button.clicked.connect(self._next_page)
        actions.addWidget(self.previous_page_button)
        actions.addWidget(self.next_page_button)
        actions.addStretch(1)
        self.edit_claim_button = QPushButton("Edit draft")
        self.edit_claim_button.clicked.connect(self._edit_claim)
        self.submit_claim_button = QPushButton("Submit")
        self.submit_claim_button.clicked.connect(lambda: self._claim_action("submit"))
        self.approve_claim_button = QPushButton("Approve")
        self.approve_claim_button.clicked.connect(lambda: self._claim_action("approve"))
        self.reject_claim_button = QPushButton("Reject")
        self.reject_claim_button.setProperty("role", "ghost")
        self.reject_claim_button.clicked.connect(lambda: self._claim_action("reject"))
        self.cancel_claim_button = QPushButton("Cancel")
        self.cancel_claim_button.setProperty("role", "ghost")
        self.cancel_claim_button.clicked.connect(lambda: self._claim_action("cancel"))
        self.reimburse_claim_button = QPushButton("Reimburse")
        self.reimburse_claim_button.setProperty("role", "primary")
        self.reimburse_claim_button.clicked.connect(self._reimburse_claim)
        for button in (
            self.edit_claim_button,
            self.submit_claim_button,
            self.approve_claim_button,
            self.reject_claim_button,
            self.cancel_claim_button,
            self.reimburse_claim_button,
        ):
            actions.addWidget(button)
        layout.addLayout(actions)
        return tab

    def refresh(self) -> None:
        self.clear_error(self.error_label)
        self.set_status("Loading expenses…")
        company_id = self.session.selected_company_id
        status_value = self.claim_status_filter.currentData()
        status = str(status_value) if status_value else None
        page = self._page

        def load() -> ExpenseData:
            accounts = self.session.client.list_accounts(company_id)
            categories = self.session.client.list_expense_categories(company_id)
            employees: list[Employee] = []
            employee_page = 1
            while True:
                batch = self.session.client.list_employees(
                    company_id, page=employee_page, page_size=100
                )
                employees.extend(batch.items)
                if len(employees) >= batch.total or not batch.items:
                    break
                employee_page += 1
            claims = self.session.client.list_expense_claims(
                company_id, page=page, page_size=PAGE_SIZE, status=status
            )
            return ExpenseData(
                accounts=accounts,
                categories=categories,
                employees=employees,
                claims=claims,
            )

        self.runner.submit(
            load,
            on_success=self._render,
            on_failure=self._failed,
            on_finished=lambda: self.set_status("Expenses workspace ready"),
        )

    def _render(self, data: ExpenseData) -> None:
        self._accounts = {str(account.id): account for account in data.accounts}
        self._categories = {str(category.id): category for category in data.categories}
        self._employees = {str(employee.id): employee for employee in data.employees}
        self._claims = {str(claim.id): claim for claim in data.claims.items}
        self._page = data.claims.page
        self._total = data.claims.total
        self._render_categories()
        self._render_claims()
        self._update_actions()

    def _render_categories(self) -> None:
        selected = self._selected_category_id()
        filter_value = self.category_status_filter.currentData()
        status = str(filter_value) if filter_value else ""
        categories = [
            category
            for category in sorted(self._categories.values(), key=lambda item: item.name)
            if not status or category.status == status
        ]
        self.categories_table.blockSignals(True)
        self.categories_table.setRowCount(0)
        for row, category in enumerate(categories):
            self.categories_table.insertRow(row)
            item = text_item(category.code)
            item.setData(Qt.ItemDataRole.UserRole, str(category.id))
            self.categories_table.setItem(row, 0, item)
            self.categories_table.setItem(row, 1, text_item(category.name))
            self.categories_table.setItem(
                row,
                2,
                text_item(f"{category.expense_account_code} · {category.expense_account_name}"),
            )
            self.categories_table.setItem(row, 3, text_item(category.description or ""))
            self.categories_table.setCellWidget(row, 4, status_widget(category.status))
            self.categories_table.setRowHeight(row, 40)
        self.categories_table.blockSignals(False)
        self.category_count.setText(
            f"{len(categories)} categor{'y' if len(categories) == 1 else 'ies'}"
        )
        if categories:
            target = self._row_for_id(self.categories_table, selected)
            self.categories_table.selectRow(target)

    def _render_claims(self) -> None:
        selected = self._selected_claim_id()
        claims = sorted(self._claims.values(), key=lambda item: item.created_at, reverse=True)
        self.claims_table.blockSignals(True)
        self.claims_table.setRowCount(0)
        for row, claim in enumerate(claims):
            self.claims_table.insertRow(row)
            item = text_item(claim.claim_number)
            item.setData(Qt.ItemDataRole.UserRole, str(claim.id))
            self.claims_table.setItem(row, 0, item)
            self.claims_table.setItem(row, 1, text_item(claim.employee_name))
            self.claims_table.setItem(
                row, 2, text_item(f"{claim.category_code} · {claim.category_name}")
            )
            self.claims_table.setItem(row, 3, text_item(date_label(claim.expense_date)))
            self.claims_table.setItem(
                row, 4, text_item(money(claim.amount, claim.currency), numeric=True)
            )
            receipt = claim.receipt_number or claim.receipt_merchant or "Not recorded"
            self.claims_table.setItem(row, 5, text_item(receipt))
            self.claims_table.setCellWidget(row, 6, status_widget(claim.status))
            self.claims_table.setRowHeight(row, 40)
        self.claims_table.blockSignals(False)
        start = (self._page - 1) * PAGE_SIZE + 1 if self._total else 0
        end = min(self._page * PAGE_SIZE, self._total)
        self.claim_count.setText(
            f"{start} to {end} of {self._total} claim(s)" if self._total else "No claims"
        )
        self.previous_page_button.setEnabled(self._page > 1)
        self.next_page_button.setEnabled(self._page * PAGE_SIZE < self._total)
        if claims:
            target = self._row_for_id(self.claims_table, selected)
            self.claims_table.selectRow(target)

    @staticmethod
    def _row_for_id(table: QTableWidget, selected: str | None) -> int:
        if selected is None:
            return 0
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and str(item.data(Qt.ItemDataRole.UserRole)) == selected:
                return row
        return 0

    def _selected_category_id(self) -> str | None:
        row = self.categories_table.currentRow()
        item = self.categories_table.item(row, 0) if row >= 0 else None
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return str(value) if value else None

    def _selected_claim_id(self) -> str | None:
        row = self.claims_table.currentRow()
        item = self.claims_table.item(row, 0) if row >= 0 else None
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return str(value) if value else None

    def _selected_category(self) -> ExpenseCategory | None:
        category_id = self._selected_category_id()
        return self._categories.get(category_id) if category_id else None

    def _selected_claim(self) -> ExpenseClaim | None:
        claim_id = self._selected_claim_id()
        return self._claims.get(claim_id) if claim_id else None

    def _update_actions(self) -> None:
        category = self._selected_category()
        claim = self._selected_claim()
        can_manage = self.session.can("expense.manage")
        can_submit = self.session.can("expense.submit")
        can_approve = self.session.can("expense.approve")
        can_pay = self.session.can("expense.pay")
        is_requester = claim is not None and claim.requested_by == self.session.me.user.id
        self.new_category_button.setEnabled(can_manage)
        self.edit_category_button.setEnabled(can_manage and category is not None)
        self.toggle_category_button.setEnabled(can_manage and category is not None)
        self.toggle_category_button.setText(
            "Restore" if category is not None and category.status == "archived" else "Archive"
        )
        self.new_claim_button.setEnabled(can_submit)
        self.edit_claim_button.setEnabled(
            can_submit
            and claim is not None
            and claim.status == "draft"
            and (is_requester or can_manage)
        )
        self.submit_claim_button.setEnabled(
            can_submit
            and claim is not None
            and claim.status == "draft"
            and (is_requester or can_manage)
        )
        self.approve_claim_button.setEnabled(
            can_approve and claim is not None and claim.status == "submitted" and not is_requester
        )
        self.reject_claim_button.setEnabled(
            can_approve and claim is not None and claim.status == "submitted" and not is_requester
        )
        self.cancel_claim_button.setEnabled(
            claim is not None
            and claim.status in {"draft", "submitted"}
            and (is_requester or can_approve)
        )
        self.reimburse_claim_button.setEnabled(
            can_pay
            and claim is not None
            and claim.status == "approved"
            and not is_requester
            and claim.decided_by != self.session.me.user.id
        )

    def _failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The expenses workspace could not be loaded.")
        self.set_status("Expense refresh failed")

    def _action_failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The expense action could not be completed.")
        self._update_actions()

    def _new_category(self) -> None:
        accounts = [
            account
            for account in self._accounts.values()
            if account.account_type == "expense" and account.is_active
        ]
        dialog = ExpenseCategoryDialog(accounts, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.runner.submit(
            lambda: self.session.client.create_expense_category(
                company_id=self.session.selected_company_id,
                code=payload.code,
                name=payload.name,
                expense_account_id=payload.expense_account_id,
                description=payload.description,
            ),
            on_success=lambda _category: self._action_done("Expense category created"),
            on_failure=self._action_failed,
        )

    def _edit_category(self) -> None:
        category = self._selected_category()
        if category is None:
            return
        accounts = [
            account
            for account in self._accounts.values()
            if account.account_type == "expense" and account.is_active
        ]
        dialog = ExpenseCategoryDialog(accounts, category, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self._run_category_update(category, payload)

    def _toggle_category(self) -> None:
        category = self._selected_category()
        if category is None:
            return
        new_status = "archived" if category.status == "active" else "active"
        if new_status == "archived":
            answer = QMessageBox.question(
                self,
                "Archive expense category",
                f"Archive {category.name}? New claims will no longer use it.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        payload = CategoryInput(
            code=category.code,
            name=category.name,
            expense_account_id=str(category.expense_account_id),
            description=category.description,
        )
        self._run_category_update(category, payload, status=new_status)

    def _run_category_update(
        self, category: ExpenseCategory, payload: CategoryInput, *, status: str | None = None
    ) -> None:
        self.runner.submit(
            lambda: self.session.client.update_expense_category(
                str(category.id),
                version=category.version,
                name=payload.name,
                expense_account_id=payload.expense_account_id,
                description=payload.description,
                status=status,
            ),
            on_success=lambda _category: self._action_done("Expense category updated"),
            on_failure=self._action_failed,
        )

    def _new_claim(self) -> None:
        employees = [
            employee
            for employee in self._employees.values()
            if employee.status in {"active", "probation"}
        ]
        categories = [
            category for category in self._categories.values() if category.status == "active"
        ]
        dialog = ExpenseClaimDialog(
            employees,
            categories,
            self.session.company.base_currency,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_claim(dialog.payload())

    def _edit_claim(self) -> None:
        claim = self._selected_claim()
        if claim is None:
            return
        employees = [
            employee
            for employee in self._employees.values()
            if employee.status in {"active", "probation"}
        ]
        categories = [
            category for category in self._categories.values() if category.status == "active"
        ]
        dialog = ExpenseClaimDialog(
            employees,
            categories,
            self.session.company.base_currency,
            claim,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_claim(dialog.payload(), claim)

    def _save_claim(self, payload: ClaimInput, claim: ExpenseClaim | None = None) -> None:
        if claim is None:

            def operation() -> ExpenseClaim:
                return self.session.client.create_expense_claim(
                    company_id=self.session.selected_company_id,
                    employee_id=payload.employee_id,
                    category_id=payload.category_id,
                    expense_date=payload.expense_date,
                    description=payload.description,
                    amount=payload.amount,
                    receipt_number=payload.receipt_number,
                    receipt_date=payload.receipt_date,
                    receipt_merchant=payload.receipt_merchant,
                )

            message = "Expense claim saved as draft"
        else:

            def operation() -> ExpenseClaim:
                return self.session.client.update_expense_claim(
                    str(claim.id),
                    version=claim.version,
                    employee_id=payload.employee_id,
                    category_id=payload.category_id,
                    expense_date=payload.expense_date,
                    description=payload.description,
                    amount=payload.amount,
                    receipt_number=payload.receipt_number,
                    receipt_date=payload.receipt_date,
                    receipt_merchant=payload.receipt_merchant,
                )

            message = "Expense draft updated"
        self.runner.submit(
            operation,
            on_success=lambda _claim: self._action_done(message),
            on_failure=self._action_failed,
        )

    def _claim_action(self, action: str) -> None:
        claim = self._selected_claim()
        if claim is None:
            return
        note: str | None = None
        if action == "reject":
            note, accepted = QInputDialog.getText(self, "Reject expense claim", "Reason")
            if not accepted or not note.strip():
                return
        elif action == "cancel":
            note, accepted = QInputDialog.getText(self, "Cancel expense claim", "Note (optional)")
            if not accepted:
                return
        self.runner.submit(
            lambda: self.session.client.expense_action(
                str(claim.id), action, version=claim.version, note=note or None
            ),
            on_success=lambda _claim: self._action_done(
                {
                    "submit": "Expense claim submitted",
                    "approve": "Expense claim approved",
                    "reject": "Expense claim rejected",
                    "cancel": "Expense claim cancelled",
                }[action]
            ),
            on_failure=self._action_failed,
        )

    def _reimburse_claim(self) -> None:
        claim = self._selected_claim()
        if claim is None:
            return
        dialog = ReimbursementDialog(claim, self.session.company.base_currency, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.runner.submit(
            lambda: self.session.client.reimburse_expense_claim(
                str(claim.id),
                version=claim.version,
                paid_on=payload.paid_on,
                reference=payload.reference,
            ),
            on_success=lambda _claim: self._action_done("Expense claim reimbursed"),
            on_failure=self._action_failed,
        )

    def _action_done(self, message: str) -> None:
        self.set_status(message)
        self.refresh()

    def _filter_claims(self) -> None:
        self._page = 1
        self.refresh()

    def _previous_page(self) -> None:
        if self._page > 1:
            self._page -= 1
            self.refresh()

    def _next_page(self) -> None:
        if self._page * PAGE_SIZE < self._total:
            self._page += 1
            self.refresh()
