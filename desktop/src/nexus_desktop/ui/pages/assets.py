"""Fixed asset register, categories, depreciation and disposal workspace."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
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
    QSpinBox,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nexus_desktop.api.client import ApiError, NetworkError
from nexus_desktop.api.models import Account, AssetCategory, AssetPage, FixedAsset
from nexus_desktop.state import ApplicationSession
from nexus_desktop.ui.common import (
    PageHeader,
    configure_table,
    money,
    status_widget,
    text_item,
)
from nexus_desktop.ui.pages.base import BasePage

PAGE_SIZE = 25


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _amount_editor(
    value: str | Decimal, currency: str, *, allow_zero: bool = False
) -> QDoubleSpinBox:
    editor = QDoubleSpinBox()
    editor.setRange(0.0 if allow_zero else 0.01, 1_000_000_000.0)
    editor.setDecimals(2)
    editor.setSingleStep(100.0)
    editor.setGroupSeparatorShown(True)
    if currency:
        editor.setSuffix(f" {currency}")
    editor.setValue(float(value))
    return editor


def _amount(value: QDoubleSpinBox) -> str:
    return f"{value.value():.2f}"


def _account_combo(
    accounts: list[Account],
    account_types: tuple[str, ...],
    normal_balance: str | None = None,
) -> QComboBox:
    combo = QComboBox()
    for account in accounts:
        if account.account_type not in account_types or not account.is_active:
            continue
        if normal_balance is not None and account.normal_balance != normal_balance:
            continue
        combo.addItem(f"{account.code} · {account.name}", str(account.id))
    return combo


@dataclass(frozen=True, slots=True)
class CategoryInput:
    code: str
    name: str
    description: str | None
    cost_account_id: str
    accumulated_depreciation_account_id: str
    depreciation_expense_account_id: str
    gain_loss_account_id: str


@dataclass(frozen=True, slots=True)
class AssetInput:
    category_id: str
    asset_number: str
    name: str
    description: str | None
    serial_reference: str | None
    acquisition_date: date
    in_service_date: date
    cost: str
    residual_value: str
    useful_life_months: int


@dataclass(frozen=True, slots=True)
class CapitalizationInput:
    funding_account_id: str
    reference: str
    note: str | None


@dataclass(frozen=True, slots=True)
class DepreciationInput:
    period_start: date
    period_end: date
    reference: str | None


@dataclass(frozen=True, slots=True)
class DisposalInput:
    disposal_date: date
    proceeds_amount: str
    proceeds_account_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class AssetData:
    accounts: list[Account]
    categories: list[AssetCategory]
    assets: AssetPage


class AssetCategoryDialog(QDialog):
    def __init__(
        self,
        accounts: list[Account],
        category: AssetCategory | None = None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._category = category
        self.setWindowTitle("New asset category" if category is None else "Edit asset category")
        self.setModal(True)
        self.setMinimumWidth(580)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel("New asset category" if category is None else "Edit asset category")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "Each category maps assets to a cost account, accumulated depreciation, "
            "depreciation expense and disposal gain/loss account."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.code = QLineEdit(category.code if category else "")
        self.code.setPlaceholderText("e.g. MOTOR")
        self.code.setMaxLength(20)
        self.code.setEnabled(category is None)
        self.name = QLineEdit(category.name if category else "")
        self.name.setPlaceholderText("Category name")
        self.description = QTextEdit(
            category.description if category and category.description else ""
        )
        self.description.setPlaceholderText("Optional category guidance")
        self.description.setFixedHeight(64)
        self.cost_account = _account_combo(accounts, ("asset",), "debit")
        self.accumulated_account = _account_combo(accounts, ("asset",), "credit")
        self.expense_account = _account_combo(accounts, ("expense",), "debit")
        self.gain_loss_account = _account_combo(accounts, ("expense", "revenue"))
        if category is not None:
            for combo, value in (
                (self.cost_account, category.cost_account_id),
                (self.accumulated_account, category.accumulated_depreciation_account_id),
                (self.expense_account, category.depreciation_expense_account_id),
                (self.gain_loss_account, category.gain_loss_account_id),
            ):
                index = combo.findData(str(value))
                if index >= 0:
                    combo.setCurrentIndex(index)
        form.addRow("Code", self.code)
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        form.addRow("Cost account", self.cost_account)
        form.addRow("Accumulated depreciation", self.accumulated_account)
        form.addRow("Depreciation expense", self.expense_account)
        form.addRow("Gain/loss account", self.gain_loss_account)
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
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self._category is None and len(self.code.text().strip()) < 1:
            self.error.setText("Enter a category code.")
            self.error.show()
            return
        if len(self.name.text().strip()) < 1:
            self.error.setText("Enter a category name.")
            self.error.show()
            return
        combos = (
            self.cost_account,
            self.accumulated_account,
            self.expense_account,
            self.gain_loss_account,
        )
        selected = [str(combo.currentData()) for combo in combos if combo.currentData()]
        if len(selected) != 4:
            self.error.setText("Map all four ledger accounts for this category.")
            self.error.show()
            return
        if len(set(selected)) != 4:
            self.error.setText("Each category mapping must use a different ledger account.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> CategoryInput:
        return CategoryInput(
            code=self.code.text().strip().upper(),
            name=self.name.text().strip(),
            description=self.description.toPlainText().strip() or None,
            cost_account_id=str(self.cost_account.currentData()),
            accumulated_depreciation_account_id=str(self.accumulated_account.currentData()),
            depreciation_expense_account_id=str(self.expense_account.currentData()),
            gain_loss_account_id=str(self.gain_loss_account.currentData()),
        )


class AssetDialog(QDialog):
    def __init__(
        self,
        categories: list[AssetCategory],
        currency: str,
        asset: FixedAsset | None = None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset = asset
        self._currency = currency
        self.setWindowTitle("New fixed asset" if asset is None else "Edit fixed asset")
        self.setModal(True)
        self.setMinimumWidth(560)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel("New fixed asset" if asset is None else "Edit fixed asset")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "Financial fields are locked once the asset is submitted. Depreciation and "
            "net book value are calculated by the API."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.category = QComboBox()
        for category in categories:
            if category.is_active:
                self.category.addItem(f"{category.code} · {category.name}", str(category.id))
        self.asset_number = QLineEdit(asset.asset_number if asset else "")
        self.asset_number.setPlaceholderText("e.g. FA-0001")
        self.asset_number.setMaxLength(40)
        self.name = QLineEdit(asset.name if asset else "")
        self.name.setPlaceholderText("Asset name")
        self.description = QTextEdit(asset.description if asset and asset.description else "")
        self.description.setPlaceholderText("Optional description")
        self.description.setFixedHeight(60)
        self.serial_reference = QLineEdit(
            asset.serial_reference if asset and asset.serial_reference else ""
        )
        self.serial_reference.setPlaceholderText("Serial or registration reference")
        self.acquisition_date = QDateEdit(
            _qdate(asset.acquisition_date) if asset else QDate.currentDate()
        )
        self.acquisition_date.setCalendarPopup(True)
        self.acquisition_date.setDisplayFormat("dd MMM yyyy")
        self.in_service_date = QDateEdit(
            _qdate(asset.in_service_date) if asset else QDate.currentDate()
        )
        self.in_service_date.setCalendarPopup(True)
        self.in_service_date.setDisplayFormat("dd MMM yyyy")
        self.cost = _amount_editor(asset.cost if asset else "0.00", currency)
        self.residual_value = _amount_editor(
            asset.residual_value if asset else "0.00", currency, allow_zero=True
        )
        self.useful_life = QSpinBox()
        self.useful_life.setRange(1, 1200)
        self.useful_life.setSuffix(" months")
        self.useful_life.setValue(asset.useful_life_months if asset else 60)
        if asset is not None:
            index = self.category.findData(str(asset.category_id))
            if index >= 0:
                self.category.setCurrentIndex(index)
        form.addRow("Category", self.category)
        form.addRow("Asset number", self.asset_number)
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        form.addRow("Serial reference", self.serial_reference)
        form.addRow("Acquisition date", self.acquisition_date)
        form.addRow("In-service date", self.in_service_date)
        form.addRow("Cost", self.cost)
        form.addRow("Residual value", self.residual_value)
        form.addRow("Useful life", self.useful_life)
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
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self.category.currentData() is None:
            self.error.setText("Select an active asset category.")
            self.error.show()
            return
        if len(self.asset_number.text().strip()) < 1:
            self.error.setText("Enter the asset number.")
            self.error.show()
            return
        if len(self.name.text().strip()) < 1:
            self.error.setText("Enter the asset name.")
            self.error.show()
            return
        if self.in_service_date.date() < self.acquisition_date.date():
            self.error.setText("The in-service date must not precede the acquisition date.")
            self.error.show()
            return
        if self.residual_value.value() > self.cost.value():
            self.error.setText("The residual value must not exceed the cost.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> AssetInput:
        return AssetInput(
            category_id=str(self.category.currentData()),
            asset_number=self.asset_number.text().strip(),
            name=self.name.text().strip(),
            description=self.description.toPlainText().strip() or None,
            serial_reference=self.serial_reference.text().strip() or None,
            acquisition_date=self.acquisition_date.date().toPyDate(),
            in_service_date=self.in_service_date.date().toPyDate(),
            cost=_amount(self.cost),
            residual_value=_amount(self.residual_value),
            useful_life_months=self.useful_life.value(),
        )


class CapitalizationDialog(QDialog):
    def __init__(
        self, accounts: list[Account], asset: FixedAsset, *, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Approve and capitalize asset")
        self.setModal(True)
        self.setMinimumWidth(480)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel(f"Capitalize {asset.asset_number}")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            f"{asset.name} · {money(asset.cost, asset.currency)}. The API posts the "
            "capitalization journal when approval succeeds."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)
        form = QFormLayout()
        form.setSpacing(10)
        self.funding_account = _account_combo(accounts, ("asset", "liability", "equity"))
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("Capitalization reference")
        self.note = QTextEdit()
        self.note.setPlaceholderText("Optional approval note")
        self.note.setFixedHeight(64)
        form.addRow("Funding account", self.funding_account)
        form.addRow("Reference", self.reference)
        form.addRow("Approval note", self.note)
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
            save.setText("Approve and post")
            save.setProperty("role", "primary")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self.funding_account.currentData() is None:
            self.error.setText("Select a funding account.")
            self.error.show()
            return
        if len(self.reference.text().strip()) < 2:
            self.error.setText("Enter a capitalization reference.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> CapitalizationInput:
        return CapitalizationInput(
            funding_account_id=str(self.funding_account.currentData()),
            reference=self.reference.text().strip(),
            note=self.note.toPlainText().strip() or None,
        )


class DepreciationDialog(QDialog):
    def __init__(self, asset: FixedAsset, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._asset = asset
        self.setWindowTitle("Post depreciation")
        self.setModal(True)
        self.setMinimumWidth(440)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel(f"Post depreciation · {asset.asset_number}")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(f"{asset.name} · monthly straight-line amount is calculated by the API.")
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)
        form = QFormLayout()
        form.setSpacing(10)
        today = date.today().replace(day=1)
        self.month = QDateEdit(_qdate(today))
        self.month.setCalendarPopup(True)
        self.month.setDisplayFormat("MMMM yyyy")
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("Optional depreciation reference")
        form.addRow("Depreciation month", self.month)
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
            save.setText("Post depreciation")
            save.setProperty("role", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def payload(self) -> DepreciationInput:
        selected = self.month.date().toPyDate()
        period_start = selected.replace(day=1)
        period_end = selected.replace(day=calendar.monthrange(selected.year, selected.month)[1])
        return DepreciationInput(
            period_start=period_start,
            period_end=period_end,
            reference=self.reference.text().strip() or None,
        )


class DisposalDialog(QDialog):
    def __init__(
        self,
        accounts: list[Account],
        asset: FixedAsset,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset = asset
        self.setWindowTitle("Request asset disposal")
        self.setModal(True)
        self.setMinimumWidth(500)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        heading = QLabel(f"Dispose {asset.asset_number}")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            f"Net book value is {money(asset.net_book_value, asset.currency)}. "
            "A different finance user must approve the disposal."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)
        form = QFormLayout()
        form.setSpacing(10)
        self.disposal_date = QDateEdit(QDate.currentDate())
        self.disposal_date.setCalendarPopup(True)
        self.disposal_date.setDisplayFormat("dd MMM yyyy")
        self.proceeds = _amount_editor("0.00", asset.currency, allow_zero=True)
        self.proceeds_account = _account_combo(accounts, ("asset", "revenue"))
        self.reason = QTextEdit()
        self.reason.setPlaceholderText("Disposal reason")
        self.reason.setFixedHeight(64)
        form.addRow("Disposal date", self.disposal_date)
        form.addRow("Proceeds", self.proceeds)
        form.addRow("Proceeds account", self.proceeds_account)
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
            save.setText("Request disposal")
            save.setProperty("role", "primary")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate(self) -> None:
        if self.disposal_date.date() < _qdate(self._asset.in_service_date):
            self.error.setText("The disposal date cannot precede the in-service date.")
            self.error.show()
            return
        if self.proceeds_account.currentData() is None:
            self.error.setText("Select a proceeds account.")
            self.error.show()
            return
        if len(self.reason.toPlainText().strip()) < 3:
            self.error.setText("Enter a disposal reason of at least three characters.")
            self.error.show()
            return
        self.accept()

    def payload(self) -> DisposalInput:
        return DisposalInput(
            disposal_date=self.disposal_date.date().toPyDate(),
            proceeds_amount=_amount(self.proceeds),
            proceeds_account_id=str(self.proceeds_account.currentData()),
            reason=self.reason.toPlainText().strip(),
        )


class AssetsPage(BasePage):
    def __init__(self, session: ApplicationSession) -> None:
        super().__init__(session)
        self._accounts: dict[str, Account] = {}
        self._categories: dict[str, AssetCategory] = {}
        self._assets: dict[str, FixedAsset] = {}
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
                "Fixed assets",
                "Asset register, capitalization approvals, depreciation and disposals.",
            ),
            1,
        )
        self.new_category_button = QPushButton("New category")
        self.new_category_button.setProperty("role", "ghost")
        self.new_category_button.clicked.connect(self._new_category)
        header.addWidget(self.new_category_button, 0, Qt.AlignmentFlag.AlignBottom)
        self.new_asset_button = QPushButton("New asset")
        self.new_asset_button.setProperty("role", "primary")
        self.new_asset_button.clicked.connect(self._new_asset)
        header.addWidget(self.new_asset_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header)
        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_register_tab(), "Register")
        self.tabs.addTab(self._build_categories_tab(), "Categories")
        root.addWidget(self.tabs, 1)
        self._update_actions()

    def _build_register_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Status"))
        self.status_filter = QComboBox()
        self.status_filter.addItem("All assets", "")
        for status in (
            "draft",
            "submitted",
            "active",
            "disposal_pending",
            "disposed",
            "rejected",
            "cancelled",
        ):
            self.status_filter.addItem(status.replace("_", " ").title(), status)
        self.status_filter.currentIndexChanged.connect(self._filter_assets)
        toolbar.addWidget(self.status_filter)
        toolbar.addStretch(1)
        self.asset_count = QLabel("")
        self.asset_count.setProperty("role", "muted")
        toolbar.addWidget(self.asset_count)
        refresh = QPushButton("Refresh")
        refresh.setProperty("role", "ghost")
        refresh.clicked.connect(self.refresh)
        toolbar.addWidget(refresh)
        layout.addLayout(toolbar)

        self.assets_table = QTableWidget()
        configure_table(
            self.assets_table,
            [
                "Asset",
                "Name",
                "Category",
                "Cost",
                "Accumulated depreciation",
                "Net book value",
                "Status",
            ],
            stretch_column=1,
        )
        self.assets_table.itemSelectionChanged.connect(self._update_actions)
        layout.addWidget(self.assets_table, 1)

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
        self.edit_asset_button = QPushButton("Edit draft")
        self.edit_asset_button.clicked.connect(self._edit_asset)
        self.submit_asset_button = QPushButton("Submit")
        self.submit_asset_button.clicked.connect(self._submit_asset)
        self.approve_asset_button = QPushButton("Approve")
        self.approve_asset_button.clicked.connect(self._approve_asset)
        self.reject_asset_button = QPushButton("Reject")
        self.reject_asset_button.setProperty("role", "ghost")
        self.reject_asset_button.clicked.connect(self._reject_asset)
        self.cancel_asset_button = QPushButton("Cancel")
        self.cancel_asset_button.setProperty("role", "ghost")
        self.cancel_asset_button.clicked.connect(self._cancel_asset)
        self.depreciate_button = QPushButton("Depreciate")
        self.depreciate_button.clicked.connect(self._depreciate_asset)
        self.dispose_button = QPushButton("Dispose")
        self.dispose_button.setProperty("role", "primary")
        self.dispose_button.clicked.connect(self._dispose_asset)
        self.approve_disposal_button = QPushButton("Approve disposal")
        self.approve_disposal_button.clicked.connect(self._approve_disposal)
        self.reject_disposal_button = QPushButton("Reject disposal")
        self.reject_disposal_button.setProperty("role", "ghost")
        self.reject_disposal_button.clicked.connect(self._reject_disposal)
        for button in (
            self.edit_asset_button,
            self.submit_asset_button,
            self.approve_asset_button,
            self.reject_asset_button,
            self.cancel_asset_button,
            self.depreciate_button,
            self.dispose_button,
            self.approve_disposal_button,
            self.reject_disposal_button,
        ):
            actions.addWidget(button)
        layout.addLayout(actions)
        return tab

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
        layout.addLayout(toolbar)

        self.categories_table = QTableWidget()
        configure_table(
            self.categories_table,
            [
                "Code",
                "Name",
                "Cost account",
                "Accumulated depreciation",
                "Depreciation expense",
                "Gain/loss account",
                "Status",
            ],
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

    def refresh(self) -> None:
        self.clear_error(self.error_label)
        self.set_status("Loading fixed assets…")
        company_id = self.session.selected_company_id
        status_value = self.status_filter.currentData()
        status = str(status_value) if status_value else None
        page = self._page

        def load() -> AssetData:
            return AssetData(
                accounts=self.session.client.list_accounts(company_id),
                categories=self.session.client.list_asset_categories(
                    company_id, include_inactive=True
                ),
                assets=self.session.client.list_assets(
                    company_id, page=page, page_size=PAGE_SIZE, status=status
                ),
            )

        self.runner.submit(
            load,
            on_success=self._render,
            on_failure=self._failed,
            on_finished=lambda: self.set_status("Fixed assets workspace ready"),
        )

    def _render(self, data: AssetData) -> None:
        self._accounts = {str(account.id): account for account in data.accounts}
        self._categories = {str(category.id): category for category in data.categories}
        self._assets = {str(asset.id): asset for asset in data.assets.items}
        self._page = data.assets.page
        self._total = data.assets.total
        self._render_categories()
        self._render_assets()
        self._update_actions()

    def _render_categories(self) -> None:
        selected = self._selected_category_id()
        filter_value = self.category_status_filter.currentData()
        status = str(filter_value) if filter_value else ""
        categories = [
            category
            for category in sorted(self._categories.values(), key=lambda item: item.name)
            if not status
            or (status == "active" and category.is_active)
            or (status == "archived" and not category.is_active)
        ]
        self.categories_table.blockSignals(True)
        self.categories_table.setRowCount(0)
        for row, category in enumerate(categories):
            self.categories_table.insertRow(row)
            item = text_item(category.code)
            item.setData(Qt.ItemDataRole.UserRole, str(category.id))
            self.categories_table.setItem(row, 0, item)
            self.categories_table.setItem(row, 1, text_item(category.name))
            for column, account_id in enumerate(
                (
                    category.cost_account_id,
                    category.accumulated_depreciation_account_id,
                    category.depreciation_expense_account_id,
                    category.gain_loss_account_id,
                ),
                start=2,
            ):
                account = self._accounts.get(str(account_id))
                self.categories_table.setItem(
                    row,
                    column,
                    text_item(f"{account.code} · {account.name}" if account else "—"),
                )
            self.categories_table.setCellWidget(
                row, 6, status_widget("active" if category.is_active else "archived")
            )
            self.categories_table.setRowHeight(row, 40)
        self.categories_table.blockSignals(False)
        self.category_count.setText(
            f"{len(categories)} categor{'y' if len(categories) == 1 else 'ies'}"
        )
        if categories:
            self.categories_table.selectRow(self._row_for_id(self.categories_table, selected))

    def _render_assets(self) -> None:
        selected = self._selected_asset_id()
        assets = sorted(self._assets.values(), key=lambda item: item.asset_number)
        self.assets_table.blockSignals(True)
        self.assets_table.setRowCount(0)
        for row, asset in enumerate(assets):
            self.assets_table.insertRow(row)
            item = text_item(asset.asset_number)
            item.setData(Qt.ItemDataRole.UserRole, str(asset.id))
            self.assets_table.setItem(row, 0, item)
            self.assets_table.setItem(row, 1, text_item(asset.name))
            self.assets_table.setItem(
                row, 2, text_item(f"{asset.category_code} · {asset.category_name}")
            )
            self.assets_table.setItem(
                row, 3, text_item(money(asset.cost, asset.currency), numeric=True)
            )
            self.assets_table.setItem(
                row,
                4,
                text_item(money(asset.accumulated_depreciation, asset.currency), numeric=True),
            )
            self.assets_table.setItem(
                row, 5, text_item(money(asset.net_book_value, asset.currency), numeric=True)
            )
            self.assets_table.setCellWidget(row, 6, status_widget(asset.status))
            self.assets_table.setRowHeight(row, 40)
        self.assets_table.blockSignals(False)
        start = (self._page - 1) * PAGE_SIZE + 1 if self._total else 0
        end = min(self._page * PAGE_SIZE, self._total)
        self.asset_count.setText(
            f"{start} to {end} of {self._total} asset(s)" if self._total else "No assets"
        )
        self.previous_page_button.setEnabled(self._page > 1)
        self.next_page_button.setEnabled(self._page * PAGE_SIZE < self._total)
        if assets:
            self.assets_table.selectRow(self._row_for_id(self.assets_table, selected))

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

    def _selected_asset_id(self) -> str | None:
        row = self.assets_table.currentRow()
        item = self.assets_table.item(row, 0) if row >= 0 else None
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return str(value) if value else None

    def _selected_category(self) -> AssetCategory | None:
        category_id = self._selected_category_id()
        return self._categories.get(category_id) if category_id else None

    def _selected_asset(self) -> FixedAsset | None:
        asset_id = self._selected_asset_id()
        return self._assets.get(asset_id) if asset_id else None

    def _update_actions(self) -> None:
        category = self._selected_category()
        asset = self._selected_asset()
        can_manage = self.session.can("asset.manage")
        can_approve = self.session.can("asset.approve")
        can_depreciate = self.session.can("asset.depreciate")
        can_dispose = self.session.can("asset.dispose")
        is_requester = asset is not None and asset.requested_by == self.session.me.user.id
        self.new_category_button.setEnabled(can_manage)
        self.new_asset_button.setEnabled(
            can_manage and any(category.is_active for category in self._categories.values())
        )
        self.edit_category_button.setEnabled(can_manage and category is not None)
        self.toggle_category_button.setEnabled(can_manage and category is not None)
        self.toggle_category_button.setText(
            "Restore" if category is not None and not category.is_active else "Archive"
        )
        self.edit_asset_button.setEnabled(
            can_manage and asset is not None and asset.status in {"draft", "rejected"}
        )
        self.submit_asset_button.setEnabled(
            can_manage and asset is not None and asset.status in {"draft", "rejected"}
        )
        self.approve_asset_button.setEnabled(
            can_approve and asset is not None and asset.status == "submitted" and not is_requester
        )
        self.reject_asset_button.setEnabled(
            can_approve and asset is not None and asset.status == "submitted" and not is_requester
        )
        self.cancel_asset_button.setEnabled(
            can_manage and asset is not None and asset.status in {"draft", "submitted"}
        )
        self.depreciate_button.setEnabled(
            can_depreciate and asset is not None and asset.status == "active"
        )
        self.dispose_button.setEnabled(
            can_dispose and asset is not None and asset.status == "active"
        )
        self.approve_disposal_button.setEnabled(
            can_dispose and asset is not None and asset.status == "disposal_pending"
        )
        self.reject_disposal_button.setEnabled(
            can_dispose and asset is not None and asset.status == "disposal_pending"
        )

    def _failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The fixed assets workspace could not be loaded.")
        self.set_status("Fixed asset refresh failed")

    def _action_failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "The fixed asset action could not be completed.")
        self._update_actions()

    def _new_category(self) -> None:
        dialog = AssetCategoryDialog(list(self._accounts.values()), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.runner.submit(
            lambda: self.session.client.create_asset_category(
                company_id=self.session.selected_company_id,
                code=payload.code,
                name=payload.name,
                description=payload.description,
                cost_account_id=payload.cost_account_id,
                accumulated_depreciation_account_id=payload.accumulated_depreciation_account_id,
                depreciation_expense_account_id=payload.depreciation_expense_account_id,
                gain_loss_account_id=payload.gain_loss_account_id,
            ),
            on_success=lambda _category: self._action_done("Asset category created"),
            on_failure=self._action_failed,
        )

    def _edit_category(self) -> None:
        category = self._selected_category()
        if category is None:
            return
        dialog = AssetCategoryDialog(list(self._accounts.values()), category, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.runner.submit(
            lambda: self.session.client.update_asset_category(
                str(category.id),
                version=category.version,
                name=payload.name,
                description=payload.description,
                cost_account_id=payload.cost_account_id,
                accumulated_depreciation_account_id=payload.accumulated_depreciation_account_id,
                depreciation_expense_account_id=payload.depreciation_expense_account_id,
                gain_loss_account_id=payload.gain_loss_account_id,
            ),
            on_success=lambda _category: self._action_done("Asset category updated"),
            on_failure=self._action_failed,
        )

    def _toggle_category(self) -> None:
        category = self._selected_category()
        if category is None:
            return
        if category.is_active:
            answer = QMessageBox.question(
                self,
                "Archive asset category",
                f"Archive {category.name}? New assets will no longer use it.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        payload = CategoryInput(
            code=category.code,
            name=category.name,
            description=category.description,
            cost_account_id=str(category.cost_account_id),
            accumulated_depreciation_account_id=str(category.accumulated_depreciation_account_id),
            depreciation_expense_account_id=str(category.depreciation_expense_account_id),
            gain_loss_account_id=str(category.gain_loss_account_id),
        )
        self.runner.submit(
            lambda: self.session.client.update_asset_category(
                str(category.id),
                version=category.version,
                name=payload.name,
                description=payload.description,
                cost_account_id=payload.cost_account_id,
                accumulated_depreciation_account_id=payload.accumulated_depreciation_account_id,
                depreciation_expense_account_id=payload.depreciation_expense_account_id,
                gain_loss_account_id=payload.gain_loss_account_id,
                is_active=not category.is_active,
            ),
            on_success=lambda _category: self._action_done("Asset category updated"),
            on_failure=self._action_failed,
        )

    def _new_asset(self) -> None:
        dialog = AssetDialog(
            list(self._categories.values()),
            self.session.company.base_currency,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.runner.submit(
            lambda: self.session.client.create_asset(
                company_id=self.session.selected_company_id,
                category_id=payload.category_id,
                asset_number=payload.asset_number,
                name=payload.name,
                description=payload.description,
                serial_reference=payload.serial_reference,
                acquisition_date=payload.acquisition_date,
                in_service_date=payload.in_service_date,
                cost=payload.cost,
                residual_value=payload.residual_value,
                useful_life_months=payload.useful_life_months,
            ),
            on_success=lambda _asset: self._action_done("Asset saved as draft"),
            on_failure=self._action_failed,
        )

    def _edit_asset(self) -> None:
        asset = self._selected_asset()
        if asset is None:
            return
        dialog = AssetDialog(
            list(self._categories.values()),
            asset.currency,
            asset,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.runner.submit(
            lambda: self.session.client.update_asset(
                str(asset.id),
                version=asset.version,
                name=payload.name,
                description=payload.description,
                serial_reference=payload.serial_reference,
                acquisition_date=payload.acquisition_date,
                in_service_date=payload.in_service_date,
                cost=payload.cost,
                residual_value=payload.residual_value,
                useful_life_months=payload.useful_life_months,
            ),
            on_success=lambda _asset: self._action_done("Asset draft updated"),
            on_failure=self._action_failed,
        )

    def _submit_asset(self) -> None:
        asset = self._selected_asset()
        if asset is None:
            return
        self.runner.submit(
            lambda: self.session.client.submit_asset(str(asset.id), version=asset.version),
            on_success=lambda _asset: self._action_done("Asset submitted for approval"),
            on_failure=self._action_failed,
        )

    def _approve_asset(self) -> None:
        asset = self._selected_asset()
        if asset is None:
            return
        dialog = CapitalizationDialog(list(self._accounts.values()), asset, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.runner.submit(
            lambda: self.session.client.approve_asset(
                str(asset.id),
                version=asset.version,
                funding_account_id=payload.funding_account_id,
                reference=payload.reference,
                note=payload.note,
            ),
            on_success=lambda _asset: self._action_done("Asset approved and capitalized"),
            on_failure=self._action_failed,
        )

    def _reject_asset(self) -> None:
        asset = self._selected_asset()
        if asset is None:
            return
        note, accepted = QInputDialog.getText(self, "Reject fixed asset", "Reason")
        if not accepted or not note.strip():
            return
        self.runner.submit(
            lambda: self.session.client.reject_asset(
                str(asset.id), version=asset.version, note=note.strip()
            ),
            on_success=lambda _asset: self._action_done("Asset rejected"),
            on_failure=self._action_failed,
        )

    def _cancel_asset(self) -> None:
        asset = self._selected_asset()
        if asset is None:
            return
        note, accepted = QInputDialog.getText(self, "Cancel fixed asset", "Note (optional)")
        if not accepted:
            return
        self.runner.submit(
            lambda: self.session.client.cancel_asset(
                str(asset.id), version=asset.version, note=note.strip() or None
            ),
            on_success=lambda _asset: self._action_done("Asset cancelled"),
            on_failure=self._action_failed,
        )

    def _depreciate_asset(self) -> None:
        asset = self._selected_asset()
        if asset is None:
            return
        dialog = DepreciationDialog(asset, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.runner.submit(
            lambda: self.session.client.depreciate_asset(
                str(asset.id),
                period_start=payload.period_start,
                period_end=payload.period_end,
                reference=payload.reference,
            ),
            on_success=lambda _row: self._action_done("Depreciation posted"),
            on_failure=self._action_failed,
        )

    def _dispose_asset(self) -> None:
        asset = self._selected_asset()
        if asset is None:
            return
        dialog = DisposalDialog(list(self._accounts.values()), asset, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.runner.submit(
            lambda: self.session.client.dispose_asset(
                str(asset.id),
                version=asset.version,
                disposal_date=payload.disposal_date,
                proceeds_amount=payload.proceeds_amount,
                proceeds_account_id=payload.proceeds_account_id,
                reason=payload.reason,
            ),
            on_success=lambda _disposal: self._action_done("Disposal requested"),
            on_failure=self._action_failed,
        )

    def _approve_disposal(self) -> None:
        asset = self._selected_asset()
        if asset is None:
            return
        note, accepted = QInputDialog.getText(self, "Approve asset disposal", "Note (optional)")
        if not accepted:
            return
        self.runner.submit(
            lambda: self.session.client.asset_disposal_action(
                str(asset.id),
                "approve",
                version=asset.version,
                note=note.strip() or None,
            ),
            on_success=lambda _disposal: self._action_done("Disposal approved"),
            on_failure=self._action_failed,
        )

    def _reject_disposal(self) -> None:
        asset = self._selected_asset()
        if asset is None:
            return
        note, accepted = QInputDialog.getText(self, "Reject asset disposal", "Reason")
        if not accepted or not note.strip():
            return
        self.runner.submit(
            lambda: self.session.client.asset_disposal_action(
                str(asset.id),
                "reject",
                version=asset.version,
                note=note.strip(),
            ),
            on_success=lambda _disposal: self._action_done("Disposal rejected"),
            on_failure=self._action_failed,
        )

    def _action_done(self, message: str) -> None:
        self.set_status(message)
        self.refresh()

    def _filter_assets(self) -> None:
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
