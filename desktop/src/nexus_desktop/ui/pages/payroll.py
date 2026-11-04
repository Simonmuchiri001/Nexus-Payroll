"""Payroll preparation, approval, finalisation and accounting posting."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
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
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from nexus_desktop.api.client import ApiError, NetworkError
from nexus_desktop.api.models import (
    PayGroup,
    PayrollPeriod,
    PayrollResult,
    PayrollRun,
)
from nexus_desktop.state import ApplicationSession
from nexus_desktop.tasks import TaskRunner
from nexus_desktop.ui.common import (
    PageHeader,
    StatusBadge,
    configure_table,
    date_label,
    money,
    refresh_style,
    text_item,
)
from nexus_desktop.ui.pages.base import BasePage


@dataclass(frozen=True, slots=True)
class PayrollPageData:
    runs: list[PayrollRun]
    groups: list[PayGroup]
    periods_by_group: dict[str, list[PayrollPeriod]]


class NewRunDialog(QDialog):
    def __init__(
        self,
        groups: list[PayGroup],
        periods_by_group: dict[str, list[PayrollPeriod]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._periods_by_group = periods_by_group
        self.setWindowTitle("New payroll run")
        self.setModal(True)
        self.setMinimumWidth(470)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel("Open a payroll period")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "Only open periods with no active run can be selected. "
            "The statutory rule set is snapshotted when the run is created."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.group_combo = QComboBox()
        for group in groups:
            open_periods = periods_by_group.get(str(group.id), [])
            if not open_periods:
                continue
            self.group_combo.addItem(f"{group.name} · {group.code}", str(group.id))
        self.period_combo = QComboBox()
        self.group_combo.currentIndexChanged.connect(self._populate_periods)
        form.addRow("Pay group", self.group_combo)
        form.addRow("Pay period", self.period_combo)
        root.addLayout(form)
        self._populate_periods()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        create_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if create_button is not None:
            create_button.setText("Create run")
            create_button.setProperty("role", "primary")
            create_button.setEnabled(self.group_combo.count() > 0)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _populate_periods(self) -> None:
        self.period_combo.clear()
        group_id = self.group_combo.currentData()
        if not group_id:
            return
        for period in self._periods_by_group.get(str(group_id), []):
            self.period_combo.addItem(
                f"{date_label(period.period_start)} - {date_label(period.period_end)} "
                f"· paid {date_label(period.payment_date)}",
                str(period.id),
            )

    def selection(self) -> tuple[str, str]:
        return str(self.group_combo.currentData()), str(self.period_combo.currentData())


class PayrollSetupDialog(QDialog):
    """Create the pay group and open period that a payroll run requires."""

    def __init__(
        self,
        session: ApplicationSession,
        groups: list[PayGroup],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self._groups = list(groups)
        self._runner = TaskRunner(self)
        self.setWindowTitle("Payroll setup")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._build()
        self._render_groups()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(16)

        heading = QLabel("Payroll setup")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "A payroll run belongs to one monthly pay group and one open period. "
            "The statutory rule set is snapshotted when the run is created."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        group_card = QFrame()
        group_card.setProperty("card", "true")
        group_layout = QVBoxLayout(group_card)
        group_layout.setContentsMargins(16, 14, 16, 14)
        group_layout.setSpacing(10)
        group_title = QLabel("Pay group")
        group_title.setProperty("role", "section-title")
        group_layout.addWidget(group_title)
        group_form = QFormLayout()
        group_form.setSpacing(8)
        self.group_name = QLineEdit("Monthly Payroll")
        self.group_name.setPlaceholderText("e.g. Monthly Payroll")
        self.group_code = QLineEdit("MONTHLY")
        self.group_code.setPlaceholderText("e.g. MONTHLY")
        currency = QLabel(self.session.company.base_currency)
        currency.setProperty("role", "muted")
        group_form.addRow("Name", self.group_name)
        group_form.addRow("Code", self.group_code)
        group_form.addRow("Currency", currency)
        group_layout.addLayout(group_form)
        self.create_group_button = QPushButton("Create pay group")
        self.create_group_button.clicked.connect(self._create_group)
        group_layout.addWidget(self.create_group_button, 0, Qt.AlignmentFlag.AlignRight)
        root.addWidget(group_card)

        period_card = QFrame()
        period_card.setProperty("card", "true")
        period_layout = QVBoxLayout(period_card)
        period_layout.setContentsMargins(16, 14, 16, 14)
        period_layout.setSpacing(10)
        period_title = QLabel("Open pay period")
        period_title.setProperty("role", "section-title")
        period_layout.addWidget(period_title)
        period_form = QFormLayout()
        period_form.setSpacing(8)
        self.group_combo = QComboBox()
        self.period_start = QDateEdit(self._month_start(QDate.currentDate()))
        self.period_end = QDateEdit(self._month_end(QDate.currentDate()))
        self.payment_date = QDateEdit(self._month_end(QDate.currentDate()).addDays(5))
        for editor in (self.period_start, self.period_end, self.payment_date):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd MMM yyyy")
        period_form.addRow("Pay group", self.group_combo)
        period_form.addRow("Period start", self.period_start)
        period_form.addRow("Period end", self.period_end)
        period_form.addRow("Payment date", self.payment_date)
        period_layout.addLayout(period_form)
        self.create_period_button = QPushButton("Open period")
        self.create_period_button.setProperty("role", "primary")
        self.create_period_button.clicked.connect(self._create_period)
        period_layout.addWidget(self.create_period_button, 0, Qt.AlignmentFlag.AlignRight)
        root.addWidget(period_card)

        self.status_label = QLabel()
        self.status_label.setProperty("role", "success")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        root.addWidget(self.status_label)
        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close_button = QPushButton("Done")
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        root.addLayout(footer)

    @staticmethod
    def _month_start(value: QDate) -> QDate:
        return QDate(value.year(), value.month(), 1)

    @staticmethod
    def _month_end(value: QDate) -> QDate:
        return QDate(value.year(), value.month(), value.daysInMonth())

    def _render_groups(self) -> None:
        current = str(self.group_combo.currentData() or "")
        self.group_combo.clear()
        for group in self._groups:
            self.group_combo.addItem(f"{group.name} · {group.code}", str(group.id))
        index = self.group_combo.findData(current)
        if index >= 0:
            self.group_combo.setCurrentIndex(index)
        has_groups = bool(self._groups)
        self.create_period_button.setEnabled(has_groups)
        self.group_combo.setEnabled(has_groups)

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self.group_name,
            self.group_code,
            self.create_group_button,
            self.group_combo,
            self.period_start,
            self.period_end,
            self.payment_date,
            self.create_period_button,
        ):
            widget.setEnabled(not busy)
        if not busy:
            self._render_groups()

    def _show_error(self, exception: Exception) -> None:
        self._set_busy(False)
        if isinstance(exception, (ApiError, NetworkError)):
            message = exception.message
        else:
            message = "The payroll setup change could not be completed. Try again."
        self.status_label.hide()
        self.error_label.setText(message)
        self.error_label.show()

    def _show_success(self, message: str) -> None:
        self._set_busy(False)
        self.error_label.hide()
        self.status_label.setText(message)
        self.status_label.show()

    def _create_group(self) -> None:
        name = self.group_name.text().strip()
        code = self.group_code.text().strip().upper()
        if len(name) < 2:
            self.error_label.setText("Enter a pay group name.")
            self.error_label.show()
            return
        if len(code) < 2:
            self.error_label.setText("Enter a pay group code.")
            self.error_label.show()
            return
        self.error_label.hide()
        self._set_busy(True)
        self._runner.submit(
            lambda: self.session.client.create_pay_group(
                company_id=self.session.selected_company_id,
                code=code,
                name=name,
                currency=self.session.company.base_currency,
            ),
            on_success=self._group_created,
            on_failure=self._show_error,
        )

    def _group_created(self, group: PayGroup) -> None:
        self._groups.append(group)
        self._show_success(f"Created pay group {group.code}.")
        self._render_groups()
        index = self.group_combo.findData(str(group.id))
        if index >= 0:
            self.group_combo.setCurrentIndex(index)

    def _create_period(self) -> None:
        group_id = self.group_combo.currentData()
        if group_id is None:
            self.error_label.setText("Create a pay group before opening a period.")
            self.error_label.show()
            return
        if self.period_end.date() < self.period_start.date():
            self.error_label.setText("The period end must not precede the start.")
            self.error_label.show()
            return
        if self.payment_date.date() < self.period_end.date():
            self.error_label.setText("The payment date must not precede the period end.")
            self.error_label.show()
            return
        self.error_label.hide()
        self._set_busy(True)
        self._runner.submit(
            lambda: self.session.client.create_payroll_period(
                pay_group_id=str(group_id),
                period_start=self.period_start.date().toPyDate(),
                period_end=self.period_end.date().toPyDate(),
                payment_date=self.payment_date.date().toPyDate(),
            ),
            on_success=self._period_created,
            on_failure=self._show_error,
        )

    def _period_created(self, period: PayrollPeriod) -> None:
        self._show_success(
            f"Opened period {date_label(period.period_start)} - {date_label(period.period_end)}."
        )


class PayrollPage(BasePage):
    def __init__(self, session: ApplicationSession) -> None:
        super().__init__(session)
        self._runs: dict[str, PayrollRun] = {}
        self._selected_run_id: str | None = None
        self._groups: list[PayGroup] = []
        self._periods_by_group: dict[str, list[PayrollPeriod]] = {}
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 26, 32, 30)
        root.setSpacing(16)

        header_row = QHBoxLayout()
        header_row.addWidget(
            PageHeader(
                "Payroll",
                "Prepare, calculate, approve, finalise and post monthly payroll. "
                "Every transition is enforced by the API.",
            ),
            1,
        )
        self.setup_button = QPushButton("Setup")
        self.setup_button.setToolTip("Create pay groups and open payroll periods")
        self.setup_button.clicked.connect(self._open_setup)
        self.setup_button.setEnabled(self.session.can("payroll.calculate"))
        header_row.addWidget(self.setup_button, 0, Qt.AlignmentFlag.AlignBottom)
        self.new_run_button = QPushButton("New run")
        self.new_run_button.setProperty("role", "primary")
        self.new_run_button.clicked.connect(self._open_new_run)
        self.new_run_button.setEnabled(self.session.can("payroll.calculate"))
        header_row.addWidget(self.new_run_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header_row)

        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        runs_title = QLabel("Payroll runs")
        runs_title.setProperty("role", "section-title")
        root.addWidget(runs_title)
        self.runs_table = QTableWidget()
        configure_table(
            self.runs_table,
            ["Run", "Period", "Status", "People", "Gross", "Net pay"],
            stretch_column=1,
        )
        self.runs_table.setMinimumHeight(230)
        self.runs_table.itemSelectionChanged.connect(self._selection_changed)
        root.addWidget(self.runs_table)

        self.empty_label = QLabel(
            "No payroll runs exist yet. Create one when a pay period is open."
        )
        self.empty_label.setProperty("role", "muted")
        self.empty_label.hide()
        root.addWidget(self.empty_label)

        self.action_bar = QFrame()
        self.action_bar.setProperty("card", "true")
        action_layout = QHBoxLayout(self.action_bar)
        action_layout.setContentsMargins(16, 12, 16, 12)
        action_layout.setSpacing(8)
        self.selection_label = QLabel("Select a payroll run to see its controls.")
        self.selection_label.setProperty("role", "muted")
        action_layout.addWidget(self.selection_label, 1)
        self.calculate_button = self._action_button("Calculate", "calculate")
        self.approve_button = self._action_button("Approve", "approve")
        self.finalize_button = self._action_button("Finalise", "finalize")
        self.post_button = self._action_button("Post to ledger", "post")
        for button in (
            self.calculate_button,
            self.approve_button,
            self.finalize_button,
            self.post_button,
        ):
            action_layout.addWidget(button)
        root.addWidget(self.action_bar)

        results_head = QHBoxLayout()
        results_title = QLabel("Payslip calculation")
        results_title.setProperty("role", "section-title")
        self.result_badge = QLabel()
        self.result_badge.setProperty("role", "badge")
        results_head.addWidget(results_title)
        results_head.addWidget(self.result_badge)
        results_head.addStretch(1)
        root.addLayout(results_head)

        self.results_table = QTableWidget()
        configure_table(
            self.results_table,
            [
                "Employee",
                "Gross",
                "Taxable",
                "PAYE",
                "NSSF",
                "SHIF",
                "Housing levy",
                "Net pay",
                "Employer cost",
            ],
            stretch_column=0,
        )
        self.results_table.setMinimumHeight(250)
        root.addWidget(self.results_table)

        self.results_empty = QLabel("Select a calculated run to review payslip figures.")
        self.results_empty.setProperty("role", "muted")
        self.results_empty.hide()
        root.addWidget(self.results_empty)
        root.addStretch(1)

    def _action_button(self, label: str, action: str) -> QPushButton:
        button = QPushButton(label)
        button.clicked.connect(lambda: self._run_action(action))
        return button

    def refresh(self) -> None:
        self.clear_error(self.error_label)
        self.set_status("Loading payroll…")
        company_id = self.session.selected_company_id
        previous_selection = self._selected_run_id

        def load() -> PayrollPageData:
            groups = self.session.client.list_pay_groups(company_id)
            periods: dict[str, list[PayrollPeriod]] = {}
            for group in groups:
                periods[str(group.id)] = [
                    period
                    for period in self.session.client.list_payroll_periods(str(group.id))
                    if period.status == "open"
                ]
            return PayrollPageData(
                runs=self.session.client.list_payroll_runs(company_id, page_size=100),
                groups=groups,
                periods_by_group=periods,
            )

        self.runner.submit(
            load,
            on_success=lambda data: self._render(data, preferred_selection=previous_selection),
            on_failure=self._failed,
            on_finished=lambda: self.set_status("Payroll ready"),
        )

    def _failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "Payroll data could not be loaded. Try again.")

    def _render(self, data: PayrollPageData, preferred_selection: str | None = None) -> None:
        self._groups = data.groups
        self._periods_by_group = data.periods_by_group
        self._runs = {str(run.id): run for run in data.runs}
        open_periods = sum(len(periods) for periods in data.periods_by_group.values())
        self.new_run_button.setEnabled(self.session.can("payroll.calculate") and open_periods > 0)
        self.new_run_button.setToolTip(
            "" if open_periods > 0 else "Use Setup to add a pay group and open a payroll period."
        )

        selected = preferred_selection if preferred_selection in self._runs else None
        if selected is None and data.runs:
            selected = str(data.runs[0].id)
        self._selected_run_id = selected

        self.runs_table.blockSignals(True)
        self.runs_table.setRowCount(0)
        for row_index, run in enumerate(data.runs):
            self.runs_table.insertRow(row_index)
            self.runs_table.setItem(row_index, 0, text_item(run.run_number))
            self.runs_table.setItem(
                row_index,
                1,
                text_item(f"{date_label(run.period_start)} - {date_label(run.period_end)}"),
            )
            self.runs_table.setCellWidget(row_index, 2, self._status_cell(run.status))
            self.runs_table.setItem(row_index, 3, text_item(str(run.employee_count), numeric=True))
            self.runs_table.setItem(
                row_index, 4, text_item(money(run.gross_total, run.currency), numeric=True)
            )
            self.runs_table.setItem(
                row_index, 5, text_item(money(run.net_total, run.currency), numeric=True)
            )
            run_item = self.runs_table.item(row_index, 0)
            if run_item is not None:
                run_item.setData(Qt.ItemDataRole.UserRole, str(run.id))
            self.runs_table.setRowHeight(row_index, 42)
            if str(run.id) == selected:
                self.runs_table.selectRow(row_index)
        self.runs_table.blockSignals(False)
        self.empty_label.setVisible(not data.runs)

        if selected is not None:
            self._update_actions(self._runs[selected])
            self._load_results(selected)
        else:
            self._update_actions(None)
            self._clear_results()

    @staticmethod
    def _status_cell(status: str) -> QWidget:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(StatusBadge(status))
        layout.addStretch(1)
        return wrapper

    def _selection_changed(self) -> None:
        row = self.runs_table.currentRow()
        if row < 0:
            return
        item = self.runs_table.item(row, 0)
        if item is None:
            return
        run_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not run_id or run_id == self._selected_run_id:
            return
        self._selected_run_id = run_id
        run = self._runs.get(run_id)
        self._update_actions(run)
        self._load_results(run_id)

    def _update_actions(self, run: PayrollRun | None) -> None:
        if run is None:
            self.selection_label.setText("Select a payroll run to see its controls.")
        else:
            self.selection_label.setText(
                f"{run.run_number}  ·  {date_label(run.period_start)} - "
                f"{date_label(run.period_end)}  ·  {run.status.capitalize()}"
            )
        mapping = {
            self.calculate_button: (
                run is not None
                and run.status in {"draft", "calculated"}
                and self.session.can("payroll.calculate")
            ),
            self.approve_button: (
                run is not None
                and run.status == "calculated"
                and self.session.can("payroll.approve")
            ),
            self.finalize_button: (
                run is not None
                and run.status == "approved"
                and self.session.can("payroll.finalize")
            ),
            self.post_button: (
                run is not None and run.status == "finalized" and self.session.can("payroll.post")
            ),
        }
        next_action: QPushButton | None = None
        if run is not None:
            order = {
                "draft": self.calculate_button,
                "calculated": self.approve_button,
                "approved": self.finalize_button,
                "finalized": self.post_button,
            }
            next_action = order.get(run.status)
        for button, enabled in mapping.items():
            button.setEnabled(enabled)
            role = "primary" if button is next_action and enabled else ""
            button.setProperty("role", role)
            refresh_style(button)

    def _load_results(self, run_id: str) -> None:
        self.results_empty.hide()
        self.result_badge.setText("Loading…")
        self.result_badge.setProperty("tone", "neutral")
        refresh_style(self.result_badge)
        self.runner.submit(
            lambda: self.session.client.payroll_results(run_id),
            on_success=self._render_results,
            on_failure=self._results_failed,
        )

    def _render_results(self, results: list[PayrollResult]) -> None:
        self.results_table.setRowCount(0)
        for row_index, result in enumerate(results):
            self.results_table.insertRow(row_index)
            employee = result.employee_name or result.employee_number or str(result.employee_id)
            self.results_table.setItem(row_index, 0, text_item(employee))
            values = (
                result.gross,
                result.taxable_income,
                result.paye,
                result.nssf_employee,
                result.shif_employee,
                result.housing_levy_employee,
                result.net_pay,
                result.employer_cost,
            )
            for column, value in enumerate(values, start=1):
                self.results_table.setItem(
                    row_index, column, text_item(money(value, result.currency), numeric=True)
                )
            self.results_table.setRowHeight(row_index, 40)
        self.results_empty.setVisible(not results)
        if results:
            self.result_badge.setText(f"{len(results)} payslips")
            self.result_badge.setProperty("tone", "success")
        else:
            self.result_badge.setText("No results")
            self.result_badge.setProperty("tone", "neutral")
        refresh_style(self.result_badge)

    def _results_failed(self, exception: Exception) -> None:
        self.result_badge.setText("Unavailable")
        self.result_badge.setProperty("tone", "danger")
        refresh_style(self.result_badge)
        self.results_empty.setText(
            exception.message
            if isinstance(exception, (ApiError, NetworkError))
            else "Payslip results could not be loaded."
        )
        self.results_empty.show()

    def _clear_results(self) -> None:
        self.results_table.setRowCount(0)
        self.results_empty.setText("Select a calculated run to review payslip figures.")
        self.results_empty.show()
        self.result_badge.setText("")
        self.result_badge.setProperty("tone", "neutral")

    def _open_setup(self) -> None:
        dialog = PayrollSetupDialog(self.session, self._groups, self)
        dialog.exec()
        self.refresh()

    def _open_new_run(self) -> None:
        if not self._groups:
            QMessageBox.information(
                self,
                "New payroll run",
                "No pay group is available. Create a pay group and open period first.",
            )
            return
        dialog = NewRunDialog(self._groups, self._periods_by_group, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        group_id, period_id = dialog.selection()
        if not group_id or not period_id:
            return
        self.set_status("Creating payroll run…")
        self.runner.submit(
            lambda: self.session.client.create_payroll_run(
                company_id=self.session.selected_company_id,
                pay_group_id=group_id,
                payroll_period_id=period_id,
            ),
            on_success=self._run_created,
            on_failure=self._action_failed,
        )

    def _run_created(self, run: PayrollRun) -> None:
        self._selected_run_id = str(run.id)
        self.set_status(f"Created {run.run_number}")
        self.refresh()

    def _run_action(self, action: str) -> None:
        if self._selected_run_id is None:
            return
        run = self._runs.get(self._selected_run_id)
        if run is None:
            return
        prompts = {
            "approve": (
                f"Approve {run.run_number}? The calculation becomes immutable and "
                "can no longer be recalculated."
            ),
            "finalize": (f"Finalise {run.run_number}? This locks the payroll results for payment."),
            "post": (
                f"Post {run.run_number} to the general ledger? "
                "This creates exactly one balanced journal."
            ),
        }
        if action in prompts:
            answer = QMessageBox.question(
                self,
                "Confirm payroll action",
                prompts[action],
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._set_actions_busy(True)
        self.set_status(f"Running payroll {action}…")
        self.runner.submit(
            lambda: self.session.client.payroll_action(str(run.id), action),
            on_success=self._action_completed,
            on_failure=self._action_failed,
            on_finished=lambda: self._set_actions_busy(False),
        )

    def _set_actions_busy(self, busy: bool) -> None:
        for button in (
            self.calculate_button,
            self.approve_button,
            self.finalize_button,
            self.post_button,
        ):
            if busy:
                button.setEnabled(False)

    def _action_completed(self, run: PayrollRun) -> None:
        self._selected_run_id = str(run.id)
        self.set_status(f"{run.run_number} is now {run.status}")
        self.error_label.hide()
        self.refresh()

    def _action_failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(
                self.error_label,
                "The payroll action could not be completed. Reload and try again.",
            )
        self._update_actions(
            self._runs.get(self._selected_run_id) if self._selected_run_id else None
        )
