"""Nexus sign-in surface and saved-session restoration."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nexus_desktop.api.client import ApiError, NetworkError, NexusClient
from nexus_desktop.api.models import MeResponse, TokenPair
from nexus_desktop.config import DEFAULT_API_URL, DesktopPreferences
from nexus_desktop.session import SessionVault
from nexus_desktop.state import ApplicationSession
from nexus_desktop.tasks import TaskRunner

logger = logging.getLogger(__name__)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginWindow(QWidget):
    authenticated = pyqtSignal(object)

    def __init__(
        self,
        *,
        preferences: DesktopPreferences,
        vault: SessionVault,
    ) -> None:
        super().__init__()
        self._preferences = preferences
        self._vault = vault
        self._runner = TaskRunner(self)
        self._client: NexusClient | None = None
        self._remember = True
        self.setObjectName("LoginPage")
        self.setWindowTitle("Nexus Enterprise — Sign in")
        self.resize(1040, 680)
        self.setMinimumSize(900, 620)
        self._build()
        QTimer.singleShot(250, self._restore_saved_session)

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        brand = QFrame()
        brand.setObjectName("BrandPanel")
        brand.setFixedWidth(390)
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(40, 36, 40, 36)
        brand_layout.setSpacing(0)

        identity = QHBoxLayout()
        identity.setSpacing(10)
        mark = QLabel("N")
        mark.setProperty("role", "brand-mark")
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_stack = QVBoxLayout()
        name_stack.setSpacing(0)
        brand_name = QLabel("NEXUS")
        brand_name.setProperty("role", "brand")
        brand_caption = QLabel("Enterprise operations")
        brand_caption.setProperty("role", "brand-caption")
        name_stack.addWidget(brand_name)
        name_stack.addWidget(brand_caption)
        identity.addWidget(mark)
        identity.addLayout(name_stack)
        identity.addStretch(1)
        brand_layout.addLayout(identity)
        brand_layout.addStretch(2)

        rule = QFrame()
        rule.setProperty("accent-rule", "true")
        rule.setFixedWidth(44)
        brand_layout.addWidget(rule)
        brand_layout.addSpacing(20)
        statement = QLabel("Every shilling\naccounted for.")
        statement.setProperty("role", "login-ledger")
        statement.setWordWrap(True)
        brand_layout.addWidget(statement)
        brand_layout.addSpacing(12)
        quote = QLabel(
            "Payroll, people and the books in one controlled system — "
            "built for Kenyan statutory rules and multi-company operations."
        )
        quote.setProperty("role", "login-quote")
        quote.setWordWrap(True)
        quote.setMaximumWidth(290)
        brand_layout.addWidget(quote)
        brand_layout.addSpacing(30)

        for text in (
            "Versioned, source-verified statutory rules",
            "Preparer and approver separation of duties",
            "Immutable, append-only audit history",
        ):
            row = QHBoxLayout()
            row.setSpacing(9)
            dot = QLabel("•")
            dot.setStyleSheet("color: #D69A62; font-size: 17px;")
            label = QLabel(text)
            label.setProperty("role", "login-proof")
            label.setWordWrap(True)
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(label, 1)
            brand_layout.addLayout(row)
        brand_layout.addStretch(3)
        version = QLabel("NEXUS ENTERPRISE  ·  KENYA FIRST")
        version.setProperty("role", "brand-caption")
        brand_layout.addWidget(version)
        root.addWidget(brand)

        pane = QWidget()
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(64, 44, 64, 36)
        pane_layout.setSpacing(0)
        pane_layout.addStretch(1)

        heading = QLabel("Sign in to your workspace")
        heading.setProperty("role", "login-title")
        pane_layout.addWidget(heading)
        pane_layout.addSpacing(8)
        description = QLabel("Use the credentials issued by your organisation administrator.")
        description.setProperty("role", "subtitle")
        description.setWordWrap(True)
        pane_layout.addWidget(description)
        pane_layout.addSpacing(28)

        self.organization_input = QLineEdit(self._preferences.last_organization)
        self.organization_input.setPlaceholderText("organisation-slug")
        self.organization_input.setClearButtonEnabled(True)
        self.organization_input.setObjectName("organizationInput")
        pane_layout.addWidget(self._field_label("Organisation"))
        pane_layout.addWidget(self.organization_input)
        pane_layout.addSpacing(16)

        self.email_input = QLineEdit(self._preferences.last_email)
        self.email_input.setPlaceholderText("you@company.co.ke")
        self.email_input.setClearButtonEnabled(True)
        self.email_input.setObjectName("emailInput")
        pane_layout.addWidget(self._field_label("Work email"))
        pane_layout.addWidget(self.email_input)
        pane_layout.addSpacing(16)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Your password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setObjectName("passwordInput")
        pane_layout.addWidget(self._field_label("Password"))
        pane_layout.addWidget(self.password_input)
        pane_layout.addSpacing(14)

        self.remember_checkbox = QCheckBox("Keep me signed in on this device")
        self.remember_checkbox.setChecked(True)
        self.remember_checkbox.setToolTip(
            "Stores a rotating refresh token in the operating-system keychain. "
            "Access tokens are never written to disk."
        )
        pane_layout.addWidget(self.remember_checkbox)
        pane_layout.addSpacing(18)

        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        pane_layout.addWidget(self.error_label)
        pane_layout.addSpacing(10)

        self.status_label = QLabel()
        self.status_label.setProperty("role", "muted")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        pane_layout.addWidget(self.status_label)

        self.sign_in_button = QPushButton("Sign in")
        self.sign_in_button.setProperty("role", "primary")
        self.sign_in_button.setMinimumHeight(42)
        self.sign_in_button.setObjectName("signInButton")
        self.sign_in_button.clicked.connect(self._submit)
        self.password_input.returnPressed.connect(self._submit)
        self.email_input.returnPressed.connect(self._submit)
        self.organization_input.returnPressed.connect(self._submit)
        pane_layout.addWidget(self.sign_in_button)
        pane_layout.addSpacing(26)

        pane_layout.addWidget(self._divider())
        pane_layout.addSpacing(18)
        api_label = QLabel("Nexus API address")
        api_label.setProperty("role", "muted")
        self.api_input = QLineEdit(self._preferences.api_url)
        self.api_input.setPlaceholderText(DEFAULT_API_URL)
        self.api_input.setObjectName("apiInput")
        pane_layout.addWidget(api_label)
        pane_layout.addSpacing(5)
        pane_layout.addWidget(self.api_input)
        pane_layout.addStretch(2)

        footer = QLabel("Protected by organisation-scoped access control.")
        footer.setProperty("role", "muted")
        pane_layout.addWidget(footer)
        root.addWidget(pane, 1)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "muted")
        return label

    @staticmethod
    def _divider() -> QFrame:
        divider = QFrame()
        divider.setProperty("divider", "true")
        return divider

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.sign_in_button.setEnabled(not busy)
        self.sign_in_button.setText("Signing in…" if busy else "Sign in")
        for field in (
            self.organization_input,
            self.email_input,
            self.password_input,
            self.api_input,
            self.remember_checkbox,
        ):
            field.setEnabled(not busy)
        if message:
            self.status_label.setText(message)
            self.status_label.show()
        else:
            self.status_label.hide()

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()
        self._set_busy(False)
        self.password_input.selectAll()
        self.password_input.setFocus()

    def _validate(self) -> tuple[str, str, str, str] | None:
        organization = self.organization_input.text().strip().lower()
        email = self.email_input.text().strip().lower()
        password = self.password_input.text()
        api_url = self.api_input.text().strip().rstrip("/")
        if not organization:
            self._show_error("Enter your organisation slug.")
            return None
        if not EMAIL_PATTERN.fullmatch(email):
            self._show_error("Enter a valid work email address.")
            return None
        if not password:
            self._show_error("Enter your password.")
            return None
        parsed = urlparse(api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            self._show_error("The API address must be a complete http:// or https:// URL.")
            return None
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            self._show_error("Use HTTPS for a Nexus API served on a remote host.")
            return None
        self.error_label.hide()
        return api_url, organization, email, password

    def _submit(self) -> None:
        if not self.sign_in_button.isEnabled():
            return
        values = self._validate()
        if values is None:
            return
        api_url, organization, email, password = values
        self._remember = self.remember_checkbox.isChecked()
        self._set_busy(True, "Authenticating with the Nexus API…")
        self._preferences.api_url = api_url
        self._preferences.last_organization = organization
        self._preferences.last_email = email
        self._preferences.sync()

        client = self._create_client(api_url, organization, email)
        self._client = client

        def authenticate() -> tuple[TokenPair, MeResponse]:
            tokens = client.login(organization, email, password)
            return tokens, client.me()

        self._runner.submit(
            authenticate,
            on_success=lambda result: self._finish_authentication(
                result, api_url, organization, email
            ),
            on_failure=self._handle_failure,
        )

    def _create_client(self, api_url: str, organization: str, email: str) -> NexusClient:
        if self._client is not None:
            self._client.close()

        def persist_tokens(tokens: TokenPair | None) -> None:
            if tokens is None:
                return
            self._vault.save(
                api_url=api_url,
                organization_slug=organization,
                email=email,
                refresh_token=tokens.refresh_token,
            )

        return NexusClient(
            api_url,
            on_tokens_changed=persist_tokens if self._remember else None,
        )

    def _finish_authentication(
        self,
        result: tuple[TokenPair, MeResponse],
        api_url: str,
        organization: str,
        email: str,
    ) -> None:
        tokens, me = result
        if self._client is None:
            self._show_error("The authentication session was lost. Try again.")
            return
        if not me.companies:
            self._show_error(
                "This account has no accessible company. Ask your administrator to assign one."
            )
            return
        if self._remember:
            self._vault.save(
                api_url=api_url,
                organization_slug=organization,
                email=email,
                refresh_token=tokens.refresh_token,
            )
        else:
            self._vault.clear(
                api_url=api_url,
                organization_slug=organization,
                email=email,
            )
        session = ApplicationSession(
            client=self._client,
            me=me,
            api_url=api_url,
            organization_slug=organization,
            email=email,
            selected_company_id=str(me.companies[0].id),
        )
        self._set_busy(False)
        self.authenticated.emit(session)

    def _handle_failure(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self._show_error(exception.message)
        else:
            logger.exception("Unexpected sign-in failure", exc_info=exception)
            self._show_error("Sign-in could not be completed. Try again.")

    def _restore_saved_session(self) -> None:
        api_url = self._preferences.api_url
        organization = self._preferences.last_organization
        email = self._preferences.last_email
        if not api_url or not organization or not email:
            return
        refresh_token = self._vault.load(
            api_url=api_url,
            organization_slug=organization,
            email=email,
        )
        if refresh_token is None:
            return
        self.organization_input.setText(organization)
        self.email_input.setText(email)
        self.api_input.setText(api_url)
        self._remember = True
        self.remember_checkbox.setChecked(True)
        self._set_busy(True, "Restoring your last secured session…")
        client = self._create_client(api_url, organization, email)
        self._client = client

        def restore() -> tuple[TokenPair, MeResponse]:
            tokens = client.restore(refresh_token)
            return tokens, client.me()

        self._runner.submit(
            restore,
            on_success=lambda result: self._finish_authentication(
                result, api_url, organization, email
            ),
            on_failure=self._restore_failed,
        )

    def _restore_failed(self, exception: Exception) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._set_busy(False)
        if isinstance(exception, ApiError) and exception.status_code == 401:
            self.status_label.setText("Your saved session expired. Sign in again.")
            self.status_label.show()
            self.password_input.setFocus()
            return
        if isinstance(exception, NetworkError):
            self.status_label.setText(
                "The saved session could not be checked because the API is unreachable."
            )
            self.status_label.show()
            return
        logger.debug("Saved session restore failed", exc_info=exception)
