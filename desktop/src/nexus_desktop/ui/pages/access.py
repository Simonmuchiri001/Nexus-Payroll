"""Organisation access: memberships, role grants and account status."""

from __future__ import annotations

import re
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from nexus_desktop.api.client import ApiError, NetworkError
from nexus_desktop.api.models import (
    Membership,
    MembershipPage,
    Role,
    RoleAssignment,
)
from nexus_desktop.state import ApplicationSession
from nexus_desktop.tasks import TaskRunner
from nexus_desktop.ui.common import (
    PageHeader,
    StatusBadge,
    configure_table,
    date_label,
    refresh_style,
    status_widget,
    text_item,
)
from nexus_desktop.ui.pages.base import BasePage

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class AccessData:
    memberships: MembershipPage
    roles: list[Role]


def _scope_label(assignment: RoleAssignment, company_names: dict[str, str]) -> str:
    if assignment.company_id is None:
        return "Organisation-wide"
    return company_names.get(str(assignment.company_id), "Assigned company")


def _role_summary(membership: Membership, company_names: dict[str, str]) -> str:
    if not membership.role_assignments:
        return "No role assigned"
    names = [assignment.role_name for assignment in membership.role_assignments]
    if len(names) <= 2:
        return " · ".join(names)
    return f"{names[0]} · {names[1]} +{len(names) - 2}"


class InviteUserDialog(QDialog):
    """Collect the identity, initial role grant and default company."""

    def __init__(
        self,
        session: ApplicationSession,
        roles: list[Role],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self._roles = roles
        self.setWindowTitle("Invite organisation user")
        self.setModal(True)
        self.setMinimumWidth(500)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        heading = QLabel("New organisation user")
        heading.setProperty("role", "section-title")
        root.addWidget(heading)
        note = QLabel(
            "The person receives an organisation membership immediately. "
            "Share the temporary password through a secure channel."
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.full_name = QLineEdit()
        self.full_name.setPlaceholderText("Full name")
        self.email = QLineEdit()
        self.email.setPlaceholderText("name@company.co.ke")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("At least 12 characters")
        self.confirm_password = QLineEdit()
        self.confirm_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password.setPlaceholderText("Repeat password")
        self.role = QComboBox()
        for role in self._roles:
            self.role.addItem(role.name, role.code)
        self.scope = QComboBox()
        self.scope.addItem("Organisation-wide", "")
        for company in self.session.me.companies:
            self.scope.addItem(company.name, str(company.id))
        default_index = self.scope.findData(self.session.selected_company_id)
        self.scope.setCurrentIndex(default_index if default_index >= 0 else 0)
        form.addRow("Full name", self.full_name)
        form.addRow("Work email", self.email)
        form.addRow("Temporary password", self.password)
        form.addRow("Confirm password", self.confirm_password)
        form.addRow("Initial role", self.role)
        form.addRow("Role scope", self.scope)
        root.addLayout(form)

        guidance = QLabel(
            "A password must use at least three of: lowercase, uppercase, digits and symbols."
        )
        guidance.setProperty("role", "muted")
        guidance.setWordWrap(True)
        root.addWidget(guidance)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_button is not None:
            save_button.setText("Create user")
            save_button.setProperty("role", "primary")
            save_button.setEnabled(bool(self._roles))
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        if not self.full_name.text().strip():
            QMessageBox.warning(self, "Invite user", "Enter the user's full name.")
            return
        email = self.email.text().strip().lower()
        if not EMAIL_PATTERN.fullmatch(email):
            QMessageBox.warning(self, "Invite user", "Enter a valid work email address.")
            return
        password = self.password.text()
        if len(password) < 12:
            QMessageBox.warning(
                self, "Invite user", "The password must contain at least 12 characters."
            )
            return
        if password != self.confirm_password.text():
            QMessageBox.warning(self, "Invite user", "The passwords do not match.")
            return
        if self.role.currentData() is None:
            QMessageBox.warning(self, "Invite user", "Select an initial role.")
            return
        self.accept()

    def payload(self) -> dict[str, object]:
        company_id = str(self.scope.currentData()) or None
        return {
            "email": self.email.text().strip().lower(),
            "full_name": self.full_name.text().strip(),
            "password": self.password.text(),
            "default_company_id": self.session.selected_company_id,
            "role_assignments": [
                {
                    "role_code": str(self.role.currentData()),
                    "company_id": company_id,
                }
            ],
        }


class ManageUserDialog(QDialog):
    """Review role grants and account state for one organisation member."""

    membership_changed = pyqtSignal(object)

    def __init__(
        self,
        session: ApplicationSession,
        membership: Membership,
        roles: list[Role],
        company_names: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self._membership = membership
        self._roles = roles
        self._company_names = company_names
        self._runner = TaskRunner(self)
        self.setWindowTitle("Manage access")
        self.setModal(True)
        self.setMinimumWidth(610)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        head = QHBoxLayout()
        identity = QVBoxLayout()
        identity.setSpacing(2)
        name = QLabel(self._membership.full_name)
        name.setProperty("role", "section-title")
        email = QLabel(self._membership.email)
        email.setProperty("role", "muted")
        identity.addWidget(name)
        identity.addWidget(email)
        head.addLayout(identity, 1)
        self.status_badge = StatusBadge(self._membership.status)
        head.addWidget(self.status_badge)
        root.addLayout(head)

        meta = QLabel(f"Membership created {date_label(self._membership.created_at)}")
        meta.setProperty("role", "muted")
        root.addWidget(meta)

        roles_label = QLabel("Role assignments")
        roles_label.setProperty("role", "section-title")
        root.addWidget(roles_label)
        self.roles_table = QTableWidget()
        configure_table(self.roles_table, ["Role", "Scope", ""], stretch_column=0)
        self.roles_table.setMinimumHeight(180)
        root.addWidget(self.roles_table)

        add_row = QHBoxLayout()
        self.role_combo = QComboBox()
        for role in self._roles:
            self.role_combo.addItem(role.name, role.code)
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Organisation-wide", "")
        for company in self.session.me.companies:
            self.scope_combo.addItem(company.name, str(company.id))
        self.add_role_button = QPushButton("Add role")
        self.add_role_button.clicked.connect(self._add_role)
        add_row.addWidget(self.role_combo, 2)
        add_row.addWidget(self.scope_combo, 2)
        add_row.addWidget(self.add_role_button)
        root.addLayout(add_row)

        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        footer = QHBoxLayout()
        self.suspend_button = QPushButton()
        self.suspend_button.clicked.connect(self._toggle_status)
        footer.addWidget(self.suspend_button)
        footer.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        root.addLayout(footer)

        self._render()
        self._apply_permissions()

    def _apply_permissions(self) -> None:
        manage = self.session.can("user.manage")
        self.add_role_button.setVisible(manage)
        self.role_combo.setVisible(manage)
        self.scope_combo.setVisible(manage)
        self.suspend_button.setVisible(manage)
        is_self = str(self._membership.id) == str(self.session.me.membership_id)
        self.suspend_button.setEnabled(
            manage and not is_self and self._membership.status == "active"
        )
        if is_self:
            self.suspend_button.setToolTip("You cannot suspend your own membership.")
        else:
            self.suspend_button.setToolTip("")

    def _render(self) -> None:
        self.status_badge.set_status(self._membership.status)
        self.suspend_button.setText(
            "Reactivate access" if self._membership.status == "suspended" else "Suspend access"
        )
        self.suspend_button.setProperty(
            "role", "primary" if self._membership.status == "suspended" else "danger"
        )
        refresh_style(self.suspend_button)

        self.roles_table.setRowCount(0)
        for row_index, assignment in enumerate(self._membership.role_assignments):
            self.roles_table.insertRow(row_index)
            self.roles_table.setItem(row_index, 0, text_item(assignment.role_name))
            self.roles_table.setItem(
                row_index,
                1,
                text_item(_scope_label(assignment, self._company_names)),
            )
            remove = QPushButton("Remove")
            remove.setProperty("role", "ghost")
            remove.setEnabled(self.session.can("user.manage"))

            def remove_assignment(
                _checked: bool = False, assignment_id: str = str(assignment.id)
            ) -> None:
                self._remove_role(assignment_id)

            remove.clicked.connect(remove_assignment)
            self.roles_table.setCellWidget(row_index, 2, remove)
            self.roles_table.setRowHeight(row_index, 36)
        self._apply_permissions()

    def _set_busy(self, busy: bool) -> None:
        self.add_role_button.setEnabled(not busy and self.session.can("user.manage"))
        self.role_combo.setEnabled(not busy)
        self.scope_combo.setEnabled(not busy)
        self.suspend_button.setEnabled(not busy and self.session.can("user.manage"))
        self.roles_table.setEnabled(not busy)
        if not busy:
            self._apply_permissions()

    def _apply_membership(self, membership: Membership) -> None:
        self._membership = membership
        self.membership_changed.emit(membership)
        self.error_label.hide()
        self._render()

    def _failed(self, exception: Exception) -> None:
        self._set_busy(False)
        if isinstance(exception, (ApiError, NetworkError)):
            message = exception.message
        else:
            message = "The access change could not be completed. Try again."
        self.error_label.setText(message)
        self.error_label.show()

    def _toggle_status(self) -> None:
        target = "active" if self._membership.status == "suspended" else "suspended"
        verb = "Reactivate" if target == "active" else "Suspend"
        answer = QMessageBox.question(
            self,
            f"{verb} access",
            f"{verb} {self._membership.full_name}? "
            + (
                "Their refresh tokens will be revoked immediately."
                if target == "suspended"
                else "They will be able to sign in again."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self._runner.submit(
            lambda: self.session.client.set_user_status(str(self._membership.id), target),
            on_success=self._apply_membership,
            on_failure=self._failed,
        )

    def _add_role(self) -> None:
        role_code = self.role_combo.currentData()
        if role_code is None:
            return
        company_id = str(self.scope_combo.currentData()) or None
        self._set_busy(True)
        self._runner.submit(
            lambda: self.session.client.add_role_assignment(
                str(self._membership.id),
                role_code=str(role_code),
                company_id=company_id,
            ),
            on_success=self._apply_membership,
            on_failure=self._failed,
        )

    def _remove_role(self, assignment_id: str) -> None:
        answer = QMessageBox.question(
            self,
            "Remove role",
            "Remove this role assignment? The user's access is recalculated immediately.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self._runner.submit(
            lambda: self.session.client.remove_role_assignment(
                str(self._membership.id), assignment_id
            ),
            on_success=self._apply_membership,
            on_failure=self._failed,
        )


class AccessPage(BasePage):
    def __init__(self, session: ApplicationSession) -> None:
        super().__init__(session)
        self.page = 1
        self.page_size = 25
        self._memberships: dict[str, Membership] = {}
        self._roles: list[Role] = []
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._reset_and_refresh)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 26, 32, 30)
        root.setSpacing(16)

        header_row = QHBoxLayout()
        header_row.addWidget(
            PageHeader(
                "Access",
                "Organisation memberships, scoped role grants and sign-in state.",
            ),
            1,
        )
        self.invite_button = QPushButton("Invite user")
        self.invite_button.setProperty("role", "primary")
        self.invite_button.setEnabled(self.session.can("user.manage"))
        self.invite_button.clicked.connect(self._invite)
        header_row.addWidget(self.invite_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header_row)

        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name or email")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._schedule_search)
        self.status_filter = QComboBox()
        self.status_filter.addItem("All statuses", "")
        self.status_filter.addItem("Active", "active")
        self.status_filter.addItem("Suspended", "suspended")
        self.status_filter.currentIndexChanged.connect(self._reset_and_refresh)
        controls.addWidget(self.search, 1)
        controls.addWidget(self.status_filter)
        root.addLayout(controls)

        self.table = QTableWidget()
        configure_table(
            self.table,
            ["Name", "Email", "Status", "Roles", "Default company"],
            stretch_column=1,
        )
        self.table.setMinimumHeight(360)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(self._open_selected)
        root.addWidget(self.table, 1)

        self.empty_label = QLabel("No organisation users match this view.")
        self.empty_label.setProperty("role", "muted")
        self.empty_label.hide()
        root.addWidget(self.empty_label)

        footer = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "muted")
        self.manage_button = QPushButton("Manage access")
        self.manage_button.setEnabled(False)
        self.manage_button.clicked.connect(self._open_selected)
        self.previous_button = QPushButton("Previous")
        self.previous_button.setProperty("role", "ghost")
        self.next_button = QPushButton("Next")
        self.next_button.setProperty("role", "ghost")
        self.previous_button.clicked.connect(self._previous_page)
        self.next_button.clicked.connect(self._next_page)
        footer.addWidget(self.count_label, 1)
        footer.addWidget(self.manage_button)
        footer.addWidget(self.previous_button)
        footer.addWidget(self.next_button)
        root.addLayout(footer)

    def _schedule_search(self) -> None:
        self._search_timer.start()

    def _reset_and_refresh(self) -> None:
        self.page = 1
        self.refresh()

    def _previous_page(self) -> None:
        if self.page > 1:
            self.page -= 1
            self.refresh()

    def _next_page(self) -> None:
        self.page += 1
        self.refresh()

    def refresh(self) -> None:
        self.clear_error(self.error_label)
        self.set_status("Loading organisation access…")
        page = self.page
        query = self.search.text().strip() or None
        status_value = self.status_filter.currentData()
        status = str(status_value) if status_value else None

        def load() -> AccessData:
            return AccessData(
                memberships=self.session.client.list_users(
                    page=page,
                    page_size=self.page_size,
                    query=query,
                    status=status,
                ),
                roles=self.session.client.list_roles(),
            )

        self.runner.submit(
            load,
            on_success=self._render,
            on_failure=self._failed,
            on_finished=lambda: self.set_status("Access directory ready"),
        )

    def _failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            self.show_error(self.error_label, exception.message)
        else:
            self.show_error(self.error_label, "Organisation access could not be loaded. Try again.")

    def _render(self, data: AccessData) -> None:
        self._roles = data.roles
        self._memberships = {
            str(membership.id): membership for membership in data.memberships.items
        }
        company_names = {str(company.id): company.name for company in self.session.me.companies}

        self.table.setRowCount(0)
        for row_index, membership in enumerate(data.memberships.items):
            self.table.insertRow(row_index)
            name_item = text_item(membership.full_name)
            name_item.setData(Qt.ItemDataRole.UserRole, str(membership.id))
            self.table.setItem(row_index, 0, name_item)
            self.table.setItem(row_index, 1, text_item(membership.email))
            self.table.setCellWidget(row_index, 2, status_widget(membership.status))
            self.table.setItem(row_index, 3, text_item(_role_summary(membership, company_names)))
            default_company = (
                company_names.get(str(membership.default_company_id), "—")
                if membership.default_company_id
                else "—"
            )
            self.table.setItem(row_index, 4, text_item(default_company))
            self.table.setRowHeight(row_index, 44)

        self.empty_label.setVisible(data.memberships.total == 0)
        total = data.memberships.total
        start = (data.memberships.page - 1) * data.memberships.page_size + 1 if total else 0
        end = min(data.memberships.page * data.memberships.page_size, total)
        self.count_label.setText(f"Showing {start}-{end} of {total}" if total else "No users found")
        self.previous_button.setEnabled(data.memberships.page > 1)
        self.next_button.setEnabled(end < total)
        self._selection_changed()

    def _selected_membership(self) -> Membership | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        return self._memberships.get(str(item.data(Qt.ItemDataRole.UserRole) or ""))

    def _selection_changed(self) -> None:
        self.manage_button.setEnabled(self._selected_membership() is not None)

    def _open_selected(self) -> None:
        membership = self._selected_membership()
        if membership is None:
            return
        company_names = {str(company.id): company.name for company in self.session.me.companies}
        dialog = ManageUserDialog(
            self.session,
            membership,
            self._roles,
            company_names,
            self,
        )
        dialog.exec()
        self.refresh()

    def _invite(self) -> None:
        if not self._roles:
            QMessageBox.information(
                self,
                "Invite user",
                "Roles could not be loaded. Reload the page and try again.",
            )
            return
        dialog = InviteUserDialog(self.session, self._roles, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.invite_button.setEnabled(False)
        self.set_status("Creating organisation user…")
        self.runner.submit(
            lambda: self.session.client.create_user(payload),
            on_success=self._created,
            on_failure=self._create_failed,
            on_finished=lambda: self.invite_button.setEnabled(self.session.can("user.manage")),
        )

    def _created(self, _membership: Membership) -> None:
        self.set_status("Organisation user created")
        self.page = 1
        self.refresh()

    def _create_failed(self, exception: Exception) -> None:
        if isinstance(exception, (ApiError, NetworkError)):
            QMessageBox.warning(self, "User not created", exception.message)
        else:
            QMessageBox.warning(
                self,
                "User not created",
                "The organisation user could not be created. Try again.",
            )
