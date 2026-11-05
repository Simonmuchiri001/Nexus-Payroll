"""Employee loans and advances: application, approval, disbursement and schedules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nexus_desktop.api.client import ApiError, NetworkError
from nexus_desktop.api.models import Employee, Loan, LoanDetail
from nexus_desktop.state import ApplicationSession
from nexus_desktop.ui.common import (
    PageHeader,
    StatusBadge,
    configure_table,
    date_label,
    money,
    status_widget,
    text_item,
)
from nexus_desktop.ui.pages.base import BasePage


def _parse_amount(editor: QLineEdit, label: str, *, maximum: str) -> Decimal:
    try:
        value = Decimal(editor.text().strip().replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"Enter {label.lower()} as a number.") from exc
    if value <= 0 or value > Decimal(maximum):
        raise ValueError(f"{label} must be greater than zero and at most {maximum}.")
    if value.as_tuple().exponent < -2:  # type: ignore[operator]
        raise ValueError(f"{label} supports at most two decimal places.")
    return value


def _parse_rate(editor: QLineEdit) -> Decimal:
    try:
        value = Decimal(editor.text().strip())
    except InvalidOperation as exc:
        raise ValueError("Enter the annual interest rate as a number.") from exc
    if value < 0 or value > 100:
        raise ValueError("The annual interest rate must be between 0 and 100.")
    if value.as_tuple().exponent < -4:  # type: ignore[operator]
        raise ValueError("The annual interest rate supports at most four decimal places.")
    return value


@dataclass(frozen=True, slots=True)
class LoanApplicationInput:
    employee_id: str
    kind: str
    principal_amount: str
    interest_rate_annual: str
    term_months: int
    purpose: str


@dataclass(frozen=True, slots=True)
class LoanDisbursementInput:
    disbursed_on: date
    first_due_date: date
    reference: str


@dataclass(frozen=True, slots=True)
class LoanData:
    employees: list[Employee]
    loans: list[Loan]


class LoanApplicationDialog(QDialog):
    def __init__(
        self,
        employees: list[Employee],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._employees = employees
        self.setWindowTitle("Apply for a loan or advance")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel("Apply for a loan or advance")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "The application is recorded as pending. A different user must approve it, "
            "and an authorised finance user must disburse it before repayments begin."
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
        self.kind = QComboBox()
        self.kind.addItem("Loan", "loan")
        self.kind.addItem("Advance", "advance")
        self.principal = QLineEdit("60000.00")
        self.principal.setPlaceholderText("Principal amount")
        self.interest = QLineEdit("12.0000")
        self.interest.setPlaceholderText("Annual interest rate (%)")
        self.term = QSpinBox()
        self.term.setRange(1, 120)
        self.term.setValue(12)
        self.term.setSuffix(" months")
        self.purpose = QTextEdit()
        self.purpose.setPlaceholderText("Purpose of the loan or advance")
        self.purpose.setFixedHeight(74)
        form.addRow("Employee", self.employee)
        form.addRow("Type", self.kind)
        form.addRow("Principal", self.principal)
        form.addRow("Annual interest (%)", self.interest)
        form.addRow("Term", self.term)
        form.addRow("Purpose", self.purpose)
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
            save.setText("Record application")
            save.setProperty("role", "primary")
            save.setEnabled(bool(self._employees))
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self.employee.currentData() is None:
            self.error.setText("Select an employee.")
            self.error.show()
            return
        try:
            _parse_amount(self.principal, "Principal", maximum="100000000.00")
            _parse_rate(self.interest)
        except ValueError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        if len(self.purpose.toPlainText().strip()) < 3:
            self.error.setText("Enter a purpose for the application.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> LoanApplicationInput:
        return LoanApplicationInput(
            employee_id=str(self.employee.currentData()),
            kind=str(self.kind.currentData()),
            principal_amount=str(
                _parse_amount(self.principal, "Principal", maximum="100000000.00")
            ),
            interest_rate_annual=str(_parse_rate(self.interest)),
            term_months=self.term.value(),
            purpose=self.purpose.toPlainText().strip(),
        )


class LoanDisbursementDialog(QDialog):
    def __init__(self, loan: Loan, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Disburse loan")
        self.setModal(True)
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel(f"Disburse {loan.loan_number}")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            f"Disbursing {money(loan.principal_amount, loan.currency)} records a balanced "
            "journal and creates the reducing-balance repayment schedule. This action "
            "cannot be repeated."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        today = QDate.currentDate()
        self.disbursed_on = QDateEdit(today)
        self.first_due_date = QDateEdit(today.addMonths(1))
        for editor in (self.disbursed_on, self.first_due_date):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd MMM yyyy")
        self.reference = QLineEdit(f"DISB-{loan.loan_number}")
        self.reference.setPlaceholderText("Bank or cash reference")
        form.addRow("Disbursed on", self.disbursed_on)
        form.addRow("First deduction date", self.first_due_date)
        form.addRow("Reference", self.reference)
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
            save.setText("Disburse loan")
            save.setProperty("role", "primary")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self.first_due_date.date() < self.disbursed_on.date():
            self.error.setText("The first deduction date must not precede the disbursement date.")
            self.error.show()
            return
        if len(self.reference.text().strip()) < 2:
            self.error.setText("Enter the disbursement reference.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> LoanDisbursementInput:
        return LoanDisbursementInput(
            disbursed_on=self.disbursed_on.date().toPyDate(),
            first_due_date=self.first_due_date.date().toPyDate(),
            reference=self.reference.text().strip(),
        )


class LoansPage(BasePage):
    def __init__(self, session: ApplicationSession) -> None:
        super().__init__(session)
        self._employees: dict[str, Employee] = {}
        self._loans: dict[str, Loan] = {}
        self._selected_id: str | None = None
        self._detail: LoanDetail | None = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 26, 32, 30)
        root.setSpacing(16)

        header = QHBoxLayout()
        header.addWidget(
            PageHeader(
                "Loans & advances",
                "Staff credit applications, approval, disbursement and repayment schedules.",
            ),
            1,
        )
        self.apply_button = QPushButton("Apply")
        self.apply_button.setProperty("role", "primary")
        self.apply_button.setEnabled(self.session.can("loan.request"))
        self.apply_button.clicked.connect(self._apply)
        header.addWidget(self.apply_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header)

        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        self.status_filter = QComboBox()
        self.status_filter.addItem("All statuses", "")
        for label, value in (
            ("Pending", "pending"),
            ("Approved", "approved"),
            ("Disbursed", "disbursed"),
            ("Closed", "closed"),
            ("Rejected", "rejected"),
            ("Cancelled", "cancelled"),
        ):
            self.status_filter.addItem(label, value)
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("All types", "")
        self.kind_filter.addItem("Loans", "loan")
        self.kind_filter.addItem("Advances", "advance")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setProperty("role", "ghost")
        self.refresh_button.clicked.connect(self.refresh)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.kind_filter.currentIndexChanged.connect(self.refresh)
        filters.addWidget(QLabel("Status"))
        filters.addWidget(self.status_filter)
        filters.addWidget(QLabel("Type"))
        filters.addWidget(self.kind_filter)
        filters.addStretch(1)
        filters.addWidget(self.refresh_button)
        root.addLayout(filters)

        content = QHBoxLayout()
        content.setSpacing(16)

        list_card = QFrame()
        list_card.setProperty("card", "true")
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(16, 14, 16, 14)
        list_layout.setSpacing(10)
        self.loan_count = QLabel("")
        self.loan_count.setProperty("role", "muted")
        list_layout.addWidget(self.loan_count)
        self.loans_table = QTableWidget()
        configure_table(
            self.loans_table,
            ["Employee", "Number", "Type", "Principal", "Outstanding", "Status"],
            stretch_column=0,
        )
        self.loans_table.setMinimumHeight(280)
        self.loans_table.itemSelectionChanged.connect(self._selection)
        list_layout.addWidget(self.loans_table, 1)
        content.addWidget(list_card, 3)

        detail_card = QFrame()
        detail_card.setProperty("card", "true")
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(16, 14, 16, 14)
        detail_layout.setSpacing(10)
        detail_head = QHBoxLayout()
        self.detail_title = QLabel("Select a loan")
        self.detail_title.setProperty("role", "section-title")
        self.detail_status = StatusBadge("pending")
        self.detail_status.hide()
        detail_head.addWidget(self.detail_title, 1)
        detail_head.addWidget(self.detail_status)
        detail_layout.addLayout(detail_head)

        self.detail_meta = QLabel("Choose a loan to see its repayment schedule.")
        self.detail_meta.setProperty("role", "muted")
        self.detail_meta.setWordWrap(True)
        detail_layout.addWidget(self.detail_meta)

        metrics = QHBoxLayout()
        metrics.setSpacing(18)
        self.principal_metric = self._metric(metrics, "Principal")
        self.repayable_metric = self._metric(metrics, "Total repayable")
        self.outstanding_metric = self._metric(metrics, "Outstanding")
        self.progress_metric = self._metric(metrics, "Repaid")
        detail_layout.addLayout(metrics)

        self.schedule_table = QTableWidget()
        configure_table(
            self.schedule_table,
            ["#", "Due", "Principal", "Interest", "Amount", "Status"],
            stretch_column=1,
        )
        self.schedule_table.setMinimumHeight(220)
        detail_layout.addWidget(self.schedule_table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.approve_button = QPushButton("Approve")
        self.approve_button.clicked.connect(self._approve)
        self.reject_button = QPushButton("Reject")
        self.reject_button.clicked.connect(self._reject)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setProperty("role", "ghost")
        self.cancel_button.clicked.connect(self._cancel)
        self.disburse_button = QPushButton("Disburse")
        self.disburse_button.setProperty("role", "primary")
        self.disburse_button.clicked.connect(self._disburse)
        actions.addStretch(1)
        actions.addWidget(self.approve_button)
        actions.addWidget(self.reject_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.disburse_button)
        detail_layout.addLayout(actions)
        content.addWidget(detail_card, 2)

        root.addLayout(content, 1)
        self._set_actions(None)

    @staticmethod
    def _metric(layout: QHBoxLayout, label: str) -> QLabel:
        block = QVBoxLayout()
        block.setSpacing(2)
        caption = QLabel(label)
        caption.setProperty("role", "metric-label")
        value = QLabel("—")
        value.setProperty("role", "metric-small")
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        block.addWidget(caption)
        block.addWidget(value)
        layout.addLayout(block)
        return value

    def refresh(self) -> None:
        self.clear_error(self.error_label)
        self.set_status("Loading loans…")
        company_id = self.session.selected_company_id
        status_value = self.status_filter.currentData()
        status = str(status_value) if status_value else None
        kind_value = self.kind_filter.currentData()
        kind = str(kind_value) if kind_value else None

        def load() -> LoanData:
            employees = self.session.client.list_employees(company_id, page=1, page_size=100)
            loans = self.session.client.list_loans(
                company_id,
                page=1,
                page_size=100,
                status=status,
                kind=kind,
            )
            return LoanData(employees=employees.items, loans=loans.items)

        self.runner.submit(
            load,
            on_success=self._render,
            on_failure=self._failed,
            on_finished=lambda: self.set_status("Loans workspace ready"),
        )

    def _render(self, data: LoanData) -> None:
        self._employees = {str(employee.id): employee for employee in data.employees}
        self._loans = {str(loan.id): loan for loan in data.loans}
        previous = self._selected_id
        self.loans_table.blockSignals(True)
        self.loans_table.setRowCount(0)
        for row_index, loan in enumerate(data.loans):
            self.loans_table.insertRow(row_index)
            item = text_item(loan.employee_name)
            item.setData(Qt.ItemDataRole.UserRole, str(loan.id))
            self.loans_table.setItem(row_index, 0, item)
            self.loans_table.setItem(row_index, 1, text_item(loan.loan_number))
            self.loans_table.setItem(
                row_index, 2, text_item("Advance" if loan.kind == "advance" else "Loan")
            )
            self.loans_table.setItem(
                row_index,
                3,
                text_item(str(loan.principal_amount), numeric=True),
            )
            self.loans_table.setItem(
                row_index,
                4,
                text_item(str(loan.outstanding_principal), numeric=True),
            )
            self.loans_table.setCellWidget(row_index, 5, status_widget(loan.status))
            self.loans_table.setRowHeight(row_index, 40)
        self.loans_table.blockSignals(False)
        self.loan_count.setText(f"{len(data.loans)} loan application(s)")

        target_row = 0
        if previous is not None:
            for row_index in range(self.loans_table.rowCount()):
                item = self.loans_table.item(row_index, 0)
                if item is not None and str(item.data(Qt.ItemDataRole.UserRole)) == previous:
                    target_row = row_index
                    break
        if self.loans_table.rowCount():
            self.loans_table.selectRow(target_row)
        else:
            self._show_empty_detail()

    def _selection(self) -> None:
        loan = self._selected_loan()
        if loan is None:
            self._show_empty_detail()
            return
        self._selected_id = str(loan.id)
        self._set_actions(loan)
        self.detail_title.setText(loan.loan_number)
        self.detail_status.set_status(loan.status)
        self.detail_status.show()
        self._set_metrics(loan)
        self.detail_meta.setText(
            f"{loan.purpose} · Requested by {loan.requested_by_name or '—'}"
            + (
                f" · Disbursed {date_label(loan.disbursed_on)}"
                if loan.disbursed_on is not None
                else ""
            )
        )
        selected_id = str(loan.id)

        self.runner.submit(
            lambda: self.session.client.loan_detail(selected_id),
            on_success=lambda detail: self._render_detail(selected_id, detail),
            on_failure=self._detail_failed,
        )

    def _render_detail(self, loan_id: str, detail: LoanDetail) -> None:
        if loan_id != self._selected_id:
            return
        self._detail = detail
        loan = detail.loan
        self._loans[loan_id] = loan
        self.detail_status.set_status(loan.status)
        self._set_metrics(loan)
        self._set_actions(loan)
        self.schedule_table.setRowCount(0)
        for row_index, installment in enumerate(detail.installments):
            self.schedule_table.insertRow(row_index)
            self.schedule_table.setItem(row_index, 0, text_item(str(installment.sequence)))
            self.schedule_table.setItem(row_index, 1, text_item(date_label(installment.due_date)))
            self.schedule_table.setItem(
                row_index,
                2,
                text_item(str(installment.principal_due), numeric=True),
            )
            self.schedule_table.setItem(
                row_index,
                3,
                text_item(str(installment.interest_due), numeric=True),
            )
            self.schedule_table.setItem(
                row_index,
                4,
                text_item(str(installment.amount_due), numeric=True),
            )
            self.schedule_table.setCellWidget(row_index, 5, status_widget(installment.status))
            self.schedule_table.setRowHeight(row_index, 38)

    def _set_metrics(self, loan: Loan) -> None:
        self.principal_metric.setText(money(loan.principal_amount, loan.currency))
        self.repayable_metric.setText(money(loan.total_repayable, loan.currency))
        self.outstanding_metric.setText(money(loan.outstanding_principal, loan.currency))
        self.progress_metric.setText(
            f"{loan.installments_paid}/{loan.installments_total} instalments"
        )

    def _set_actions(self, loan: Loan | None) -> None:
        can_approve = self.session.can("loan.approve")
        can_request = self.session.can("loan.request")
        can_disburse = self.session.can("loan.disburse")
        requester = loan.requested_by if loan is not None else None
        is_own = requester == self.session.me.user.id
        self.approve_button.setEnabled(
            bool(loan and loan.status == "pending" and can_approve and not is_own)
        )
        self.reject_button.setEnabled(
            bool(loan and loan.status == "pending" and can_approve and not is_own)
        )
        self.cancel_button.setEnabled(
            bool(loan and loan.status in {"pending", "approved"} and (can_request or can_approve))
        )
        self.disburse_button.setEnabled(bool(loan and loan.status == "approved" and can_disburse))
        tooltip = (
            "The applicant cannot approve or reject their own application."
            if loan is not None and is_own
            else ""
        )
        self.approve_button.setToolTip(tooltip)
        self.reject_button.setToolTip(tooltip)

    def _show_empty_detail(self) -> None:
        self._selected_id = None
        self._detail = None
        self.detail_title.setText("No loan selected")
        self.detail_status.hide()
        self.detail_meta.setText("Choose a loan from the list to see its schedule.")
        for metric in (
            self.principal_metric,
            self.repayable_metric,
            self.outstanding_metric,
            self.progress_metric,
        ):
            metric.setText("—")
        self.schedule_table.setRowCount(0)
        self._set_actions(None)

    def _selected_loan(self) -> Loan | None:
        row = self.loans_table.currentRow()
        if row < 0:
            return None
        item = self.loans_table.item(row, 0)
        if item is None:
            return None
        return self._loans.get(str(item.data(Qt.ItemDataRole.UserRole) or ""))

    def _failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The loan workspace could not be loaded.")
        self.set_status("Loan refresh failed")

    def _detail_failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The loan schedule could not be loaded.")

    def _apply(self) -> None:
        employees = [
            employee for employee in self._employees.values() if employee.status != "terminated"
        ]
        if not employees:
            QMessageBox.information(
                self, "Loan application", "Create an active employee before applying."
            )
            return
        dialog = LoanApplicationDialog(employees, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.apply_button.setEnabled(False)
        self.set_status("Recording loan application…")
        self.runner.submit(
            lambda: self.session.client.create_loan(
                company_id=self.session.selected_company_id,
                employee_id=payload.employee_id,
                kind=payload.kind,
                principal_amount=payload.principal_amount,
                interest_rate_annual=payload.interest_rate_annual,
                term_months=payload.term_months,
                purpose=payload.purpose,
            ),
            on_success=self._applied,
            on_failure=self._apply_failed,
        )

    def _applied(self, _loan: Loan) -> None:
        self.apply_button.setEnabled(self.session.can("loan.request"))
        self.set_status("Loan application recorded")
        self.refresh()

    def _apply_failed(self, exception: Exception) -> None:
        self.apply_button.setEnabled(self.session.can("loan.request"))
        self._detail_failed(exception)

    def _approve(self) -> None:
        loan = self._selected_loan()
        if loan is None:
            return
        answer = QMessageBox.question(
            self,
            "Approve loan",
            f"Approve {loan.loan_number} for {loan.employee_name}?\n\n"
            f"Principal: {money(loan.principal_amount, loan.currency)}. "
            "Approval does not disburse funds.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_action(
            loan,
            "approve",
            lambda: self.session.client.loan_action(str(loan.id), "approve", version=loan.version),
        )

    def _reject(self) -> None:
        loan = self._selected_loan()
        if loan is None:
            return
        text, accepted = QInputDialog.getText(self, "Reject loan", "Reason for rejection")
        if not accepted:
            return
        note = text.strip()
        if not note:
            self.show_error(self.error_label, "A rejection reason is required.")
            return
        self._run_action(
            loan,
            "reject",
            lambda: self.session.client.loan_action(
                str(loan.id), "reject", version=loan.version, note=note
            ),
        )

    def _cancel(self) -> None:
        loan = self._selected_loan()
        if loan is None:
            return
        text, accepted = QInputDialog.getText(
            self, "Cancel application", "Reason for cancellation (optional)"
        )
        if not accepted:
            return
        note = text.strip() or None
        self._run_action(
            loan,
            "cancel",
            lambda: self.session.client.loan_action(
                str(loan.id), "cancel", version=loan.version, note=note
            ),
        )

    def _disburse(self) -> None:
        loan = self._selected_loan()
        if loan is None:
            return
        dialog = LoanDisbursementDialog(loan, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self._run_action(
            loan,
            "disburse",
            lambda: self.session.client.disburse_loan(
                str(loan.id),
                version=loan.version,
                disbursed_on=payload.disbursed_on,
                first_due_date=payload.first_due_date,
                reference=payload.reference,
            ),
        )

    def _run_action(self, loan: Loan, action: str, operation: Callable[[], LoanDetail]) -> None:
        self.approve_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.disburse_button.setEnabled(False)
        progressive = {
            "approve": "Approving",
            "reject": "Rejecting",
            "cancel": "Cancelling",
            "disburse": "Disbursing",
        }
        self.set_status(f"{progressive.get(action, 'Updating')} {loan.loan_number}…")
        self.runner.submit(
            operation,
            on_success=lambda _result: self._action_done(action),
            on_failure=self._action_failed,
        )

    def _action_done(self, action: str) -> None:
        completed = {
            "approve": "approved",
            "reject": "rejected",
            "cancel": "cancelled",
            "disburse": "disbursed",
        }
        self.set_status(f"Loan {completed.get(action, 'updated')}")
        self.refresh()

    def _action_failed(self, exception: Exception) -> None:
        self._selection()
        self._detail_failed(exception)
