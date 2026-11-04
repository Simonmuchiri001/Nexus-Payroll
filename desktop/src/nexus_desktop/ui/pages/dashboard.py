"""Operational overview built entirely from authoritative API reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nexus_desktop.api.client import ApiError, NetworkError
from nexus_desktop.api.models import (
    Account,
    EmployeePage,
    PayrollRun,
    TrialBalance,
)
from nexus_desktop.state import ApplicationSession
from nexus_desktop.ui.common import (
    Card,
    EmptyState,
    MetricCard,
    PageHeader,
    StatusBadge,
    date_label,
    money,
)
from nexus_desktop.ui.pages.base import BasePage


@dataclass(frozen=True, slots=True)
class DashboardData:
    employees: EmployeePage
    runs: list[PayrollRun]
    trial_balance: TrialBalance
    accounts: list[Account]


class OverviewPage(BasePage):
    def __init__(self, session: ApplicationSession) -> None:
        super().__init__(session)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 26, 32, 30)
        root.setSpacing(20)

        header_row = QHBoxLayout()
        header = PageHeader(
            "Overview",
            "A current view of people, payroll and the ledger.",
        )
        header_row.addWidget(header, 1)
        self.run_button = QPushButton("Prepare payroll")
        self.run_button.setProperty("role", "primary")
        self.run_button.clicked.connect(lambda: self.navigate_requested.emit("payroll"))
        header_row.addWidget(self.run_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header_row)

        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.headcount_card = MetricCard("People on payroll", "—", "Active people records")
        self.net_pay_card = MetricCard("Latest net payroll", "—", "No payroll run yet")
        self.statutory_card = MetricCard(
            "Statutory liabilities", "—", "Payroll balances in the ledger"
        )
        self.ledger_card = MetricCard("Ledger control", "—", "Debits and credits")
        for card in (
            self.headcount_card,
            self.net_pay_card,
            self.statutory_card,
            self.ledger_card,
        ):
            metrics.addWidget(card, 1)
        root.addLayout(metrics)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.cycle_card = Card()
        cycle_layout = QVBoxLayout(self.cycle_card)
        cycle_layout.setContentsMargins(20, 18, 20, 18)
        cycle_layout.setSpacing(12)
        cycle_head = QHBoxLayout()
        cycle_title = QLabel("Latest payroll cycle")
        cycle_title.setProperty("role", "section-title")
        cycle_head.addWidget(cycle_title, 1)
        self.cycle_link = QPushButton("Open payroll")
        self.cycle_link.setProperty("role", "link")
        self.cycle_link.clicked.connect(lambda: self.navigate_requested.emit("payroll"))
        cycle_head.addWidget(self.cycle_link)
        cycle_layout.addLayout(cycle_head)
        rule = QFrame()
        rule.setProperty("accent-rule", "true")
        cycle_layout.addWidget(rule)
        self.cycle_content = QVBoxLayout()
        self.cycle_content.setSpacing(10)
        cycle_layout.addLayout(self.cycle_content)
        cycle_layout.addStretch(1)
        body.addWidget(self.cycle_card, 3)

        self.ledger_panel = Card()
        ledger_layout = QVBoxLayout(self.ledger_panel)
        ledger_layout.setContentsMargins(20, 18, 20, 18)
        ledger_layout.setSpacing(12)
        ledger_title = QLabel("Payroll liabilities")
        ledger_title.setProperty("role", "section-title")
        ledger_layout.addWidget(ledger_title)
        ledger_note = QLabel("Posted balances as of today")
        ledger_note.setProperty("role", "muted")
        ledger_layout.addWidget(ledger_note)
        self.liability_content = QVBoxLayout()
        self.liability_content.setSpacing(8)
        ledger_layout.addLayout(self.liability_content)
        ledger_layout.addStretch(1)
        body.addWidget(self.ledger_panel, 2)
        root.addLayout(body)

        self.assurance_card = Card(raised=True)
        assurance_layout = QHBoxLayout(self.assurance_card)
        assurance_layout.setContentsMargins(20, 14, 20, 14)
        assurance_layout.setSpacing(22)
        mark = QLabel("Trust controls")
        mark.setProperty("role", "section-title")
        assurance_layout.addWidget(mark)
        for text in (
            "Preparer cannot approve",
            "Posted payroll cannot be recalculated",
            "Audit events are append-only",
        ):
            label = QLabel(text)
            label.setProperty("role", "muted")
            assurance_layout.addWidget(label)
        assurance_layout.addStretch(1)
        root.addWidget(self.assurance_card)
        root.addStretch(1)

    def refresh(self) -> None:
        self.clear_error(self.error_label)
        self.set_status("Loading overview…")
        company_id = self.session.selected_company_id

        def load() -> DashboardData:
            return DashboardData(
                employees=self.session.client.list_employees(company_id, page=1, page_size=1),
                runs=self.session.client.list_payroll_runs(company_id, page=1, page_size=25),
                trial_balance=self.session.client.trial_balance(company_id, date.today()),
                accounts=self.session.client.list_accounts(company_id),
            )

        self.runner.submit(
            load,
            on_success=self._render,
            on_failure=self._failed,
            on_finished=lambda: self.set_status("Overview ready"),
        )

    def _failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(
                self.error_label,
                "The overview could not be loaded. Try again or contact your administrator.",
            )

    def _render(self, data: DashboardData) -> None:
        latest = data.runs[0] if data.runs else None
        self.headcount_card.update_metric(
            str(data.employees.total),
            "Payroll-eligible and people records",
        )
        if latest is not None:
            self.net_pay_card.update_metric(
                money(latest.net_total, latest.currency, compact=True),
                f"{latest.run_number} · {latest.status.capitalize()}",
            )
        else:
            self.net_pay_card.update_metric(
                "—",
                "No payroll run has been created for this company",
            )

        liabilities = {
            "2100": "PAYE payable",
            "2110": "NSSF payable",
            "2120": "SHIF payable",
            "2130": "Housing levy payable",
            "2140": "NITA payable",
            "2200": "Net pay payable",
        }
        total_liability = Decimal("0.00")
        statutory_codes = {"2100", "2110", "2120", "2130", "2140"}
        for row in data.trial_balance.rows:
            if row.code in statutory_codes:
                total_liability += row.credit - row.debit
        self.statutory_card.update_metric(
            money(total_liability, data.trial_balance.currency, compact=True),
            f"Posted through {date_label(data.trial_balance.as_of)}",
        )
        if data.trial_balance.balanced:
            self.ledger_card.update_metric(
                "Balanced",
                f"{money(data.trial_balance.total_debit, data.trial_balance.currency, compact=True)} total debits",
            )
        else:
            self.ledger_card.update_metric(
                "Attention",
                "Trial balance does not balance",
            )

        self._render_cycle(latest)
        self._render_liabilities(data.trial_balance, liabilities)

    def _render_cycle(self, latest: PayrollRun | None) -> None:
        while self.cycle_content.count():
            item = self.cycle_content.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child = item.layout()
            if child is not None:
                while child.count():
                    sub = child.takeAt(0)
                    if sub is None:
                        continue
                    sub_widget = sub.widget()
                    if sub_widget is not None:
                        sub_widget.deleteLater()

        if latest is None:
            self.cycle_content.addWidget(
                EmptyState(
                    "No payroll has been prepared",
                    "Start a payroll run when the next pay period opens.",
                )
            )
            return

        summary = QLabel(
            f"{latest.run_number}  ·  {date_label(latest.period_start)} to "
            f"{date_label(latest.period_end)}  ·  {latest.employee_count} people"
        )
        summary.setProperty("role", "muted")
        summary.setWordWrap(True)
        self.cycle_content.addWidget(summary)

        amounts = QHBoxLayout()
        amounts.setSpacing(28)
        for label, value in (
            ("Gross", money(latest.gross_total, latest.currency)),
            ("Deductions", money(latest.deductions_total, latest.currency)),
            ("Net pay", money(latest.net_total, latest.currency)),
        ):
            block = QVBoxLayout()
            block.setSpacing(2)
            caption = QLabel(label)
            caption.setProperty("role", "metric-label")
            amount = QLabel(value)
            amount.setProperty("role", "section-title")
            block.addWidget(caption)
            block.addWidget(amount)
            amounts.addLayout(block)
        amounts.addStretch(1)
        self.cycle_content.addLayout(amounts)

        rail = QHBoxLayout()
        rail.setSpacing(8)
        statuses = [
            ("draft", "Draft"),
            ("calculated", "Calculated"),
            ("approved", "Approved"),
            ("finalized", "Finalised"),
            ("posted", "Posted"),
        ]
        order = [item[0] for item in statuses]
        current_index = order.index(latest.status) if latest.status in order else -1
        for index, (code, label) in enumerate(statuses):
            badge = StatusBadge(code)
            if index > current_index:
                badge.setProperty("tone", "neutral")
                badge.setText(f"{label} · pending")
            else:
                badge.setText(label)
            rail.addWidget(badge)
            if index < len(statuses) - 1:
                arrow = QLabel("→")
                arrow.setProperty("role", "muted")
                rail.addWidget(arrow)
        rail.addStretch(1)
        self.cycle_content.addLayout(rail)

    def _render_liabilities(self, balance: TrialBalance, labels: dict[str, str]) -> None:
        while self.liability_content.count():
            item = self.liability_content.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        rows = {row.code: row for row in balance.rows}
        count = 0
        for code, label in labels.items():
            row = rows.get(code)
            if row is None:
                continue
            value = row.credit - row.debit
            if value == 0:
                continue
            line = QWidget()
            layout = QHBoxLayout(line)
            layout.setContentsMargins(0, 0, 0, 0)
            name = QLabel(label)
            name.setProperty("role", "muted")
            amount = QLabel(money(value, balance.currency))
            amount.setProperty("role", "subtitle")
            layout.addWidget(name, 1)
            layout.addWidget(amount)
            self.liability_content.addWidget(line)
            count += 1
        if count == 0:
            empty = QLabel("No payroll liabilities have been posted yet.")
            empty.setProperty("role", "muted")
            empty.setWordWrap(True)
            self.liability_content.addWidget(empty)
