"""Leave requests, balances, attendance and leave configuration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nexus_desktop.api.client import ApiError, NetworkError
from nexus_desktop.api.models import (
    AttendancePage,
    AttendanceRecord,
    Employee,
    Holiday,
    LeaveBalance,
    LeaveRequest,
    LeaveRequestPage,
    LeaveType,
)
from nexus_desktop.state import ApplicationSession
from nexus_desktop.ui.common import (
    PageHeader,
    configure_table,
    date_label,
    status_widget,
    text_item,
)
from nexus_desktop.ui.pages.base import BasePage


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _today() -> date:
    return QDate.currentDate().toPyDate()


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _month_end(value: date) -> date:
    next_month = value.replace(
        year=value.year + (1 if value.month == 12 else 0),
        month=1 if value.month == 12 else value.month + 1,
    )
    return next_month - timedelta(days=1)


def _decimal_input(label: str, value: str = "0.00", *, maximum: str = "366.00") -> QLineEdit:
    editor = QLineEdit(value)
    editor.setPlaceholderText(f"{label} (0-{maximum})")
    return editor


def _parse_days(editor: QLineEdit, label: str) -> Decimal:
    try:
        value = Decimal(editor.text().strip())
    except InvalidOperation as exc:
        raise ValueError(f"Enter {label.lower()} as a number.") from exc
    if value < 0 or value > Decimal("366"):
        raise ValueError(f"{label} must be between 0 and 366 days.")
    if value.as_tuple().exponent < -2:  # type: ignore[operator]
        raise ValueError(f"{label} supports at most two decimal places.")
    return value


@dataclass(frozen=True, slots=True)
class LeaveRequestInput:
    employee_id: str
    leave_type_id: str
    start_date: date
    end_date: date
    reason: str


@dataclass(frozen=True, slots=True)
class LeaveTypeInput:
    company_id: str
    code: str
    name: str
    description: str | None
    is_paid: bool
    requires_balance: bool
    annual_entitlement_days: str
    max_carryover_days: str


@dataclass(frozen=True, slots=True)
class HolidayInput:
    company_id: str
    holiday_date: date
    name: str


@dataclass(frozen=True, slots=True)
class AttendanceInput:
    employee_id: str
    work_date: date
    status: str
    note: str | None


@dataclass(frozen=True, slots=True)
class LeaveData:
    requests: LeaveRequestPage
    types: list[LeaveType]
    employees: list[Employee]
    holidays: list[Holiday]
    attendance: AttendancePage


class LeaveRequestDialog(QDialog):
    def __init__(
        self,
        session: ApplicationSession,
        employees: list[Employee],
        types: list[LeaveType],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self._employees = employees
        self._types = types
        self.setWindowTitle("Record leave request")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        heading = QLabel("Record leave request")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "Working days exclude weekends and company holidays. The request "
            "consumes the employee's balance while it is pending approval."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.employee = QComboBox()
        for employee in self._employees:
            self.employee.addItem(
                f"{employee.employee_number} · {employee.first_name} {employee.last_name}",
                str(employee.id),
            )
        self.leave_type = QComboBox()
        for leave_type in self._types:
            self.leave_type.addItem(leave_type.name, str(leave_type.id))
        today = QDate.currentDate()
        self.start_date = QDateEdit(today)
        self.end_date = QDateEdit(today)
        for editor in (self.start_date, self.end_date):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd MMM yyyy")
        self.reason = QTextEdit()
        self.reason.setPlaceholderText("Reason for the leave")
        self.reason.setFixedHeight(80)
        form.addRow("Employee", self.employee)
        form.addRow("Leave type", self.leave_type)
        form.addRow("Start date", self.start_date)
        form.addRow("End date", self.end_date)
        form.addRow("Reason", self.reason)
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
            save.setText("Record request")
            save.setProperty("role", "primary")
            save.setEnabled(bool(self._employees) and bool(self._types))
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self.employee.currentData() is None:
            self.error.setText("Select an employee.")
            self.error.show()
            return
        if self.leave_type.currentData() is None:
            self.error.setText("Create a leave type before recording leave.")
            self.error.show()
            return
        if self.end_date.date() < self.start_date.date():
            self.error.setText("The end date must not precede the start date.")
            self.error.show()
            return
        if self.reason.toPlainText().strip():
            self.accept()
            return
        self.error.setText("Enter a reason for the leave.")
        self.error.show()

    def payload(self) -> LeaveRequestInput:
        return LeaveRequestInput(
            employee_id=str(self.employee.currentData()),
            leave_type_id=str(self.leave_type.currentData()),
            start_date=self.start_date.date().toPyDate(),
            end_date=self.end_date.date().toPyDate(),
            reason=self.reason.toPlainText().strip(),
        )


class LeaveTypeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create leave type")
        self.setModal(True)
        self.setMinimumWidth(480)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel("Create leave type")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "Entitlements apply when a balance is initialised for a leave year. "
            "Changing the type later does not rewrite existing balances."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.code = QLineEdit()
        self.code.setPlaceholderText("ANNUAL")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Annual leave")
        self.description = QLineEdit()
        self.description.setPlaceholderText("Optional description")
        self.entitlement = _decimal_input("Annual entitlement", "21.00")
        self.carryover = _decimal_input("Maximum carryover", "0.00")
        self.is_paid = QCheckBox("Paid leave")
        self.is_paid.setChecked(True)
        self.requires_balance = QCheckBox("Requires a leave balance")
        self.requires_balance.setChecked(True)
        form.addRow("Code", self.code)
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        form.addRow("Annual entitlement (days)", self.entitlement)
        form.addRow("Maximum carryover (days)", self.carryover)
        form.addRow("", self.is_paid)
        form.addRow("", self.requires_balance)
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
            save.setText("Create type")
            save.setProperty("role", "primary")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if len(self.code.text().strip()) < 2:
            self.error.setText("Enter a leave type code.")
            self.error.show()
            return
        if len(self.name.text().strip()) < 2:
            self.error.setText("Enter a leave type name.")
            self.error.show()
            return
        try:
            entitlement = _parse_days(self.entitlement, "Annual entitlement")
            carryover = _parse_days(self.carryover, "Maximum carryover")
        except ValueError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        if carryover > entitlement:
            self.error.setText("Maximum carryover cannot exceed the annual entitlement.")
            self.error.show()
            return
        self.accept()

    def payload(self, *, company_id: str) -> LeaveTypeInput:
        return LeaveTypeInput(
            company_id=company_id,
            code=self.code.text().strip().upper(),
            name=self.name.text().strip(),
            description=self.description.text().strip() or None,
            is_paid=self.is_paid.isChecked(),
            requires_balance=self.requires_balance.isChecked(),
            annual_entitlement_days=str(_parse_days(self.entitlement, "Annual entitlement")),
            max_carryover_days=str(_parse_days(self.carryover, "Maximum carryover")),
        )


class HolidayDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add company holiday")
        self.setModal(True)
        self.setMinimumWidth(420)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel("Add company holiday")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        form = QFormLayout()
        form.setSpacing(10)
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("dd MMM yyyy")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Holiday name")
        form.addRow("Date", self.date)
        form.addRow("Name", self.name)
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
            save.setText("Add holiday")
            save.setProperty("role", "primary")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if len(self.name.text().strip()) < 2:
            self.error.setText("Enter the holiday name.")
            self.error.show()
            return
        self.accept()

    def payload(self, *, company_id: str) -> HolidayInput:
        return HolidayInput(
            company_id=company_id,
            holiday_date=self.date.date().toPyDate(),
            name=self.name.text().strip(),
        )


class AttendanceDialog(QDialog):
    def __init__(
        self,
        session: ApplicationSession,
        employees: list[Employee],
        record: AttendanceRecord | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self._employees = employees
        self._record = record
        self.setWindowTitle("Edit attendance" if record else "Record attendance")
        self.setModal(True)
        self.setMinimumWidth(440)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel("Edit attendance" if self._record else "Record attendance")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "Attendance is recorded for weekdays only. Approved leave is owned "
            "by the leave request and cannot be edited here."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)
        form = QFormLayout()
        form.setSpacing(10)
        self.employee = QComboBox()
        for employee in self._employees:
            self.employee.addItem(
                f"{employee.employee_number} · {employee.first_name} {employee.last_name}",
                str(employee.id),
            )
        self.work_date = QDateEdit(
            _qdate(self._record.work_date) if self._record else QDate.currentDate()
        )
        self.work_date.setCalendarPopup(True)
        self.work_date.setDisplayFormat("dd MMM yyyy")
        self.status = QComboBox()
        for label, value in (
            ("Present", "present"),
            ("Remote", "remote"),
            ("Absent", "absent"),
            ("Holiday", "holiday"),
        ):
            self.status.addItem(label, value)
        if self._record is not None:
            index = self.status.findData(self._record.status)
            if index >= 0:
                self.status.setCurrentIndex(index)
            self.employee.setEnabled(False)
            self.work_date.setEnabled(False)
        self.note = QLineEdit(self._record.note if self._record and self._record.note else "")
        self.note.setPlaceholderText("Optional note")
        form.addRow("Employee", self.employee)
        form.addRow("Work date", self.work_date)
        form.addRow("Status", self.status)
        form.addRow("Note", self.note)
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
            save.setText("Save attendance")
            save.setProperty("role", "primary")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self.employee.currentData() is None:
            self.error.setText("Select an employee.")
            self.error.show()
            return
        if self.work_date.date() > QDate.currentDate():
            self.error.setText("Attendance cannot be recorded for a future date.")
            self.error.show()
            return
        self.accept()

    def create_payload(self) -> AttendanceInput:
        return AttendanceInput(
            employee_id=str(self.employee.currentData()),
            work_date=self.work_date.date().toPyDate(),
            status=str(self.status.currentData()),
            note=self.note.text().strip() or None,
        )

    def update_payload(self) -> dict[str, object]:
        return {
            "status": str(self.status.currentData()),
            "note": self.note.text().strip() or None,
        }


class BalanceAdjustDialog(QDialog):
    def __init__(self, balance: LeaveBalance, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._balance = balance
        self.setWindowTitle("Adjust leave balance")
        self.setModal(True)
        self.setMinimumWidth(440)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel(f"Adjust {balance.leave_type_name} · {balance.leave_year}")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "The entitlement cannot be reduced below days already used or "
            "pending approval. Every adjustment is audited with its reason."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)
        form = QFormLayout()
        form.setSpacing(10)
        self.entitled = _decimal_input("Entitlement", str(balance.entitled_days))
        self.carryover = _decimal_input("Carryover", str(balance.carried_over_days))
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Reason for the adjustment")
        form.addRow("Entitlement (days)", self.entitled)
        form.addRow("Carryover (days)", self.carryover)
        form.addRow("Reason", self.reason)
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
            save.setText("Apply adjustment")
            save.setProperty("role", "primary")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        try:
            _parse_days(self.entitled, "Entitlement")
            _parse_days(self.carryover, "Carryover")
        except ValueError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        if len(self.reason.text().strip()) < 3:
            self.error.setText("Enter a reason for the adjustment.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> dict[str, object]:
        return {
            "version": self._balance.version,
            "entitled_days": str(_parse_days(self.entitled, "Entitlement")),
            "carried_over_days": str(_parse_days(self.carryover, "Carryover")),
            "reason": self.reason.text().strip(),
        }


class LeavePage(BasePage):
    def __init__(self, session: ApplicationSession) -> None:
        super().__init__(session)
        self._employees: dict[str, Employee] = {}
        self._types: dict[str, LeaveType] = {}
        self._requests: dict[str, LeaveRequest] = {}
        self._balances: dict[str, LeaveBalance] = {}
        self._attendance: dict[str, AttendanceRecord] = {}
        self._holidays: dict[str, Holiday] = {}
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 26, 32, 30)
        root.setSpacing(16)

        header = QHBoxLayout()
        header.addWidget(
            PageHeader(
                "Leave",
                "Entitlements, approval workflow, company holidays and the attendance register.",
            ),
            1,
        )
        self.record_button = QPushButton("Record leave")
        self.record_button.setProperty("role", "primary")
        self.record_button.setEnabled(self.session.can("leave.request"))
        self.record_button.clicked.connect(self._open_request)
        header.addWidget(self.record_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header)

        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_requests_tab(), "Requests")
        self.tabs.addTab(self._build_balances_tab(), "Balances")
        self.tabs.addTab(self._build_attendance_tab(), "Attendance")
        self.tabs.addTab(self._build_setup_tab(), "Setup")
        root.addWidget(self.tabs, 1)

    def _build_requests_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        self.request_status = QComboBox()
        self.request_status.addItem("All statuses", "")
        for label, value in (
            ("Pending", "pending"),
            ("Approved", "approved"),
            ("Rejected", "rejected"),
            ("Cancelled", "cancelled"),
        ):
            self.request_status.addItem(label, value)
        self.request_year = QComboBox()
        current = QDate.currentDate().year()
        self.request_year.addItem("All years", None)
        for year in (current + 1, current, current - 1, current - 2):
            self.request_year.addItem(str(year), year)
        self.request_year.setCurrentIndex(self.request_year.findData(current))
        refresh = QPushButton("Refresh")
        refresh.setProperty("role", "ghost")
        refresh.clicked.connect(self.refresh)
        self.request_status.currentIndexChanged.connect(self.refresh)
        self.request_year.currentIndexChanged.connect(self.refresh)
        filters.addWidget(QLabel("Status"))
        filters.addWidget(self.request_status)
        filters.addWidget(QLabel("Year"))
        filters.addWidget(self.request_year)
        filters.addStretch(1)
        filters.addWidget(refresh)
        layout.addLayout(filters)

        self.requests_table = QTableWidget()
        configure_table(
            self.requests_table,
            ["Employee", "Leave type", "Period", "Days", "Status", "Recorded by"],
            stretch_column=0,
        )
        self.requests_table.setMinimumHeight(300)
        self.requests_table.itemSelectionChanged.connect(self._request_selection)
        layout.addWidget(self.requests_table, 1)

        self.request_count = QLabel("")
        self.request_count.setProperty("role", "muted")
        self.approve_button = QPushButton("Approve")
        self.approve_button.setEnabled(False)
        self.approve_button.clicked.connect(lambda: self._request_action("approve"))
        self.reject_button = QPushButton("Reject")
        self.reject_button.setEnabled(False)
        self.reject_button.clicked.connect(lambda: self._request_action("reject"))
        self.cancel_button = QPushButton("Cancel leave")
        self.cancel_button.setProperty("role", "ghost")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(lambda: self._request_action("cancel"))

        footer = QHBoxLayout()
        footer.addWidget(self.request_count, 1)
        footer.addWidget(self.approve_button)
        footer.addWidget(self.reject_button)
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)
        return tab

    def _build_balances_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.balance_employee = QComboBox()
        self.balance_employee.setMinimumWidth(260)
        self.balance_year = QSpinBox()
        self.balance_year.setRange(2000, 2100)
        self.balance_year.setValue(QDate.currentDate().year())
        self.load_balances_button = QPushButton("Load balances")
        self.load_balances_button.clicked.connect(lambda: self._load_balances(False))
        self.initialize_button = QPushButton("Initialise year")
        self.initialize_button.setProperty("role", "ghost")
        self.initialize_button.setEnabled(self.session.can("leave.manage"))
        self.initialize_button.clicked.connect(lambda: self._load_balances(True))
        controls.addWidget(QLabel("Employee"))
        controls.addWidget(self.balance_employee, 1)
        controls.addWidget(QLabel("Year"))
        controls.addWidget(self.balance_year)
        controls.addWidget(self.load_balances_button)
        controls.addWidget(self.initialize_button)
        layout.addLayout(controls)

        self.balances_table = QTableWidget()
        configure_table(
            self.balances_table,
            ["Leave type", "Entitled", "Carried", "Used", "Pending", "Available"],
            stretch_column=0,
        )
        self.balances_table.setMinimumHeight(260)
        self.balances_table.itemSelectionChanged.connect(self._balance_selection)
        layout.addWidget(self.balances_table, 1)

        footer = QHBoxLayout()
        self.balance_hint = QLabel(
            "Balances are created from the leave type entitlement for the selected year."
        )
        self.balance_hint.setProperty("role", "muted")
        self.adjust_button = QPushButton("Adjust balance")
        self.adjust_button.setEnabled(False)
        self.adjust_button.clicked.connect(self._adjust_balance)
        footer.addWidget(self.balance_hint, 1)
        footer.addWidget(self.adjust_button)
        layout.addLayout(footer)
        return tab

    def _build_attendance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        today = _today()
        self.attendance_start = QDateEdit(_qdate(_month_start(today)))
        self.attendance_end = QDateEdit(_qdate(_month_end(today)))
        for editor in (self.attendance_start, self.attendance_end):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd MMM yyyy")
        self.load_attendance_button = QPushButton("Load register")
        self.load_attendance_button.clicked.connect(self._load_attendance)
        self.record_attendance_button = QPushButton("Record attendance")
        self.record_attendance_button.setProperty("role", "primary")
        self.record_attendance_button.setEnabled(self.session.can("attendance.manage"))
        self.record_attendance_button.clicked.connect(self._open_attendance)
        controls.addWidget(QLabel("From"))
        controls.addWidget(self.attendance_start)
        controls.addWidget(QLabel("To"))
        controls.addWidget(self.attendance_end)
        controls.addWidget(self.load_attendance_button)
        controls.addStretch(1)
        controls.addWidget(self.record_attendance_button)
        layout.addLayout(controls)

        self.attendance_table = QTableWidget()
        configure_table(
            self.attendance_table,
            ["Employee", "Date", "Status", "Source", "Note"],
            stretch_column=0,
        )
        self.attendance_table.setMinimumHeight(300)
        self.attendance_table.itemSelectionChanged.connect(self._attendance_selection)
        layout.addWidget(self.attendance_table, 1)

        footer = QHBoxLayout()
        self.attendance_count = QLabel("")
        self.attendance_count.setProperty("role", "muted")
        self.edit_attendance_button = QPushButton("Edit attendance")
        self.edit_attendance_button.setEnabled(False)
        self.edit_attendance_button.clicked.connect(self._edit_attendance)
        footer.addWidget(self.attendance_count, 1)
        footer.addWidget(self.edit_attendance_button)
        layout.addLayout(footer)
        return tab

    def _build_setup_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(16)

        types_card = QFrame()
        types_card.setProperty("card", "true")
        types_layout = QVBoxLayout(types_card)
        types_layout.setContentsMargins(16, 14, 16, 14)
        types_layout.setSpacing(10)
        types_head = QHBoxLayout()
        types_title = QLabel("Leave types")
        types_title.setProperty("role", "section-title")
        self.add_type_button = QPushButton("Add type")
        self.add_type_button.setEnabled(self.session.can("leave.manage"))
        self.add_type_button.clicked.connect(self._add_type)
        types_head.addWidget(types_title)
        types_head.addStretch(1)
        types_head.addWidget(self.add_type_button)
        types_layout.addLayout(types_head)

        self.types_table = QTableWidget()
        configure_table(
            self.types_table,
            ["Code", "Name", "Entitled", "Carryover", "Paid", "Status"],
            stretch_column=1,
        )
        self.types_table.itemSelectionChanged.connect(self._type_selection)
        types_layout.addWidget(self.types_table, 1)
        self.archive_type_button = QPushButton("Archive / reactivate")
        self.archive_type_button.setEnabled(False)
        self.archive_type_button.clicked.connect(self._toggle_type)
        types_layout.addWidget(self.archive_type_button)
        layout.addWidget(types_card, 3)

        holidays_card = QFrame()
        holidays_card.setProperty("card", "true")
        holidays_layout = QVBoxLayout(holidays_card)
        holidays_layout.setContentsMargins(16, 14, 16, 14)
        holidays_layout.setSpacing(10)
        holidays_head = QHBoxLayout()
        holidays_title = QLabel("Company holidays")
        holidays_title.setProperty("role", "section-title")
        self.holiday_year = QSpinBox()
        self.holiday_year.setRange(2000, 2100)
        self.holiday_year.setValue(QDate.currentDate().year())
        self.holiday_year.valueChanged.connect(self._reload_holidays)
        self.add_holiday_button = QPushButton("Add holiday")
        self.add_holiday_button.setEnabled(self.session.can("leave.manage"))
        self.add_holiday_button.clicked.connect(self._add_holiday)
        holidays_head.addWidget(holidays_title)
        holidays_head.addStretch(1)
        holidays_head.addWidget(self.holiday_year)
        holidays_head.addWidget(self.add_holiday_button)
        holidays_layout.addLayout(holidays_head)

        self.holidays_table = QTableWidget()
        configure_table(self.holidays_table, ["Date", "Name"], stretch_column=1)
        self.holidays_table.itemSelectionChanged.connect(self._holiday_selection)
        holidays_layout.addWidget(self.holidays_table, 1)
        self.remove_holiday_button = QPushButton("Remove holiday")
        self.remove_holiday_button.setEnabled(False)
        self.remove_holiday_button.clicked.connect(self._remove_holiday)
        holidays_layout.addWidget(self.remove_holiday_button)
        layout.addWidget(holidays_card, 2)
        return tab

    def refresh(self) -> None:
        self.clear_error(self.error_label)
        self.set_status("Loading leave and attendance…")
        company_id = self.session.selected_company_id
        status_value = self.request_status.currentData()
        status = str(status_value) if status_value else None
        year_value = self.request_year.currentData()
        year = int(year_value) if isinstance(year_value, int) else None
        holiday_year = self.holiday_year.value()
        start = self.attendance_start.date().toPyDate()
        end = self.attendance_end.date().toPyDate()

        def load() -> LeaveData:
            employees = self.session.client.list_employees(company_id, page=1, page_size=100)
            return LeaveData(
                requests=self.session.client.list_leave_requests(
                    company_id,
                    page=1,
                    page_size=100,
                    status=status,
                    leave_year=year,
                ),
                types=self.session.client.list_leave_types(company_id),
                employees=employees.items,
                holidays=self.session.client.list_holidays(company_id, year=holiday_year),
                attendance=self.session.client.list_attendance(
                    company_id, start_date=start, end_date=end
                ),
            )

        self.runner.submit(
            load,
            on_success=self._render,
            on_failure=self._failed,
            on_finished=lambda: self.set_status("Leave workspace ready"),
        )

    def _render(self, data: LeaveData) -> None:
        self._employees = {str(employee.id): employee for employee in data.employees}
        self._types = {str(leave_type.id): leave_type for leave_type in data.types}
        self._requests = {str(request.id): request for request in data.requests.items}
        self._holidays = {str(holiday.id): holiday for holiday in data.holidays}
        self._attendance = {str(record.id): record for record in data.attendance.items}
        employee_names = {
            str(employee.id): f"{employee.first_name} {employee.last_name}"
            for employee in data.employees
        }

        self.requests_table.setRowCount(0)
        for row_index, request in enumerate(data.requests.items):
            self.requests_table.insertRow(row_index)
            employee_name = employee_names.get(str(request.employee_id), request.employee_name)
            item = text_item(employee_name)
            item.setData(Qt.ItemDataRole.UserRole, str(request.id))
            self.requests_table.setItem(row_index, 0, item)
            self.requests_table.setItem(row_index, 1, text_item(request.leave_type_name))
            self.requests_table.setItem(
                row_index,
                2,
                text_item(f"{date_label(request.start_date)} - {date_label(request.end_date)}"),
            )
            self.requests_table.setItem(
                row_index, 3, text_item(str(request.requested_days), numeric=True)
            )
            self.requests_table.setCellWidget(row_index, 4, status_widget(request.status))
            self.requests_table.setItem(row_index, 5, text_item(request.requested_by_name or "—"))
            self.requests_table.setRowHeight(row_index, 42)
        self.request_count.setText(f"{data.requests.total} leave request(s)")
        self._request_selection()
        self._render_types(data.types)
        self._render_holidays(data.holidays)
        self._render_attendance(data.attendance)
        self._render_employee_combo(data.employees)

    def _render_employee_combo(self, employees: list[Employee]) -> None:
        current = str(self.balance_employee.currentData() or "")
        self.balance_employee.clear()
        for employee in employees:
            self.balance_employee.addItem(
                f"{employee.employee_number} · {employee.first_name} {employee.last_name}",
                str(employee.id),
            )
        index = self.balance_employee.findData(current)
        if index >= 0:
            self.balance_employee.setCurrentIndex(index)

    def _render_types(self, types: list[LeaveType]) -> None:
        self.types_table.setRowCount(0)
        for row_index, leave_type in enumerate(types):
            self.types_table.insertRow(row_index)
            item = text_item(leave_type.code)
            item.setData(Qt.ItemDataRole.UserRole, str(leave_type.id))
            self.types_table.setItem(row_index, 0, item)
            self.types_table.setItem(row_index, 1, text_item(leave_type.name))
            self.types_table.setItem(
                row_index, 2, text_item(str(leave_type.annual_entitlement_days), numeric=True)
            )
            self.types_table.setItem(
                row_index, 3, text_item(str(leave_type.max_carryover_days), numeric=True)
            )
            self.types_table.setItem(row_index, 4, text_item("Yes" if leave_type.is_paid else "No"))
            self.types_table.setCellWidget(row_index, 5, status_widget(leave_type.status))
            self.types_table.setRowHeight(row_index, 38)
        self._type_selection()

    def _render_holidays(self, holidays: list[Holiday]) -> None:
        self.holidays_table.setRowCount(0)
        for row_index, holiday in enumerate(holidays):
            self.holidays_table.insertRow(row_index)
            item = text_item(date_label(holiday.holiday_date))
            item.setData(Qt.ItemDataRole.UserRole, str(holiday.id))
            self.holidays_table.setItem(row_index, 0, item)
            self.holidays_table.setItem(row_index, 1, text_item(holiday.name))
            self.holidays_table.setRowHeight(row_index, 38)
        self._holiday_selection()

    def _render_attendance(self, page: AttendancePage) -> None:
        self.attendance_table.setRowCount(0)
        for row_index, record in enumerate(page.items):
            self.attendance_table.insertRow(row_index)
            item = text_item(record.employee_name)
            item.setData(Qt.ItemDataRole.UserRole, str(record.id))
            self.attendance_table.setItem(row_index, 0, item)
            self.attendance_table.setItem(row_index, 1, text_item(date_label(record.work_date)))
            self.attendance_table.setCellWidget(row_index, 2, status_widget(record.status))
            self.attendance_table.setItem(
                row_index,
                3,
                text_item("Leave request" if record.source == "leave" else "Manual"),
            )
            self.attendance_table.setItem(row_index, 4, text_item(record.note or "—"))
            self.attendance_table.setRowHeight(row_index, 40)
        self.attendance_count.setText(f"{page.total} attendance record(s)")
        self._attendance_selection()

    def _failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The leave workspace could not be loaded. Try again.")
        self.set_status("Leave refresh failed")

    def _selected_request(self) -> LeaveRequest | None:
        row = self.requests_table.currentRow()
        if row < 0:
            return None
        item = self.requests_table.item(row, 0)
        if item is None:
            return None
        return self._requests.get(str(item.data(Qt.ItemDataRole.UserRole) or ""))

    def _request_selection(self) -> None:
        request = self._selected_request()
        can_decide = self.session.can("leave.approve")
        can_cancel = self.session.can("leave.manage") or self.session.can("leave.request")
        self.approve_button.setEnabled(
            bool(
                request
                and can_decide
                and request.status == "pending"
                and request.requested_by != self.session.me.user.id
            )
        )
        self.reject_button.setEnabled(
            bool(
                request
                and can_decide
                and request.status == "pending"
                and request.requested_by != self.session.me.user.id
            )
        )
        self.cancel_button.setEnabled(
            bool(request and can_cancel and request.status in {"pending", "approved"})
        )
        tooltip = ""
        if request and request.requested_by == self.session.me.user.id:
            tooltip = "The user who recorded a request cannot approve or reject it."
        self.approve_button.setToolTip(tooltip)
        self.reject_button.setToolTip(tooltip)

    def _request_action(self, action: str) -> None:
        request = self._selected_request()
        if request is None:
            return
        if action == "approve":
            answer = QMessageBox.question(
                self,
                "Approve leave",
                f"Approve {request.requested_days} day(s) for {request.employee_name}?\n\n"
                "The balance moves from pending to used and the attendance "
                "register is updated.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            note: str | None = None
        elif action == "reject":
            note, accepted = self._decision_note("Reject leave", "Reason for rejection")
            if not accepted:
                return
        else:
            note, accepted = self._decision_note("Cancel leave", "Reason for cancellation")
            if not accepted:
                return
        self._set_request_buttons(False)
        self.set_status(f"{action.capitalize()}ing leave request…")
        self.runner.submit(
            lambda: self.session.client.leave_request_action(str(request.id), action, note=note),
            on_success=lambda _result: self._request_mutated(action),
            on_failure=self._request_failed,
        )

    def _decision_note(self, title: str, label: str) -> tuple[str | None, bool]:
        from PyQt6.QtWidgets import QInputDialog

        text, accepted = QInputDialog.getText(self, title, label)
        if not accepted:
            return None, False
        return text.strip() or None, True

    def _set_request_buttons(self, enabled: bool) -> None:
        self.approve_button.setEnabled(enabled)
        self.reject_button.setEnabled(enabled)
        self.cancel_button.setEnabled(enabled)

    def _request_mutated(self, action: str) -> None:
        completed = {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}
        self.set_status(f"Leave request {completed.get(action, 'updated')}")
        self.refresh()

    def _request_failed(self, exception: Exception) -> None:
        self._request_selection()
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The leave request change could not be completed.")

    def _open_request(self) -> None:
        employees = [
            employee for employee in self._employees.values() if employee.status != "terminated"
        ]
        active_types = [
            leave_type for leave_type in self._types.values() if leave_type.status == "active"
        ]
        if not employees:
            QMessageBox.information(
                self, "Record leave", "Create an active employee before recording leave."
            )
            return
        if not active_types:
            QMessageBox.information(self, "Record leave", "Create a leave type in Setup first.")
            return
        dialog = LeaveRequestDialog(self.session, employees, active_types, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.record_button.setEnabled(False)
        self.set_status("Recording leave request…")
        self.runner.submit(
            lambda: self.session.client.create_leave_request(
                employee_id=payload.employee_id,
                leave_type_id=payload.leave_type_id,
                start_date=payload.start_date,
                end_date=payload.end_date,
                reason=payload.reason,
            ),
            on_success=self._request_created,
            on_failure=self._create_failed,
        )

    def _request_created(self, _request: LeaveRequest) -> None:
        self.record_button.setEnabled(self.session.can("leave.request"))
        self.set_status("Leave request recorded")
        self.refresh()

    def _create_failed(self, exception: Exception) -> None:
        self.record_button.setEnabled(self.session.can("leave.request"))
        self._request_failed(exception)

    def _load_balances(self, initialize: bool) -> None:
        employee_id = self.balance_employee.currentData()
        if employee_id is None:
            QMessageBox.information(self, "Leave balances", "Select an employee.")
            return
        year = self.balance_year.value()
        self.load_balances_button.setEnabled(False)
        self.initialize_button.setEnabled(False)
        self.set_status("Loading leave balances…")

        def operation() -> list[LeaveBalance]:
            if initialize:
                return self.session.client.initialize_leave_balances(
                    employee_id=str(employee_id), leave_year=year
                )
            return self.session.client.list_leave_balances(str(employee_id), leave_year=year)

        self.runner.submit(
            operation,
            on_success=self._balances_loaded,
            on_failure=self._balances_failed,
        )

    def _balances_loaded(self, balances: list[LeaveBalance]) -> None:
        self.load_balances_button.setEnabled(True)
        self.initialize_button.setEnabled(self.session.can("leave.manage"))
        self._balances = {str(balance.id): balance for balance in balances}
        self.balances_table.setRowCount(0)
        for row_index, balance in enumerate(balances):
            self.balances_table.insertRow(row_index)
            item = text_item(balance.leave_type_name)
            item.setData(Qt.ItemDataRole.UserRole, str(balance.id))
            self.balances_table.setItem(row_index, 0, item)
            self.balances_table.setItem(
                row_index, 1, text_item(str(balance.entitled_days), numeric=True)
            )
            self.balances_table.setItem(
                row_index, 2, text_item(str(balance.carried_over_days), numeric=True)
            )
            self.balances_table.setItem(
                row_index, 3, text_item(str(balance.used_days), numeric=True)
            )
            self.balances_table.setItem(
                row_index, 4, text_item(str(balance.pending_days), numeric=True)
            )
            self.balances_table.setItem(
                row_index, 5, text_item(str(balance.available_days), numeric=True)
            )
            self.balances_table.setRowHeight(row_index, 38)
        if not balances:
            self.balance_hint.setText(
                "No balances exist for this year. Use Initialise year to create them."
            )
        else:
            self.balance_hint.setText(
                f"{len(balances)} balance(s) for {self.balance_year.value()}."
            )
        self.set_status("Leave balances ready")
        self._balance_selection()

    def _balances_failed(self, exception: Exception) -> None:
        self.load_balances_button.setEnabled(True)
        self.initialize_button.setEnabled(self.session.can("leave.manage"))
        self._balance_selection()
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "Leave balances could not be loaded.")

    def _selected_balance(self) -> LeaveBalance | None:
        row = self.balances_table.currentRow()
        if row < 0:
            return None
        item = self.balances_table.item(row, 0)
        if item is None:
            return None
        return self._balances.get(str(item.data(Qt.ItemDataRole.UserRole) or ""))

    def _balance_selection(self) -> None:
        self.adjust_button.setEnabled(
            bool(self._selected_balance() and self.session.can("leave.manage"))
        )

    def _adjust_balance(self) -> None:
        balance = self._selected_balance()
        if balance is None:
            return
        dialog = BalanceAdjustDialog(balance, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.adjust_button.setEnabled(False)
        self.runner.submit(
            lambda: self.session.client.adjust_leave_balance(str(balance.id), payload),
            on_success=self._balance_adjusted,
            on_failure=self._balances_failed,
        )

    def _balance_adjusted(self, _balance: LeaveBalance) -> None:
        self.set_status("Leave balance adjusted")
        self._load_balances(False)

    def _load_attendance(self) -> None:
        start = self.attendance_start.date().toPyDate()
        end = self.attendance_end.date().toPyDate()
        if end < start:
            QMessageBox.warning(
                self, "Attendance register", "The end date must not precede the start."
            )
            return
        self.load_attendance_button.setEnabled(False)
        self.set_status("Loading attendance register…")
        self.runner.submit(
            lambda: self.session.client.list_attendance(
                self.session.selected_company_id,
                start_date=start,
                end_date=end,
            ),
            on_success=self._attendance_loaded,
            on_failure=self._attendance_failed,
        )

    def _attendance_loaded(self, page: AttendancePage) -> None:
        self.load_attendance_button.setEnabled(True)
        self._attendance = {str(record.id): record for record in page.items}
        self._render_attendance(page)
        self.set_status("Attendance register ready")

    def _attendance_failed(self, exception: Exception) -> None:
        self.load_attendance_button.setEnabled(True)
        self.record_attendance_button.setEnabled(self.session.can("attendance.manage"))
        self._attendance_selection()
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The attendance register could not be loaded.")

    def _selected_attendance(self) -> AttendanceRecord | None:
        row = self.attendance_table.currentRow()
        if row < 0:
            return None
        item = self.attendance_table.item(row, 0)
        if item is None:
            return None
        return self._attendance.get(str(item.data(Qt.ItemDataRole.UserRole) or ""))

    def _attendance_selection(self) -> None:
        record = self._selected_attendance()
        self.edit_attendance_button.setEnabled(
            bool(record and record.source == "manual" and self.session.can("attendance.manage"))
        )
        self.edit_attendance_button.setToolTip(
            "Leave attendance is managed through the leave request."
            if record is not None and record.source == "leave"
            else ""
        )

    def _open_attendance(self) -> None:
        employees = [
            employee for employee in self._employees.values() if employee.status != "terminated"
        ]
        if not employees:
            QMessageBox.information(
                self, "Record attendance", "Create an employee before recording attendance."
            )
            return
        dialog = AttendanceDialog(self.session, employees, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.create_payload()
        self.record_attendance_button.setEnabled(False)
        self.runner.submit(
            lambda: self.session.client.create_attendance(
                employee_id=payload.employee_id,
                work_date=payload.work_date,
                status=payload.status,
                note=payload.note,
            ),
            on_success=self._attendance_mutated,
            on_failure=self._attendance_failed,
        )

    def _edit_attendance(self) -> None:
        record = self._selected_attendance()
        if record is None:
            return
        employees = list(self._employees.values())
        dialog = AttendanceDialog(self.session, employees, record, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.edit_attendance_button.setEnabled(False)
        self.runner.submit(
            lambda: self.session.client.update_attendance(str(record.id), dialog.update_payload()),
            on_success=self._attendance_mutated,
            on_failure=self._attendance_failed,
        )

    def _attendance_mutated(self, _record: AttendanceRecord) -> None:
        self.record_attendance_button.setEnabled(self.session.can("attendance.manage"))
        self.set_status("Attendance recorded")
        self._load_attendance()

    def _add_type(self) -> None:
        dialog = LeaveTypeDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload(company_id=self.session.selected_company_id)
        self.add_type_button.setEnabled(False)
        self.runner.submit(
            lambda: self.session.client.create_leave_type(
                company_id=payload.company_id,
                code=payload.code,
                name=payload.name,
                description=payload.description,
                is_paid=payload.is_paid,
                requires_balance=payload.requires_balance,
                annual_entitlement_days=payload.annual_entitlement_days,
                max_carryover_days=payload.max_carryover_days,
            ),
            on_success=lambda _result: self._type_changed(),
            on_failure=self._setup_failed,
        )

    def _type_changed(self) -> None:
        self.add_type_button.setEnabled(self.session.can("leave.manage"))
        self.set_status("Leave types updated")
        self.refresh()

    def _setup_failed(self, exception: Exception) -> None:
        self.add_type_button.setEnabled(self.session.can("leave.manage"))
        self.add_holiday_button.setEnabled(self.session.can("leave.manage"))
        self._type_selection()
        self._holiday_selection()
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The leave setup change could not be completed.")

    def _selected_type(self) -> LeaveType | None:
        row = self.types_table.currentRow()
        if row < 0:
            return None
        item = self.types_table.item(row, 0)
        if item is None:
            return None
        return self._types.get(str(item.data(Qt.ItemDataRole.UserRole) or ""))

    def _type_selection(self) -> None:
        self.archive_type_button.setEnabled(
            bool(self._selected_type() and self.session.can("leave.manage"))
        )

    def _toggle_type(self) -> None:
        leave_type = self._selected_type()
        if leave_type is None:
            return
        target = "archived" if leave_type.status == "active" else "active"
        verb = "Archive" if target == "archived" else "Reactivate"
        answer = QMessageBox.question(
            self,
            f"{verb} leave type",
            f"{verb} {leave_type.name}? Archived types cannot be selected for new requests.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.archive_type_button.setEnabled(False)
        self.runner.submit(
            lambda: self.session.client.update_leave_type(
                str(leave_type.id), {"status": target, "version": leave_type.version}
            ),
            on_success=lambda _result: self._type_changed(),
            on_failure=self._setup_failed,
        )

    def _reload_holidays(self) -> None:
        year = self.holiday_year.value()
        self.runner.submit(
            lambda: self.session.client.list_holidays(self.session.selected_company_id, year=year),
            on_success=self._render_holidays,
            on_failure=self._setup_failed,
        )

    def _add_holiday(self) -> None:
        dialog = HolidayDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload(company_id=self.session.selected_company_id)
        self.add_holiday_button.setEnabled(False)
        self.runner.submit(
            lambda: self.session.client.create_holiday(
                company_id=payload.company_id,
                holiday_date=payload.holiday_date,
                name=payload.name,
            ),
            on_success=self._holiday_changed,
            on_failure=self._setup_failed,
        )

    def _selected_holiday(self) -> Holiday | None:
        row = self.holidays_table.currentRow()
        if row < 0:
            return None
        item = self.holidays_table.item(row, 0)
        if item is None:
            return None
        return self._holidays.get(str(item.data(Qt.ItemDataRole.UserRole) or ""))

    def _holiday_selection(self) -> None:
        self.remove_holiday_button.setEnabled(
            bool(self._selected_holiday() and self.session.can("leave.manage"))
        )

    def _remove_holiday(self) -> None:
        holiday = self._selected_holiday()
        if holiday is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove holiday",
            f"Remove {holiday.name} on {date_label(holiday.holiday_date)}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.remove_holiday_button.setEnabled(False)
        self.runner.submit(
            lambda: self.session.client.delete_holiday(str(holiday.id)),
            on_success=lambda _result: self._holiday_changed(),
            on_failure=self._setup_failed,
        )

    def _holiday_changed(self, _value: object = None) -> None:
        self.add_holiday_button.setEnabled(self.session.can("leave.manage"))
        self.set_status("Company holidays updated")
        self.refresh()
