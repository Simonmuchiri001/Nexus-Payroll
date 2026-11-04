"""Nexus visual system for light and dark desktop themes.

The palette is drawn from the subject rather than generic SaaS blue: deep
emerald ink for authority, warm ledger paper for working surfaces, and a
burnished copper rule for focus and the payroll lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

ThemeName = Literal["light", "dark"]


@dataclass(frozen=True, slots=True)
class Theme:
    name: ThemeName
    background: str
    surface: str
    raised: str
    border: str
    ink: str
    muted: str
    primary: str
    primary_hover: str
    primary_text: str
    primary_soft: str
    accent: str
    accent_soft: str
    danger: str
    danger_soft: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    selection: str
    table_alt: str
    sidebar: str
    sidebar_ink: str
    sidebar_muted: str


LIGHT = Theme(
    name="light",
    background="#F2EFE7",
    surface="#FCFBF7",
    raised="#FFFFFF",
    border="#DCD5C7",
    ink="#17251F",
    muted="#66736C",
    primary="#0D4A39",
    primary_hover="#14624C",
    primary_text="#FCFAF3",
    primary_soft="#E2EEE7",
    accent="#B26527",
    accent_soft="#F4E4D3",
    danger="#A23B32",
    danger_soft="#F7E5E2",
    success="#2B7254",
    success_soft="#E4F0E9",
    warning="#8B5A12",
    warning_soft="#F6ECD9",
    selection="#DCE9E2",
    table_alt="#F6F3EC",
    sidebar="#102C23",
    sidebar_ink="#F6F2E9",
    sidebar_muted="#9DB5A9",
)

DARK = Theme(
    name="dark",
    background="#0B1210",
    surface="#111A16",
    raised="#17231D",
    border="#2A3A31",
    ink="#EDE9DF",
    muted="#9AA89F",
    primary="#2E8B69",
    primary_hover="#39A17B",
    primary_text="#F8F5EC",
    primary_soft="#193329",
    accent="#D69A62",
    accent_soft="#352517",
    danger="#DE8177",
    danger_soft="#38201D",
    success="#64BE95",
    success_soft="#173126",
    warning="#D7A75D",
    warning_soft="#332816",
    selection="#203A2E",
    table_alt="#151F1A",
    sidebar="#08100D",
    sidebar_ink="#F1EDE3",
    sidebar_muted="#8FA69A",
)

THEMES: dict[ThemeName, Theme] = {"light": LIGHT, "dark": DARK}
MONO_FAMILIES = ("SF Mono", "Menlo", "Cascadia Mono", "Consolas", "DejaVu Sans Mono")
SANS_FAMILIES = (
    "Avenir Next",
    "Inter",
    "Segoe UI Variable",
    "Segoe UI",
    "SF Pro Text",
    "Helvetica Neue",
)


def _first_available(candidates: tuple[str, ...], fallback: str) -> str:
    available = set(QFontDatabase.families())
    for candidate in candidates:
        if candidate in available:
            return candidate
    return fallback


def apply_theme(application: QApplication, name: ThemeName) -> Theme:
    theme = THEMES[name]
    family = _first_available(SANS_FAMILIES, application.font().family())
    font = QFont(family, 10)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    application.setFont(font)
    application.setStyle("Fusion")
    application.setStyleSheet(_stylesheet(theme))
    return theme


def _stylesheet(t: Theme) -> str:
    return f"""
    * {{
        outline: none;
    }}
    QWidget {{
        color: {t.ink};
        font-size: 13px;
    }}
    QMainWindow, QWidget#AppShell, QWidget#Page, QWidget#LoginPage {{
        background: {t.background};
    }}
    QToolTip {{
        background: {t.raised};
        color: {t.ink};
        border: 1px solid {t.border};
        padding: 6px 8px;
        border-radius: 6px;
    }}
    QFrame#TopBar {{
        background: {t.surface};
        border-bottom: 1px solid {t.border};
    }}
    QFrame#Sidebar {{
        background: {t.sidebar};
        border: none;
    }}
    QLabel {{
        background: transparent;
    }}
    QLabel[role="brand"] {{
        color: {t.sidebar_ink};
        font-size: 18px;
        font-weight: 700;
        letter-spacing: 0.4px;
    }}
    QLabel[role="brand-mark"] {{
        color: {t.sidebar};
        background: {t.accent};
        font-size: 16px;
        font-weight: 800;
        border-radius: 9px;
        padding: 6px;
    }}
    QLabel[role="brand-caption"] {{
        color: {t.sidebar_muted};
        font-size: 11px;
    }}
    QLabel[role="eyebrow"] {{
        color: {t.accent};
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.7px;
    }}
    QLabel[role="title"] {{
        color: {t.ink};
        font-size: 26px;
        font-weight: 700;
    }}
    QLabel[role="login-title"] {{
        color: {t.ink};
        font-size: 24px;
        font-weight: 700;
    }}
    QLabel[role="section-title"] {{
        color: {t.ink};
        font-size: 16px;
        font-weight: 600;
    }}
    QLabel[role="subtitle"] {{
        color: {t.muted};
        font-size: 13px;
    }}
    QLabel[role="muted"] {{
        color: {t.muted};
        font-size: 12px;
    }}
    QLabel[role="metric"] {{
        color: {t.ink};
        font-size: 26px;
        font-weight: 700;
    }}
    QLabel[role="metric-label"] {{
        color: {t.muted};
        font-size: 11px;
        font-weight: 500;
    }}
    QLabel[role="error"] {{
        color: {t.danger};
        background: {t.danger_soft};
        border: 1px solid {t.danger};
        border-radius: 7px;
        padding: 9px 11px;
    }}
    QLabel[role="success"] {{
        color: {t.success};
        background: {t.success_soft};
        border-radius: 7px;
        padding: 8px 10px;
    }}
    QLabel[role="chip"] {{
        color: {t.muted};
        background: {t.background};
        border: 1px solid {t.border};
        border-radius: 9px;
        padding: 2px 8px;
        font-size: 11px;
    }}
    QLabel[role="badge"] {{
        border-radius: 8px;
        padding: 3px 8px;
        font-size: 11px;
        font-weight: 600;
    }}
    QLabel[role="badge"][tone="neutral"] {{
        color: {t.muted};
        background: {t.background};
    }}
    QLabel[role="badge"][tone="success"] {{
        color: {t.success};
        background: {t.success_soft};
    }}
    QLabel[role="badge"][tone="warning"] {{
        color: {t.warning};
        background: {t.warning_soft};
    }}
    QLabel[role="badge"][tone="accent"] {{
        color: {t.accent};
        background: {t.accent_soft};
    }}
    QLabel[role="badge"][tone="danger"] {{
        color: {t.danger};
        background: {t.danger_soft};
    }}
    QFrame#BrandPanel {{
        background: {t.sidebar};
        border: none;
    }}
    QLabel[role="login-ledger"] {{
        color: {t.sidebar_ink};
        font-size: 28px;
        font-weight: 700;
    }}
    QLabel[role="login-quote"] {{
        color: {t.sidebar_muted};
        font-size: 13px;
    }}
    QLabel[role="login-proof"] {{
        color: {t.sidebar_ink};
        font-size: 12px;
        padding: 6px 0;
    }}
    QFrame[card="true"] {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 10px;
    }}
    QFrame[card="raised"] {{
        background: {t.raised};
        border: 1px solid {t.border};
        border-radius: 10px;
    }}
    QFrame[accent-rule="true"] {{
        background: {t.accent};
        border: none;
        min-height: 3px;
        max-height: 3px;
    }}
    QFrame[divider="true"] {{
        background: {t.border};
        border: none;
        min-height: 1px;
        max-height: 1px;
    }}
    QPushButton {{
        background: {t.surface};
        color: {t.ink};
        border: 1px solid {t.border};
        border-radius: 7px;
        padding: 0 15px;
        min-height: 34px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {t.table_alt};
        border-color: {t.muted};
    }}
    QPushButton:pressed {{
        background: {t.selection};
    }}
    QPushButton:disabled {{
        color: {t.muted};
        background: {t.background};
        border-color: {t.border};
    }}
    QPushButton[role="primary"] {{
        background: {t.primary};
        color: {t.primary_text};
        border: 1px solid {t.primary};
    }}
    QPushButton[role="primary"]:hover {{
        background: {t.primary_hover};
        border-color: {t.primary_hover};
    }}
    QPushButton[role="danger"] {{
        background: transparent;
        color: {t.danger};
        border: 1px solid {t.danger};
    }}
    QPushButton[role="danger"]:hover {{
        background: {t.danger_soft};
    }}
    QPushButton[role="ghost"] {{
        background: transparent;
        border: none;
        color: {t.muted};
        font-weight: 500;
        padding: 0 9px;
    }}
    QPushButton[role="ghost"]:hover {{
        color: {t.ink};
        background: {t.table_alt};
    }}
    QPushButton[role="link"] {{
        background: transparent;
        border: none;
        color: {t.accent};
        padding: 0;
        min-height: 20px;
        font-weight: 600;
        text-align: left;
    }}
    QLineEdit, QComboBox, QDateEdit, QSpinBox, QTextEdit {{
        background: {t.raised};
        color: {t.ink};
        border: 1px solid {t.border};
        border-radius: 7px;
        padding: 7px 10px;
        min-height: 24px;
        selection-background-color: {t.primary};
        selection-color: {t.primary_text};
    }}
    QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus, QTextEdit:focus {{
        border: 2px solid {t.accent};
        padding: 6px 9px;
    }}
    QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled {{
        color: {t.muted};
        background: {t.background};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background: {t.raised};
        color: {t.ink};
        border: 1px solid {t.border};
        selection-background-color: {t.selection};
        selection-color: {t.ink};
        padding: 4px;
    }}
    QCheckBox {{
        spacing: 8px;
        color: {t.ink};
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {t.border};
        border-radius: 4px;
        background: {t.raised};
    }}
    QCheckBox::indicator:checked {{
        background: {t.primary};
        border-color: {t.primary};
    }}
    QListWidget#Navigation {{
        background: transparent;
        border: none;
        color: {t.sidebar_muted};
        font-size: 13px;
        font-weight: 500;
        outline: none;
    }}
    QListWidget#Navigation::item {{
        padding: 10px 14px;
        margin: 2px 10px;
        border-radius: 7px;
        border-left: 3px solid transparent;
    }}
    QListWidget#Navigation::item:hover {{
        color: {t.sidebar_ink};
        background: rgba(255, 255, 255, 0.05);
    }}
    QListWidget#Navigation::item:selected {{
        color: {t.sidebar_ink};
        background: rgba(255, 255, 255, 0.09);
        border-left: 3px solid {t.accent};
    }}
    QTableWidget {{
        background: {t.surface};
        alternate-background-color: {t.table_alt};
        color: {t.ink};
        border: 1px solid {t.border};
        border-radius: 8px;
        gridline-color: {t.border};
        selection-background-color: {t.selection};
        selection-color: {t.ink};
    }}
    QTableWidget::item {{
        padding: 8px;
        border-bottom: 1px solid {t.border};
    }}
    QTableWidget::item:selected {{
        background: {t.selection};
        color: {t.ink};
    }}
    QHeaderView::section {{
        background: {t.table_alt};
        color: {t.muted};
        border: none;
        border-bottom: 1px solid {t.border};
        padding: 9px 8px;
        font-size: 11px;
        font-weight: 600;
    }}
    QTableCornerButton::section {{
        background: {t.table_alt};
        border: none;
    }}
    QTabWidget::pane {{
        border: none;
        background: transparent;
        top: -1px;
    }}
    QTabBar::tab {{
        color: {t.muted};
        background: transparent;
        padding: 8px 14px;
        margin-right: 4px;
        border-bottom: 2px solid transparent;
        font-weight: 600;
    }}
    QTabBar::tab:selected {{
        color: {t.ink};
        border-bottom: 2px solid {t.accent};
    }}
    QTabBar::tab:hover {{
        color: {t.ink};
    }}
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {t.border};
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {t.muted};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
    }}
    QScrollBar::handle:horizontal {{
        background: {t.border};
        border-radius: 4px;
        min-width: 30px;
    }}
    QStatusBar {{
        background: {t.surface};
        color: {t.muted};
        border-top: 1px solid {t.border};
    }}
    QDialog {{
        background: {t.background};
    }}
    QMessageBox {{
        background: {t.background};
    }}
    QProgressBar {{
        background: {t.background};
        border: none;
        border-radius: 2px;
        height: 4px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {t.accent};
        border-radius: 2px;
    }}
    QMenu {{
        background: {t.raised};
        border: 1px solid {t.border};
        padding: 5px;
    }}
    QMenu::item {{
        padding: 7px 18px;
        border-radius: 5px;
    }}
    QMenu::item:selected {{
        background: {t.selection};
    }}
    """
