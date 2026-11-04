"""Nexus Enterprise desktop application entry point."""

from __future__ import annotations

import logging
import sys

from PyQt6.QtWidgets import QApplication, QWidget

from nexus_desktop.config import (
    APPLICATION_NAME,
    ORGANIZATION_NAME,
    DesktopPreferences,
)
from nexus_desktop.session import KeyringSessionVault, SessionVault
from nexus_desktop.state import ApplicationSession
from nexus_desktop.ui.login import LoginWindow
from nexus_desktop.ui.main_window import MainWindow
from nexus_desktop.ui.theme import ThemeName, apply_theme

logger = logging.getLogger(__name__)


class NexusDesktopApplication:
    """Own the login-to-workspace transition and a single Qt application."""

    def __init__(
        self,
        application: QApplication,
        *,
        preferences: DesktopPreferences | None = None,
        vault: SessionVault | None = None,
    ) -> None:
        self.application = application
        self.preferences = preferences or DesktopPreferences.create()
        self.vault = vault or KeyringSessionVault()
        self.current_window: QWidget | None = None
        self._theme: ThemeName = self.preferences.theme

    def start(self) -> None:
        apply_theme(self.application, self._theme)
        self._show_login()

    def _show_login(self) -> None:
        login = LoginWindow(preferences=self.preferences, vault=self.vault)
        login.authenticated.connect(self._workspace_ready)
        login.destroyed.connect(self._login_destroyed)
        self.current_window = login
        login.show()
        login.raise_()
        login.activateWindow()

    def _workspace_ready(self, session: object) -> None:
        if not isinstance(session, ApplicationSession):
            logger.error("Login emitted an unexpected session object")
            return
        apply_theme(self.application, self._theme)
        main = MainWindow(session, self.preferences, self.vault)
        main.signed_out.connect(self._signed_out)
        main.theme_changed.connect(self._theme_changed)
        main.destroyed.connect(self._main_destroyed)
        previous = self.current_window
        self.current_window = main
        main.show()
        main.raise_()
        main.activateWindow()
        if previous is not None and previous is not main:
            previous.close()
            previous.deleteLater()

    def _signed_out(self) -> None:
        main = self.current_window
        self._show_login()
        if isinstance(main, MainWindow):
            main.session.client.close()
            main.close()
            main.deleteLater()

    def _theme_changed(self, name: str) -> None:
        theme: ThemeName = "dark" if name == "dark" else "light"
        self._theme = theme
        apply_theme(self.application, theme)

    def _login_destroyed(self) -> None:
        if isinstance(self.current_window, LoginWindow):
            self.current_window = None

    def _main_destroyed(self) -> None:
        if isinstance(self.current_window, MainWindow):
            self.current_window = None


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    application = QApplication(sys.argv)
    application.setApplicationName(APPLICATION_NAME)
    application.setOrganizationName(ORGANIZATION_NAME)
    application.setApplicationDisplayName("Nexus Enterprise")
    controller = NexusDesktopApplication(application)
    application.setProperty("nexusController", controller)
    controller.start()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
