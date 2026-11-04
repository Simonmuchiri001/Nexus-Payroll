"""Desktop configuration and persisted, non-secret preferences."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from PyQt6.QtCore import QSettings

DEFAULT_API_URL = "http://127.0.0.1:8000/api/v1"
ORGANIZATION_NAME = "Nexus Enterprise"
APPLICATION_NAME = "Nexus Desktop"


def initial_api_url() -> str:
    return os.environ.get("NEXUS_API_URL", DEFAULT_API_URL).rstrip("/")


@dataclass(slots=True)
class DesktopPreferences:
    """Non-sensitive preferences only. Refresh tokens live in the OS keychain."""

    _settings: QSettings

    @classmethod
    def create(cls) -> DesktopPreferences:
        return cls(QSettings(ORGANIZATION_NAME, APPLICATION_NAME))

    @property
    def api_url(self) -> str:
        value = self._settings.value("api_url", initial_api_url())
        return str(value).rstrip("/") or DEFAULT_API_URL

    @api_url.setter
    def api_url(self, value: str) -> None:
        self._settings.setValue("api_url", value.rstrip("/"))

    @property
    def theme(self) -> Literal["light", "dark"]:
        return "dark" if self._settings.value("theme", "light") == "dark" else "light"

    @theme.setter
    def theme(self, value: Literal["light", "dark"]) -> None:
        self._settings.setValue("theme", value)

    @property
    def last_organization(self) -> str:
        return str(self._settings.value("last_organization", ""))

    @last_organization.setter
    def last_organization(self, value: str) -> None:
        self._settings.setValue("last_organization", value)

    @property
    def last_email(self) -> str:
        return str(self._settings.value("last_email", ""))

    @last_email.setter
    def last_email(self, value: str) -> None:
        self._settings.setValue("last_email", value)

    def sync(self) -> None:
        self._settings.sync()
