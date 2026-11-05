"""Benefit plans, employee enrolments and payroll contribution configuration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
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
    BenefitEnrollment,
    BenefitEnrollmentPage,
    BenefitPlan,
    Employee,
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

CATEGORY_LABELS = {
    "medical": "Medical",
    "pension": "Pension",
    "insurance": "Insurance",
    "allowance": "Allowance",
    "other": "Other",
}

PAGE_SIZE = 25


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _amount_editor(value: str = "0.00", currency: str = "") -> QDoubleSpinBox:
    editor = QDoubleSpinBox()
    editor.setRange(0.0, 1_000_000_000.0)
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
class PlanInput:
    code: str
    name: str
    category: str
    employee_contribution: str
    employer_contribution: str
    description: str | None


@dataclass(frozen=True, slots=True)
class EnrollmentInput:
    employee_id: str
    plan_id: str
    effective_from: date
    effective_to: date | None
    employee_contribution: str
    employer_contribution: str
    notes: str | None


@dataclass(frozen=True, slots=True)
class EnrollmentEndInput:
    effective_to: date
    note: str | None


@dataclass(frozen=True, slots=True)
class BenefitData:
    plans: list[BenefitPlan]
    employees: list[Employee]
    enrollments: BenefitEnrollmentPage


class BenefitPlanDialog(QDialog):
    def __init__(
        self,
        plan: BenefitPlan | None = None,
        *,
        currency: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._plan = plan
        self._currency = currency
        self.setWindowTitle("New benefit plan" if plan is None else "Edit benefit plan")
        self.setModal(True)
        self.setMinimumWidth(540)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel("New benefit plan" if self._plan is None else "Edit benefit plan")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "Monthly contributions are snapshotted when an employee is enrolled. "
            "Changing the plan later only affects new enrolments."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.code = QLineEdit(self._plan.code if self._plan else "")
        self.code.setPlaceholderText("e.g. MED")
        self.code.setMaxLength(24)
        self.code.setEnabled(self._plan is None)
        self.name = QLineEdit(self._plan.name if self._plan else "")
        self.name.setPlaceholderText("Benefit plan name")
        self.category = QComboBox()
        for value, label in CATEGORY_LABELS.items():
            self.category.addItem(label, value)
        if self._plan is not None:
            index = self.category.findData(self._plan.category)
            if index >= 0:
                self.category.setCurrentIndex(index)
        self.employee_contribution = _amount_editor(
            str(self._plan.employee_contribution) if self._plan else "0.00",
            self._currency,
        )
        self.employer_contribution = _amount_editor(
            str(self._plan.employer_contribution) if self._plan else "0.00",
            self._currency,
        )
        self.description = QTextEdit(
            self._plan.description if self._plan and self._plan.description else ""
        )
        self.description.setPlaceholderText("Plan description and eligibility notes")
        self.description.setFixedHeight(74)
        form.addRow("Code", self.code)
        form.addRow("Name", self.name)
        form.addRow("Category", self.category)
        form.addRow("Employee monthly", self.employee_contribution)
        form.addRow("Employer monthly", self.employer_contribution)
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
            save.setText("Save plan")
            save.setProperty("role", "primary")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self._plan is None and len(self.code.text().strip()) < 2:
            self.error.setText("Enter a plan code of at least two characters.")
            self.error.show()
            return
        if len(self.name.text().strip()) < 2:
            self.error.setText("Enter a plan name.")
            self.error.show()
            return
        if self.employee_contribution.value() + self.employer_contribution.value() <= 0:
            self.error.setText("At least one contribution must be greater than zero.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> PlanInput:
        return PlanInput(
            code=self.code.text().strip().upper(),
            name=self.name.text().strip(),
            category=str(self.category.currentData()),
            employee_contribution=_amount(self.employee_contribution),
            employer_contribution=_amount(self.employer_contribution),
            description=self.description.toPlainText().strip() or None,
        )


class BenefitEnrollmentDialog(QDialog):
    def __init__(
        self,
        employees: list[Employee],
        plans: list[BenefitPlan],
        *,
        currency: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._employees = {str(employee.id): employee for employee in employees}
        self._plans = {str(plan.id): plan for plan in plans}
        self._currency = currency
        self.setWindowTitle("Enrol employee in a benefit plan")
        self.setModal(True)
        self.setMinimumWidth(540)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel("Enrol employee")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "Contributions are charged monthly and prorated for the days the enrolment "
            "overlaps each payroll period. Employer contributions are an additional cost."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.employee = QComboBox()
        for employee in self._employees.values():
            self.employee.addItem(
                f"{employee.employee_number} · {employee.full_name}",
                str(employee.id),
            )
        self.plan = QComboBox()
        for plan in self._plans.values():
            self.plan.addItem(f"{plan.code} · {plan.name}", str(plan.id))
        self.plan.currentIndexChanged.connect(self._apply_plan_amounts)
        today = QDate.currentDate()
        self.effective_from = QDateEdit(today)
        self.effective_to_enabled = QCheckBox("Set an end date")
        self.effective_to = QDateEdit(today)
        self.effective_to.setEnabled(False)
        self.effective_to_enabled.toggled.connect(self.effective_to.setEnabled)
        for editor in (self.effective_from, self.effective_to):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd MMM yyyy")
        self.employee_contribution = _amount_editor(currency=self._currency)
        self.employer_contribution = _amount_editor(currency=self._currency)
        self.notes = QTextEdit()
        self.notes.setPlaceholderText("Optional enrolment notes")
        self.notes.setFixedHeight(64)
        form.addRow("Employee", self.employee)
        form.addRow("Plan", self.plan)
        form.addRow("Effective from", self.effective_from)
        form.addRow("", self.effective_to_enabled)
        form.addRow("Effective to", self.effective_to)
        form.addRow("Employee monthly", self.employee_contribution)
        form.addRow("Employer monthly", self.employer_contribution)
        form.addRow("Notes", self.notes)
        root.addLayout(form)
        self._apply_plan_amounts()

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
            save.setText("Enrol employee")
            save.setProperty("role", "primary")
            save.setEnabled(bool(self._employees) and bool(self._plans))
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _apply_plan_amounts(self) -> None:
        plan = self._plans.get(str(self.plan.currentData()))
        if plan is None:
            return
        self.employee_contribution.setValue(float(plan.employee_contribution))
        self.employer_contribution.setValue(float(plan.employer_contribution))

    def _validate(self) -> None:
        if self.employee.currentData() is None or self.plan.currentData() is None:
            self.error.setText("Select an employee and an active benefit plan.")
            self.error.show()
            return
        employee = self._employees.get(str(self.employee.currentData()))
        if employee is not None and self.effective_from.date() < _qdate(employee.hire_date):
            self.error.setText("The effective date cannot precede the employee's hire date.")
            self.error.show()
            return
        if self.effective_to_enabled.isChecked() and (
            self.effective_to.date() < self.effective_from.date()
        ):
            self.error.setText("The end date must not precede the effective date.")
            self.error.show()
            return
        if self.employee_contribution.value() + self.employer_contribution.value() <= 0:
            self.error.setText("At least one contribution must be greater than zero.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> EnrollmentInput:
        return EnrollmentInput(
            employee_id=str(self.employee.currentData()),
            plan_id=str(self.plan.currentData()),
            effective_from=self.effective_from.date().toPyDate(),
            effective_to=(
                self.effective_to.date().toPyDate()
                if self.effective_to_enabled.isChecked()
                else None
            ),
            employee_contribution=_amount(self.employee_contribution),
            employer_contribution=_amount(self.employer_contribution),
            notes=self.notes.toPlainText().strip() or None,
        )


class EnrollmentEndDialog(QDialog):
    def __init__(self, enrollment: BenefitEnrollment, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._enrollment = enrollment
        self.setWindowTitle("End benefit enrolment")
        self.setModal(True)
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel(f"End {enrollment.plan_name} enrolment")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            f"{enrollment.employee_name} will stop contributing from the day after the "
            "end date. Payroll periods that overlap the enrolment are prorated."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        default_end = max(date.today(), enrollment.effective_from)
        self.effective_to = QDateEdit(_qdate(default_end))
        self.effective_to.setCalendarPopup(True)
        self.effective_to.setDisplayFormat("dd MMM yyyy")
        self.note = QTextEdit()
        self.note.setPlaceholderText("Reason for ending the enrolment")
        self.note.setFixedHeight(64)
        form.addRow("Effective to", self.effective_to)
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
            save.setText("End enrolment")
            save.setProperty("role", "primary")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self.effective_to.date() < _qdate(self._enrollment.effective_from):
            self.error.setText("The end date must not precede the enrolment start date.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> EnrollmentEndInput:
        return EnrollmentEndInput(
            effective_to=self.effective_to.date().toPyDate(),
            note=self.note.toPlainText().strip() or None,
        )


class BenefitsPage(BasePage):
    def __init__(self, session: ApplicationSession) -> None:
        super().__init__(session)
        self._plans: dict[str, BenefitPlan] = {}
        self._employees: dict[str, Employee] = {}
        self._enrollments: dict[str, BenefitEnrollment] = {}
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
                "Benefits",
                "Voluntary benefit plans, employee contributions and employer costs.",
            ),
            1,
        )
        self.new_plan_button = QPushButton("New plan")
        self.new_plan_button.setProperty("role", "primary")
        self.new_plan_button.setEnabled(self.session.can("benefit.manage"))
        self.new_plan_button.clicked.connect(self._new_plan)
        header.addWidget(self.new_plan_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header)

        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_plans_tab(), "Plans")
        self.tabs.addTab(self._build_enrollments_tab(), "Enrolments")
        root.addWidget(self.tabs, 1)
        self._update_actions()

    def _build_plans_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        toolbar.addWidget(QLabel("Status"))
        self.plan_status_filter = QComboBox()
        self.plan_status_filter.addItem("All plans", "")
        self.plan_status_filter.addItem("Active", "active")
        self.plan_status_filter.addItem("Archived", "archived")
        self.plan_status_filter.currentIndexChanged.connect(self._render_plans)
        toolbar.addWidget(self.plan_status_filter)
        toolbar.addStretch(1)
        self.plan_count = QLabel("")
        self.plan_count.setProperty("role", "muted")
        toolbar.addWidget(self.plan_count)
        self.refresh_plans_button = QPushButton("Refresh")
        self.refresh_plans_button.setProperty("role", "ghost")
        self.refresh_plans_button.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_plans_button)
        layout.addLayout(toolbar)

        card = QFrame()
        card.setProperty("card", "true")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)
        self.plans_table = QTableWidget()
        configure_table(
            self.plans_table,
            ["Code", "Name", "Category", "Employee", "Employer", "Currency", "Status"],
            stretch_column=1,
        )
        self.plans_table.itemSelectionChanged.connect(self._update_actions)
        card_layout.addWidget(self.plans_table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.edit_plan_button = QPushButton("Edit plan")
        self.edit_plan_button.clicked.connect(self._edit_plan)
        self.toggle_plan_button = QPushButton("Archive")
        self.toggle_plan_button.setProperty("role", "ghost")
        self.toggle_plan_button.clicked.connect(self._toggle_plan)
        actions.addStretch(1)
        actions.addWidget(self.edit_plan_button)
        actions.addWidget(self.toggle_plan_button)
        card_layout.addLayout(actions)
        layout.addWidget(card, 1)
        return tab

    def _build_enrollments_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        toolbar.addWidget(QLabel("Status"))
        self.enrollment_status_filter = QComboBox()
        self.enrollment_status_filter.addItem("All enrolments", "")
        self.enrollment_status_filter.addItem("Active", "active")
        self.enrollment_status_filter.addItem("Ended", "ended")
        self.enrollment_status_filter.currentIndexChanged.connect(self._filter_enrollments)
        toolbar.addWidget(self.enrollment_status_filter)
        toolbar.addStretch(1)
        self.enrollment_count = QLabel("")
        self.enrollment_count.setProperty("role", "muted")
        toolbar.addWidget(self.enrollment_count)
        self.enrol_button = QPushButton("Enrol employee")
        self.enrol_button.setProperty("role", "primary")
        self.enrol_button.clicked.connect(self._enrol)
        toolbar.addWidget(self.enrol_button)
        layout.addLayout(toolbar)

        card = QFrame()
        card.setProperty("card", "true")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)
        self.enrollments_table = QTableWidget()
        configure_table(
            self.enrollments_table,
            [
                "Employee",
                "Plan",
                "Effective from",
                "Effective to",
                "Employee",
                "Employer",
                "Status",
            ],
            stretch_column=0,
        )
        self.enrollments_table.itemSelectionChanged.connect(self._update_actions)
        card_layout.addWidget(self.enrollments_table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.previous_page_button = QPushButton("Previous")
        self.previous_page_button.setProperty("role", "ghost")
        self.previous_page_button.clicked.connect(self._previous_page)
        self.next_page_button = QPushButton("Next")
        self.next_page_button.setProperty("role", "ghost")
        self.next_page_button.clicked.connect(self._next_page)
        self.end_enrollment_button = QPushButton("End enrolment")
        self.end_enrollment_button.clicked.connect(self._end_enrollment)
        actions.addWidget(self.previous_page_button)
        actions.addWidget(self.next_page_button)
        actions.addStretch(1)
        actions.addWidget(self.end_enrollment_button)
        card_layout.addLayout(actions)
        layout.addWidget(card, 1)
        return tab

    def refresh(self) -> None:
        self.clear_error(self.error_label)
        self.set_status("Loading benefits…")
        company_id = self.session.selected_company_id
        enrollment_status_value = self.enrollment_status_filter.currentData()
        enrollment_status = str(enrollment_status_value) if enrollment_status_value else None
        page = self._page

        def load() -> BenefitData:
            plans = self.session.client.list_benefit_plans(company_id)
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
            enrollments = self.session.client.list_benefit_enrollments(
                company_id,
                page=page,
                page_size=PAGE_SIZE,
                status=enrollment_status,
            )
            return BenefitData(plans=plans, employees=employees, enrollments=enrollments)

        self.runner.submit(
            load,
            on_success=self._render,
            on_failure=self._failed,
            on_finished=lambda: self.set_status("Benefits workspace ready"),
        )

    def _render(self, data: BenefitData) -> None:
        self._plans = {str(plan.id): plan for plan in data.plans}
        self._employees = {str(employee.id): employee for employee in data.employees}
        self._enrollments = {
            str(enrollment.id): enrollment for enrollment in data.enrollments.items
        }
        self._total = data.enrollments.total
        self._page = data.enrollments.page
        self._render_plans()
        self._render_enrollments()
        self._update_actions()

    def _render_plans(self) -> None:
        selected = self._selected_plan_id()
        status_value = self.plan_status_filter.currentData()
        status = str(status_value) if status_value else ""
        plans = [
            plan
            for plan in sorted(self._plans.values(), key=lambda item: item.name)
            if not status or plan.status == status
        ]
        self.plans_table.blockSignals(True)
        self.plans_table.setRowCount(0)
        for row_index, plan in enumerate(plans):
            self.plans_table.insertRow(row_index)
            item = text_item(plan.code)
            item.setData(Qt.ItemDataRole.UserRole, str(plan.id))
            self.plans_table.setItem(row_index, 0, item)
            self.plans_table.setItem(row_index, 1, text_item(plan.name))
            self.plans_table.setItem(
                row_index, 2, text_item(CATEGORY_LABELS.get(plan.category, plan.category))
            )
            self.plans_table.setItem(
                row_index, 3, text_item(str(plan.employee_contribution), numeric=True)
            )
            self.plans_table.setItem(
                row_index, 4, text_item(str(plan.employer_contribution), numeric=True)
            )
            self.plans_table.setItem(row_index, 5, text_item(plan.currency))
            self.plans_table.setCellWidget(row_index, 6, status_widget(plan.status))
            self.plans_table.setRowHeight(row_index, 40)
        self.plans_table.blockSignals(False)
        self.plan_count.setText(f"{len(plans)} plan(s)")
        if plans:
            target = 0
            if selected is not None:
                for row_index in range(self.plans_table.rowCount()):
                    item = self.plans_table.item(row_index, 0)
                    if item is not None and str(item.data(Qt.ItemDataRole.UserRole)) == selected:
                        target = row_index
                        break
            self.plans_table.selectRow(target)

    def _render_enrollments(self) -> None:
        selected = self._selected_enrollment_id()
        enrollments = sorted(
            self._enrollments.values(),
            key=lambda item: (item.employee_name, item.plan_name),
        )
        self.enrollments_table.blockSignals(True)
        self.enrollments_table.setRowCount(0)
        for row_index, enrollment in enumerate(enrollments):
            self.enrollments_table.insertRow(row_index)
            item = text_item(enrollment.employee_name)
            item.setData(Qt.ItemDataRole.UserRole, str(enrollment.id))
            self.enrollments_table.setItem(row_index, 0, item)
            self.enrollments_table.setItem(
                row_index, 1, text_item(f"{enrollment.plan_code} · {enrollment.plan_name}")
            )
            self.enrollments_table.setItem(
                row_index, 2, text_item(date_label(enrollment.effective_from))
            )
            self.enrollments_table.setItem(
                row_index, 3, text_item(date_label(enrollment.effective_to))
            )
            self.enrollments_table.setItem(
                row_index,
                4,
                text_item(str(enrollment.employee_contribution), numeric=True),
            )
            self.enrollments_table.setItem(
                row_index,
                5,
                text_item(str(enrollment.employer_contribution), numeric=True),
            )
            self.enrollments_table.setCellWidget(row_index, 6, status_widget(enrollment.status))
            self.enrollments_table.setRowHeight(row_index, 40)
        self.enrollments_table.blockSignals(False)
        start = (self._page - 1) * PAGE_SIZE + 1 if self._total else 0
        end = min(self._page * PAGE_SIZE, self._total)
        self.enrollment_count.setText(
            f"{start} to {end} of {self._total} enrolment(s)" if self._total else "No enrolments"
        )
        self.previous_page_button.setEnabled(self._page > 1)
        self.next_page_button.setEnabled(self._page * PAGE_SIZE < self._total)
        if enrollments:
            target = 0
            if selected is not None:
                for row_index in range(self.enrollments_table.rowCount()):
                    item = self.enrollments_table.item(row_index, 0)
                    if item is not None and str(item.data(Qt.ItemDataRole.UserRole)) == selected:
                        target = row_index
                        break
            self.enrollments_table.selectRow(target)

    def _selected_plan_id(self) -> str | None:
        row = self.plans_table.currentRow()
        if row < 0:
            return None
        item = self.plans_table.item(row, 0)
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def _selected_enrollment_id(self) -> str | None:
        row = self.enrollments_table.currentRow()
        if row < 0:
            return None
        item = self.enrollments_table.item(row, 0)
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def _selected_plan(self) -> BenefitPlan | None:
        plan_id = self._selected_plan_id()
        return self._plans.get(plan_id) if plan_id else None

    def _selected_enrollment(self) -> BenefitEnrollment | None:
        enrollment_id = self._selected_enrollment_id()
        return self._enrollments.get(enrollment_id) if enrollment_id else None

    def _update_actions(self) -> None:
        can_manage = self.session.can("benefit.manage")
        plan = self._selected_plan()
        enrollment = self._selected_enrollment()
        self.new_plan_button.setEnabled(can_manage)
        self.edit_plan_button.setEnabled(can_manage and plan is not None)
        self.toggle_plan_button.setEnabled(can_manage and plan is not None)
        if plan is not None:
            self.toggle_plan_button.setText("Restore" if plan.status == "archived" else "Archive")
        else:
            self.toggle_plan_button.setText("Archive")
        self.enrol_button.setEnabled(can_manage)
        self.end_enrollment_button.setEnabled(
            can_manage and enrollment is not None and enrollment.status == "active"
        )

    def _failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The benefits workspace could not be loaded.")
        self.set_status("Benefit refresh failed")

    def _action_failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The benefit action could not be completed.")
        self._update_actions()

    def _new_plan(self) -> None:
        dialog = BenefitPlanDialog(currency=self.session.company.base_currency, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.new_plan_button.setEnabled(False)
        self.set_status("Creating benefit plan…")
        self.runner.submit(
            lambda: self.session.client.create_benefit_plan(
                company_id=self.session.selected_company_id,
                code=payload.code,
                name=payload.name,
                category=payload.category,
                employee_contribution=payload.employee_contribution,
                employer_contribution=payload.employer_contribution,
                description=payload.description,
            ),
            on_success=lambda _plan: self._action_done("Benefit plan created"),
            on_failure=self._action_failed,
        )

    def _edit_plan(self) -> None:
        plan = self._selected_plan()
        if plan is None:
            return
        dialog = BenefitPlanDialog(plan, currency=self.session.company.base_currency, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self._run_plan_update(plan, payload, "Benefit plan updated")

    def _toggle_plan(self) -> None:
        plan = self._selected_plan()
        if plan is None:
            return
        new_status = "archived" if plan.status == "active" else "active"
        if new_status == "archived":
            answer = QMessageBox.question(
                self,
                "Archive benefit plan",
                f"Archive {plan.name}?\n\nActive enrolments must be ended first.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        payload = PlanInput(
            code=plan.code,
            name=plan.name,
            category=plan.category,
            employee_contribution=str(plan.employee_contribution),
            employer_contribution=str(plan.employer_contribution),
            description=plan.description,
        )
        self._run_plan_update(
            plan,
            payload,
            "Benefit plan archived" if new_status == "archived" else "Benefit plan restored",
            status=new_status,
        )

    def _run_plan_update(
        self,
        plan: BenefitPlan,
        payload: PlanInput,
        completed: str,
        *,
        status: str | None = None,
    ) -> None:
        self.set_status("Updating benefit plan…")
        self.runner.submit(
            lambda: self.session.client.update_benefit_plan(
                str(plan.id),
                version=plan.version,
                name=payload.name,
                category=payload.category,
                employee_contribution=payload.employee_contribution,
                employer_contribution=payload.employer_contribution,
                description=payload.description,
                status=status,
            ),
            on_success=lambda _plan: self._action_done(completed),
            on_failure=self._action_failed,
        )

    def _enrol(self) -> None:
        employees = [
            employee
            for employee in self._employees.values()
            if employee.status in {"active", "probation"} and employee.is_payroll_eligible
        ]
        plans = [plan for plan in self._plans.values() if plan.status == "active"]
        if not employees:
            QMessageBox.information(
                self, "Benefit enrolment", "Create an active employee before enrolling."
            )
            return
        if not plans:
            QMessageBox.information(
                self, "Benefit enrolment", "Create an active benefit plan before enrolling."
            )
            return
        dialog = BenefitEnrollmentDialog(
            employees,
            plans,
            currency=self.session.company.base_currency,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.enrol_button.setEnabled(False)
        self.set_status("Enrolling employee…")
        self.runner.submit(
            lambda: self.session.client.create_benefit_enrollment(
                company_id=self.session.selected_company_id,
                employee_id=payload.employee_id,
                plan_id=payload.plan_id,
                effective_from=payload.effective_from,
                effective_to=payload.effective_to,
                employee_contribution=payload.employee_contribution,
                employer_contribution=payload.employer_contribution,
                notes=payload.notes,
            ),
            on_success=lambda _enrollment: self._action_done("Employee enrolled"),
            on_failure=self._action_failed,
        )

    def _end_enrollment(self) -> None:
        enrollment = self._selected_enrollment()
        if enrollment is None:
            return
        dialog = EnrollmentEndDialog(enrollment, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.end_enrollment_button.setEnabled(False)
        self.set_status("Ending enrolment…")
        self.runner.submit(
            lambda: self.session.client.end_benefit_enrollment(
                str(enrollment.id),
                version=enrollment.version,
                effective_to=payload.effective_to,
                note=payload.note,
            ),
            on_success=lambda _enrollment: self._action_done("Benefit enrolment ended"),
            on_failure=self._action_failed,
        )

    def _action_done(self, message: str) -> None:
        self.set_status(message)
        self.refresh()

    def _filter_enrollments(self) -> None:
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
