"""Secure refresh-token persistence through the operating-system keychain."""

from __future__ import annotations

from typing import Protocol

import keyring
from keyring.errors import KeyringError

SERVICE_NAME = "nexus-enterprise-desktop"


class SessionVault(Protocol):
    def save(
        self,
        *,
        api_url: str,
        organization_slug: str,
        email: str,
        refresh_token: str,
    ) -> bool: ...

    def load(self, *, api_url: str, organization_slug: str, email: str) -> str | None: ...

    def clear(self, *, api_url: str, organization_slug: str, email: str) -> None: ...


def _account_key(api_url: str, organization_slug: str, email: str) -> str:
    return f"{api_url.rstrip('/')}|{organization_slug.lower()}|{email.lower()}"


class KeyringSessionVault:
    """Store only the rotating refresh token; access tokens stay in memory."""

    def save(
        self,
        *,
        api_url: str,
        organization_slug: str,
        email: str,
        refresh_token: str,
    ) -> bool:
        try:
            keyring.set_password(
                SERVICE_NAME,
                _account_key(api_url, organization_slug, email),
                refresh_token,
            )
        except KeyringError:
            return False
        return True

    def load(self, *, api_url: str, organization_slug: str, email: str) -> str | None:
        try:
            value = keyring.get_password(
                SERVICE_NAME, _account_key(api_url, organization_slug, email)
            )
        except KeyringError:
            return None
        return value

    def clear(self, *, api_url: str, organization_slug: str, email: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, _account_key(api_url, organization_slug, email))
        except KeyringError:
            return


class InMemorySessionVault:
    """Deterministic vault used by tests and previews."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}

    def save(
        self,
        *,
        api_url: str,
        organization_slug: str,
        email: str,
        refresh_token: str,
    ) -> bool:
        self._tokens[_account_key(api_url, organization_slug, email)] = refresh_token
        return True

    def load(self, *, api_url: str, organization_slug: str, email: str) -> str | None:
        return self._tokens.get(_account_key(api_url, organization_slug, email))

    def clear(self, *, api_url: str, organization_slug: str, email: str) -> None:
        self._tokens.pop(_account_key(api_url, organization_slug, email), None)
