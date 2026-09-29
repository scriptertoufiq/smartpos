"""User accounts and roles. Admin only.

The first screen with real behaviour behind it: every button here calls
AuthService, which enforces the same rules whether the caller is this view or
the command line.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from smartpos.core.constants import Role
from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.models.user import User
from smartpos.models.warehouse import Warehouse
from smartpos.services.auth_service import AuthService
from smartpos.services.warehouse_service import WarehouseService

log = get_logger(__name__)

COLUMNS = ["ID", "Username", "Full name", "Role", "Warehouse", "Status", "Last login"]


class UserEditor(QDialog):
    """Create or edit one account."""

    def __init__(self, parent=None, user: User | None = None,
                 warehouses: list[Warehouse] | None = None) -> None:
        super().__init__(parent)
        self.user = user
        creating = user is None
        self.setWindowTitle("New user" if creating else f"Edit {user.username}")
        self.setMinimumWidth(400)

        form = QFormLayout(self)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(10)

        self.username = QLineEdit(user.username if user else "")
        self.username.setPlaceholderText("no spaces, at least 3 characters")
        form.addRow("Username", self.username)

        self.full_name = QLineEdit(user.full_name if user else "")
        form.addRow("Full name", self.full_name)

        self.role = QComboBox()
        for role in Role:
            self.role.addItem(role.value.capitalize(), role)
        if user:
            self.role.setCurrentIndex(list(Role).index(user.role))
        form.addRow("Role", self.role)

        self.warehouse = QComboBox()
        self.warehouse.addItem("— not posted to an outlet —", None)
        for warehouse in warehouses or []:
            self.warehouse.addItem(f"{warehouse.name} ({warehouse.code})", warehouse.id)
        if user and user.warehouse_id is not None:
            index = self.warehouse.findData(user.warehouse_id)
            if index >= 0:
                self.warehouse.setCurrentIndex(index)
            else:
                # The posting points at a warehouse that is gone or disabled;
                # show it rather than silently clearing it on save.
                self.warehouse.addItem(f"#{user.warehouse_id} (unavailable)",
                                       user.warehouse_id)
                self.warehouse.setCurrentIndex(self.warehouse.count() - 1)
        form.addRow("Warehouse", self.warehouse)

        if creating:
            self.password = QLineEdit()
            self.password.setEchoMode(QLineEdit.Password)
            self.password.setPlaceholderText("at least 6 characters")
            form.addRow("Password", self.password)

            self.confirm = QLineEdit()
            self.confirm.setEchoMode(QLineEdit.Password)
            form.addRow("Repeat it", self.confirm)
        else:
            self.password = None
            self.confirm = None
            note = QLabel("Use “Reset password” on the list to change the password.")
            note.setWordWrap(True)
            note.setObjectName("hintLabel")
            form.addRow("", note)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _validate(self) -> None:
        if self.password is not None and self.password.text() != self.confirm.text():
            QMessageBox.warning(self, "Check the password",
                                "The two passwords do not match.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "username": self.username.text().strip(),
            "full_name": self.full_name.text().strip(),
            "role": self.role.currentData(),
            "warehouse_id": self.warehouse.currentData(),
            "password": self.password.text() if self.password is not None else None,
        }


class UserView(QWidget):
    """User accounts and roles. Admin only."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._warehouses = WarehouseService()
        self._users: list[User] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        heading = QLabel("Users")
        heading.setObjectName("viewHeading")
        layout.addWidget(heading)

        # --- toolbar ---------------------------------------------------
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by username or name…")
        self.search.setMinimumHeight(34)
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search, 1)

        self.role_filter = QComboBox()
        self.role_filter.addItem("All roles", None)
        for role in Role:
            self.role_filter.addItem(role.value.capitalize(), role)
        self.role_filter.setMinimumHeight(34)
        bar.addWidget(self.role_filter)

        self.show_disabled = QCheckBox("Show disabled")
        self.show_disabled.setChecked(True)
        bar.addWidget(self.show_disabled)

        self.add_button = QPushButton("New user")
        self.add_button.setProperty("primary", True)
        self.add_button.setMinimumHeight(34)
        bar.addWidget(self.add_button)
        layout.addLayout(bar)

        # --- table -----------------------------------------------------
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        # --- actions ---------------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.edit_button = QPushButton("Edit")
        self.password_button = QPushButton("Reset password")
        self.toggle_button = QPushButton("Disable")
        self.delete_button = QPushButton("Delete")
        self.delete_button.setProperty("danger", True)
        for b in (self.edit_button, self.password_button,
                  self.toggle_button, self.delete_button):
            b.setMinimumHeight(32)
            b.setEnabled(False)
            actions.addWidget(b)
        actions.addStretch(1)

        self.status = QLabel("")
        self.status.setObjectName("hintLabel")
        actions.addWidget(self.status)
        layout.addLayout(actions)

        # --- wiring ----------------------------------------------------
        self.search.textChanged.connect(self.refresh)
        self.role_filter.currentIndexChanged.connect(self.refresh)
        self.show_disabled.toggled.connect(self.refresh)
        self.add_button.clicked.connect(self._create)
        self.edit_button.clicked.connect(self._edit)
        self.password_button.clicked.connect(self._reset_password)
        self.toggle_button.clicked.connect(self._toggle_active)
        self.delete_button.clicked.connect(self._delete)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(self._edit)

    # ------------------------------------------------------------- helpers

    def _selected(self) -> User | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self._users[rows[0].row()]

    def _selection_changed(self) -> None:
        user = self._selected()
        me = self._auth.current_user()
        chosen = user is not None
        is_self = chosen and me is not None and user.id == me.id

        self.edit_button.setEnabled(chosen)
        self.password_button.setEnabled(chosen)
        self.toggle_button.setEnabled(chosen and not is_self)
        self.delete_button.setEnabled(chosen and not is_self)
        if chosen:
            self.toggle_button.setText("Enable" if not user.is_active else "Disable")
        self.status.setText("This is your own account." if is_self else "")

    def _warehouse_options(self) -> list[Warehouse]:
        """Active outlets only -- nobody should be posted to a closed one."""
        try:
            return self._warehouses.list_all(active_only=True)
        except Exception:
            log.exception("Could not load warehouses for the picker")
            return []

    def _report(self, exc: Exception) -> None:
        if isinstance(exc, SmartPOSError):
            QMessageBox.warning(self, "Cannot do that", str(exc))
        else:
            log.exception("Unexpected error in the user screen")
            QMessageBox.critical(
                self, "Something went wrong",
                "The action failed. The details were written to the log file.")

    # -------------------------------------------------------------- actions

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        try:
            self._users = self._auth.list_users(
                include_inactive=self.show_disabled.isChecked(),
                role=self.role_filter.currentData(),
                search=self.search.text().strip() or None,
            )
        except Exception as exc:
            self._report(exc)
            return

        self.table.setRowCount(len(self._users))
        for row, user in enumerate(self._users):
            last = user.last_login_at.strftime("%d %b %Y, %H:%M") if user.last_login_at else "never"
            cells = [
                str(user.id),
                user.username,
                user.full_name,
                user.role.value.capitalize(),
                self._warehouses.label_for(user.warehouse_id),
                "Active" if user.is_active else "Disabled",
                last,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (0, 4, 5):
                    item.setTextAlignment(Qt.AlignCenter)
                if not user.is_active:
                    item.setForeground(Qt.gray)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._selection_changed()

    def _create(self) -> None:
        dialog = UserEditor(self, warehouses=self._warehouse_options())
        if dialog.exec_() != QDialog.Accepted:
            return
        v = dialog.values()
        try:
            self._auth.create_user(v["username"], v["full_name"], v["password"] or "",
                                   v["role"], v["warehouse_id"])
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()

    def _edit(self) -> None:
        user = self._selected()
        if user is None:
            return
        dialog = UserEditor(self, user, self._warehouse_options())
        if dialog.exec_() != QDialog.Accepted:
            return
        v = dialog.values()
        try:
            self._auth.update_user(user.id, full_name=v["full_name"], role=v["role"],
                                   warehouse_id=v["warehouse_id"], username=v["username"])
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()

    def _reset_password(self) -> None:
        user = self._selected()
        if user is None:
            return
        new, ok = QInputDialog.getText(
            self, "Reset password", f"New password for {user.username}:",
            QLineEdit.Password)
        if not ok:
            return
        try:
            self._auth.reset_password(user.id, new)
        except Exception as exc:
            self._report(exc)
            return
        QMessageBox.information(self, "Password reset",
                                f"{user.username} can now sign in with the new password.")

    def _toggle_active(self) -> None:
        user = self._selected()
        if user is None:
            return
        try:
            if user.is_active:
                self._auth.deactivate_user(user.id)
            else:
                self._auth.activate_user(user.id)
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()

    def _delete(self) -> None:
        user = self._selected()
        if user is None:
            return
        confirm = QMessageBox.question(
            self, "Delete user",
            f"Permanently delete {user.username}?\n\n"
            "This cannot be undone. If the account has recorded any "
            "transactions it will be refused — disable it instead.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        try:
            self._auth.delete_user(user.id)
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()
