"""Shared UI primitives used across the desktop workspace."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

TONES = {
    "draft": "neutral",
    "open": "neutral",
    "pending": "warning",
    "calculated": "accent",
    "approved": "warning",
    "disbursed": "accent",
    "archived": "neutral",
    "ended": "neutral",
    "scheduled": "neutral",
    "reserved": "warning",
    "deducted": "success",
    "rejected": "danger",
    "cancelled": "neutral",
    "finalized": "success",
    "posted": "success",
    "active": "success",
    "probation": "accent",
    "suspended": "danger",
    "terminated": "neutral",
    "closed": "neutral",
    "present": "success",
    "remote": "accent",
    "leave": "accent",
    "absent": "danger",
    "holiday": "neutral",
}


def money(amount: Decimal, currency: str = "KES", *, compact: bool = False) -> str:
    if compact and abs(amount) >= Decimal("1000000"):
        return f"{currency} {amount / Decimal('1000000'):,.1f}M"
    return f"{currency} {amount:,.2f}"


def date_label(value: date | datetime | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %H:%M")
    return value.strftime("%d %b %Y")


def refresh_style(widget: QWidget) -> None:
    """Re-evaluate dynamic Qt properties after changing a role or tone."""

    style = widget.style()
    if style is None:
        return
    style.unpolish(widget)
    style.polish(widget)


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        child = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif child is not None:
            clear_layout(child)


class PageHeader(QWidget):
    def __init__(
        self,
        title: str,
        subtitle: str,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.title = QLabel(title)
        self.title.setProperty("role", "title")
        self.subtitle = QLabel(subtitle)
        self.subtitle.setProperty("role", "subtitle")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, *, raised: bool = False) -> None:
        super().__init__(parent)
        self.setProperty("card", "raised" if raised else "true")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)


class MetricCard(Card):
    def __init__(self, label: str, value: str = "—", hint: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)
        self.label = QLabel(label)
        self.label.setProperty("role", "metric-label")
        self.value = QLabel(value)
        self.value.setProperty("role", "metric")
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hint = QLabel(hint)
        self.hint.setProperty("role", "muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)

    def update_metric(self, value: str, hint: str = "") -> None:
        self.value.setText(value)
        self.hint.setText(hint)


class StatusBadge(QLabel):
    def __init__(self, status: str) -> None:
        super().__init__()
        self.setProperty("role", "badge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_status(status)

    def set_status(self, status: str) -> None:
        self.setText(status.replace("_", " ").capitalize())
        self.setProperty("tone", TONES.get(status.lower(), "neutral"))
        refresh_style(self)


def status_widget(status: str) -> QWidget:
    wrapper = QWidget()
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(StatusBadge(status))
    layout.addStretch(1)
    return wrapper


def configure_table(
    table: QTableWidget,
    headers: list[str],
    *,
    stretch_column: int | None = None,
) -> None:
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    vertical = table.verticalHeader()
    if vertical is not None:
        vertical.setVisible(False)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setWordWrap(False)
    table.setSortingEnabled(False)
    header = table.horizontalHeader()
    if header is None:
        raise RuntimeError("The table has no horizontal header.")
    header.setHighlightSections(False)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    for index in range(len(headers)):
        if stretch_column == index:
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
        else:
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)


def text_item(value: str, *, numeric: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(value)
    if numeric:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    else:
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return item


class EmptyState(QWidget):
    def __init__(self, title: str, detail: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 38, 20, 38)
        layout.setSpacing(6)
        heading = QLabel(title)
        heading.setProperty("role", "section-title")
        body = QLabel(detail)
        body.setProperty("role", "muted")
        body.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(body)
