"""Base class for authenticated workspace pages."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QLabel, QWidget

from nexus_desktop.state import ApplicationSession
from nexus_desktop.tasks import TaskRunner


class BasePage(QWidget):
    status_changed = pyqtSignal(str)
    navigate_requested = pyqtSignal(str)
    authentication_expired = pyqtSignal()

    def __init__(self, session: ApplicationSession) -> None:
        super().__init__()
        self.session = session
        self.runner = TaskRunner(self)
        self.setObjectName("Page")

    def refresh(self) -> None:
        """Load or reload the page. Pages override this method."""

    def set_status(self, message: str) -> None:
        self.status_changed.emit(message)

    def show_error(self, label: QLabel, message: str) -> None:
        label.setText(message)
        label.show()

    def clear_error(self, label: QLabel) -> None:
        label.clear()
        label.hide()
