"""Chart of accounts, trial balance and posted journal view."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import (
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nexus_desktop.api.client import ApiError, NetworkError
from nexus_desktop.api.models import Account, Journal, TrialBalance
from nexus_desktop.state import ApplicationSession
from nexus_desktop.ui.common import (
    PageHeader,
    configure_table,
    date_label,
    money,
    refresh_style,
    text_item,
)
from nexus_desktop.ui.pages.base import BasePage


@dataclass(frozen=True, slots=True)
class AccountingData:
    accounts: list[Account]
    trial_balance: TrialBalance
    journals: list[Journal]


class AccountingPage(BasePage):
    def __init__(self, session: ApplicationSession) -> None:
        super().__init__(session)
        self._loaded = False
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 26, 32, 30)
        root.setSpacing(16)

        root.addWidget(
            PageHeader(
                "Accounting",
                "The posted ledger for the selected company. Draft journals remain "
                "outside the trial balance until posted.",
            )
        )
        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        trial_tab = QWidget()
        trial_layout = QVBoxLayout(trial_tab)
        trial_layout.setContentsMargins(0, 12, 0, 0)
        trial_layout.setSpacing(12)
        filter_row = QHBoxLayout()
        as_of_label = QLabel("As of")
        as_of_label.setProperty("role", "muted")
        self.as_of = QDateEdit(QDate.currentDate())
        self.as_of.setCalendarPopup(True)
        self.as_of.setDisplayFormat("dd MMM yyyy")
        self.as_of.dateChanged.connect(self._load_balance_only)
        self.refresh_button = QPushButton("Refresh ledger")
        self.refresh_button.clicked.connect(self.refresh)
        self.balance_badge = QLabel()
        self.balance_badge.setProperty("role", "badge")
        filter_row.addWidget(as_of_label)
        filter_row.addWidget(self.as_of)
        filter_row.addWidget(self.refresh_button)
        filter_row.addWidget(self.balance_badge)
        filter_row.addStretch(1)
        trial_layout.addLayout(filter_row)

        self.balance_table = QTableWidget()
        configure_table(
            self.balance_table,
            ["Code", "Account", "Type", "Debit", "Credit", "Balance"],
            stretch_column=1,
        )
        self.balance_table.setMinimumHeight(380)
        trial_layout.addWidget(self.balance_table, 1)
        self.balance_footer = QLabel("")
        self.balance_footer.setProperty("role", "muted")
        trial_layout.addWidget(self.balance_footer)
        self.tabs.addTab(trial_tab, "Trial balance")

        accounts_tab = QWidget()
        accounts_layout = QVBoxLayout(accounts_tab)
        accounts_layout.setContentsMargins(0, 12, 0, 0)
        self.accounts_table = QTableWidget()
        configure_table(
            self.accounts_table,
            ["Code", "Account", "Type", "Normal balance", "System account"],
            stretch_column=1,
        )
        self.accounts_table.setMinimumHeight(380)
        accounts_layout.addWidget(self.accounts_table)
        self.tabs.addTab(accounts_tab, "Chart of accounts")

        journals_tab = QWidget()
        journals_layout = QVBoxLayout(journals_tab)
        journals_layout.setContentsMargins(0, 12, 0, 0)
        self.journals_table = QTableWidget()
        configure_table(
            self.journals_table,
            ["Date", "Journal", "Description", "Status", "Debit", "Credit"],
            stretch_column=2,
        )
        self.journals_table.setMinimumHeight(380)
        journals_layout.addWidget(self.journals_table)
        self.journal_note = QLabel("")
        self.journal_note.setProperty("role", "muted")
        journals_layout.addWidget(self.journal_note)
        self.tabs.addTab(journals_tab, "Journals")

    def refresh(self) -> None:
        self.clear_error(self.error_label)
        self.set_status("Loading ledger…")
        company_id = self.session.selected_company_id
        as_of = self.as_of.date().toPyDate()

        def load() -> AccountingData:
            return AccountingData(
                accounts=self.session.client.list_accounts(company_id),
                trial_balance=self.session.client.trial_balance(company_id, as_of),
                journals=self.session.client.list_journals(company_id, page_size=100).items,
            )

        self.runner.submit(
            load,
            on_success=self._render,
            on_failure=self._failed,
            on_finished=lambda: self.set_status("Ledger ready"),
        )

    def _load_balance_only(self) -> None:
        if not self._loaded:
            return
        company_id = self.session.selected_company_id
        as_of = self.as_of.date().toPyDate()
        self.runner.submit(
            lambda: self.session.client.trial_balance(company_id, as_of),
            on_success=self._render_balance,
            on_failure=self._failed,
        )

    def _failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The ledger could not be loaded. Try again.")

    def _render(self, data: AccountingData) -> None:
        self._loaded = True
        self._render_balance(data.trial_balance)
        self.accounts_table.setRowCount(0)
        for row_index, account in enumerate(data.accounts):
            self.accounts_table.insertRow(row_index)
            self.accounts_table.setItem(row_index, 0, text_item(account.code))
            self.accounts_table.setItem(row_index, 1, text_item(account.name))
            self.accounts_table.setItem(row_index, 2, text_item(account.account_type.capitalize()))
            self.accounts_table.setItem(
                row_index, 3, text_item(account.normal_balance.capitalize())
            )
            self.accounts_table.setItem(
                row_index, 4, text_item("Yes" if account.is_system else "No")
            )
            self.accounts_table.setRowHeight(row_index, 38)

        self.journals_table.setRowCount(0)
        for row_index, journal in enumerate(data.journals):
            self.journals_table.insertRow(row_index)
            self.journals_table.setItem(row_index, 0, text_item(date_label(journal.journal_date)))
            self.journals_table.setItem(row_index, 1, text_item(journal.journal_number))
            self.journals_table.setItem(row_index, 2, text_item(journal.description))
            self.journals_table.setItem(row_index, 3, text_item(journal.status.capitalize()))
            self.journals_table.setItem(
                row_index,
                4,
                text_item(money(journal.total_debit, journal.currency), numeric=True),
            )
            self.journals_table.setItem(
                row_index,
                5,
                text_item(money(journal.total_credit, journal.currency), numeric=True),
            )
            self.journals_table.setRowHeight(row_index, 38)
        self.journal_note.setText(
            f"{len(data.journals)} journal entries in the current company ledger."
        )

    def _render_balance(self, balance: TrialBalance) -> None:
        self.balance_table.setRowCount(0)
        for row_index, row in enumerate(balance.rows):
            self.balance_table.insertRow(row_index)
            self.balance_table.setItem(row_index, 0, text_item(row.code))
            self.balance_table.setItem(row_index, 1, text_item(row.name))
            self.balance_table.setItem(row_index, 2, text_item(row.account_type.capitalize()))
            self.balance_table.setItem(
                row_index,
                3,
                text_item(money(row.debit, balance.currency), numeric=True),
            )
            self.balance_table.setItem(
                row_index,
                4,
                text_item(money(row.credit, balance.currency), numeric=True),
            )
            self.balance_table.setItem(
                row_index,
                5,
                text_item(money(abs(row.balance), balance.currency), numeric=True),
            )
            self.balance_table.setRowHeight(row_index, 38)
        self.balance_footer.setText(
            f"Total debits {money(balance.total_debit, balance.currency)}   ·   "
            f"Total credits {money(balance.total_credit, balance.currency)}"
        )
        self.balance_badge.setText("Balanced" if balance.balanced else "Out of balance")
        self.balance_badge.setProperty("tone", "success" if balance.balanced else "danger")
        refresh_style(self.balance_badge)
