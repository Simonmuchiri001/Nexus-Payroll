"""People directory and employee onboarding."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from PyQt6.QtCore import QDate, Qt, QTimer
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from nexus_desktop.api.client import ApiError, NetworkError
from nexus_desktop.api.models import EmployeePage
from nexus_desktop.state import ApplicationSession
from nexus_desktop.ui.common import (
    PageHeader,
    configure_table,
    date_label,
    status_widget,
    text_item,
)
from nexus_desktop.ui.pages.base import BasePage

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmployeeDialog(QDialog):
    def __init__(self, session: ApplicationSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Add employee")
        self.setModal(True)
        self.setMinimumWidth(510)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(16)

        heading = QLabel("Employee record")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel("Create the person and their first effective compensation record together.")
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.first_name = QLineEdit()
        self.first_name.setPlaceholderText("First name")
        self.last_name = QLineEdit()
        self.last_name.setPlaceholderText("Last name")
        self.job_title = QLineEdit()
        self.job_title.setPlaceholderText("e.g. Finance Analyst")
        self.work_email = QLineEdit()
        self.work_email.setPlaceholderText("Optional work email")
        self.employee_number = QLineEdit()
        self.employee_number.setPlaceholderText("Leave blank to generate")
        self.hire_date = QDateEdit(QDate.currentDate())
        self.hire_date.setCalendarPopup(True)
        self.hire_date.setDisplayFormat("dd MMM yyyy")
        self.status = QComboBox()
        self.status.addItem("Active", "active")
        self.status.addItem("Probation", "probation")
        self.base_salary = self._money_field("Basic salary")
        self.housing_allowance = self._money_field("Housing allowance")
        self.transport_allowance = self._money_field("Transport allowance")
        self.other_allowance = self._money_field("Other allowance")
        form.addRow("First name", self.first_name)
        form.addRow("Last name", self.last_name)
        form.addRow("Job title", self.job_title)
        form.addRow("Work email", self.work_email)
        form.addRow("Employee number", self.employee_number)
        form.addRow("Hire date", self.hire_date)
        form.addRow("Status", self.status)
        form.addRow("Basic salary", self.base_salary)
        form.addRow("Housing", self.housing_allowance)
        form.addRow("Transport", self.transport_allowance)
        form.addRow("Other allowance", self.other_allowance)
        root.addLayout(form)

        currency_note = QLabel(
            f"Compensation is recorded in {session.company.base_currency} and starts on the hire date."
        )
        currency_note.setProperty("role", "muted")
        currency_note.setWordWrap(True)
        root.addWidget(currency_note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_button is not None:
            save_button.setText("Create employee")
            save_button.setProperty("role", "primary")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _money_field(placeholder: str) -> QLineEdit:
        field = QLineEdit("0.00")
        field.setPlaceholderText(placeholder)
        validator = QDoubleValidator(0.0, 1_000_000_000_000.0, 2)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        field.setValidator(validator)
        field.setAlignment(Qt.AlignmentFlag.AlignRight)
        return field

    @staticmethod
    def _decimal(field: QLineEdit) -> Decimal:
        try:
            return Decimal(field.text().strip() or "0").quantize(Decimal("0.01"))
        except InvalidOperation:
            return Decimal("0.00")

    def _validate_and_accept(self) -> None:
        if not self.first_name.text().strip():
            QMessageBox.warning(self, "Employee", "Enter the employee's first name.")
            return
        if not self.last_name.text().strip():
            QMessageBox.warning(self, "Employee", "Enter the employee's last name.")
            return
        if not self.job_title.text().strip():
            QMessageBox.warning(self, "Employee", "Enter the employee's job title.")
            return
        email = self.work_email.text().strip()
        if email and not EMAIL_PATTERN.fullmatch(email):
            QMessageBox.warning(self, "Employee", "Enter a valid work email or leave it blank.")
            return
        amounts = (
            self._decimal(self.base_salary),
            self._decimal(self.housing_allowance),
            self._decimal(self.transport_allowance),
            self._decimal(self.other_allowance),
        )
        if sum(amounts) <= 0:
            QMessageBox.warning(
                self, "Employee", "The compensation package must contain a positive amount."
            )
            return
        self.accept()

    def payload(self) -> dict[str, object]:
        hire_date = self.hire_date.date().toPyDate()
        employee_number = self.employee_number.text().strip()
        work_email = self.work_email.text().strip()
        return {
            "company_id": self.session.selected_company_id,
            "employee_number": employee_number or None,
            "first_name": self.first_name.text().strip(),
            "last_name": self.last_name.text().strip(),
            "work_email": work_email or None,
            "job_title": self.job_title.text().strip(),
            "hire_date": hire_date.isoformat(),
            "status": self.status.currentData(),
            "is_payroll_eligible": True,
            "initial_compensation": {
                "base_salary": str(self._decimal(self.base_salary)),
                "housing_allowance": str(self._decimal(self.housing_allowance)),
                "transport_allowance": str(self._decimal(self.transport_allowance)),
                "other_allowance": str(self._decimal(self.other_allowance)),
                "currency": self.session.company.base_currency,
                "effective_from": hire_date.isoformat(),
            },
        }


class EmployeesPage(BasePage):
    def __init__(self, session: ApplicationSession) -> None:
        super().__init__(session)
        self.page = 1
        self.page_size = 25
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(320)
        self._search_timer.timeout.connect(self._reset_and_refresh)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 26, 32, 30)
        root.setSpacing(16)

        header_row = QHBoxLayout()
        header_row.addWidget(
            PageHeader(
                "People",
                "Employee records, status and payroll eligibility for the selected company.",
            ),
            1,
        )
        self.add_button = QPushButton("Add employee")
        self.add_button.setProperty("role", "primary")
        self.add_button.setEnabled(self.session.can("employee.create"))
        self.add_button.clicked.connect(self._open_employee_dialog)
        header_row.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header_row)

        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, number or email")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._schedule_search)
        self.status_filter = QComboBox()
        self.status_filter.addItem("All statuses", "")
        for value, label in (
            ("active", "Active"),
            ("probation", "Probation"),
            ("suspended", "Suspended"),
            ("terminated", "Terminated"),
        ):
            self.status_filter.addItem(label, value)
        self.status_filter.currentIndexChanged.connect(self._reset_and_refresh)
        controls.addWidget(self.search, 1)
        controls.addWidget(self.status_filter)
        root.addLayout(controls)

        self.table = QTableWidget()
        configure_table(
            self.table,
            ["Employee", "Number", "Job title", "Hire date", "Status"],
            stretch_column=2,
        )
        self.table.setMinimumHeight(360)
        root.addWidget(self.table, 1)

        self.empty_label = QLabel("No employee records match this view.")
        self.empty_label.setProperty("role", "muted")
        self.empty_label.hide()
        root.addWidget(self.empty_label)

        footer = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "muted")
        self.previous_button = QPushButton("Previous")
        self.previous_button.setProperty("role", "ghost")
        self.next_button = QPushButton("Next")
        self.next_button.setProperty("role", "ghost")
        self.previous_button.clicked.connect(self._previous_page)
        self.next_button.clicked.connect(self._next_page)
        footer.addWidget(self.count_label, 1)
        footer.addWidget(self.previous_button)
        footer.addWidget(self.next_button)
        root.addLayout(footer)

    def _schedule_search(self) -> None:
        self._search_timer.start()

    def _reset_and_refresh(self) -> None:
        self.page = 1
        self.refresh()

    def _previous_page(self) -> None:
        if self.page > 1:
            self.page -= 1
            self.refresh()

    def _next_page(self) -> None:
        self.page += 1
        self.refresh()

    def refresh(self) -> None:
        self.clear_error(self.error_label)
        self.set_status("Loading people…")
        company_id = self.session.selected_company_id
        page = self.page
        query = self.search.text().strip() or None
        status_value = self.status_filter.currentData()
        status = str(status_value) if status_value else None

        def load() -> EmployeePage:
            return self.session.client.list_employees(
                company_id,
                page=page,
                page_size=self.page_size,
                query=query,
                status=status,
            )

        self.runner.submit(
            load,
            on_success=self._render,
            on_failure=self._failed,
            on_finished=lambda: self.set_status("People directory ready"),
        )

    def _failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "People could not be loaded. Try again.")

    def _render(self, page: EmployeePage) -> None:
        self.table.setRowCount(0)
        for row_index, employee in enumerate(page.items):
            self.table.insertRow(row_index)
            self.table.setItem(row_index, 0, text_item(employee.full_name))
            self.table.setItem(row_index, 1, text_item(employee.employee_number))
            self.table.setItem(row_index, 2, text_item(employee.job_title))
            self.table.setItem(row_index, 3, text_item(date_label(employee.hire_date)))
            self.table.setCellWidget(row_index, 4, status_widget(employee.status))
            self.table.setRowHeight(row_index, 42)

        self.empty_label.setVisible(page.total == 0)
        start = (page.page - 1) * page.page_size + 1 if page.total else 0
        end = min(page.page * page.page_size, page.total)
        self.count_label.setText(
            f"Showing {start}-{end} of {page.total}" if page.total else "No people found"
        )
        self.previous_button.setEnabled(page.page > 1)
        self.next_button.setEnabled(end < page.total)

    def _open_employee_dialog(self) -> None:
        dialog = EmployeeDialog(self.session, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.add_button.setEnabled(False)
        self.set_status("Creating employee…")
        self.runner.submit(
            lambda: self.session.client.create_employee(payload),
            on_success=self._created,
            on_failure=self._create_failed,
            on_finished=lambda: self.add_button.setEnabled(self.session.can("employee.create")),
        )

    def _created(self, _employee: object) -> None:
        self.set_status("Employee created")
        self.page = 1
        self.refresh()

    def _create_failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            QMessageBox.warning(self, "Employee not created", exception.message)
        else:
            QMessageBox.warning(
                self,
                "Employee not created",
                "The employee could not be created. Try again.",
            )
