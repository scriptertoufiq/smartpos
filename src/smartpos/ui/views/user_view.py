"""User accounts and roles. Admin only.

The list and the form are two pages of one screen rather than a dialog over the
top. A modal hides the list you were just reading, and on a maximised window it
floats in the middle of a lot of empty space; switching pages keeps the screen
whole and leaves room for the form to breathe.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
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
FORM_WIDTH = 560

PAGE_LIST = 0
PAGE_FORM = 1


class UserView(QWidget):
    """User accounts and roles. Admin only."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._warehouses = WarehouseService()
        self._users: list[User] = []
        self._editing: User | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        self.heading = QLabel("Users")
        self.heading.setObjectName("viewHeading")
        layout.addWidget(self.heading)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_list())      # PAGE_LIST
        self.pages.addWidget(self._build_form())      # PAGE_FORM
        layout.addWidget(self.pages, 1)

    # -------------------------------------------------------- the list page

    def _build_list(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

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

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

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

        self.search.textChanged.connect(self.refresh)
        self.role_filter.currentIndexChanged.connect(self.refresh)
        self.show_disabled.toggled.connect(self.refresh)
        self.add_button.clicked.connect(self._start_create)
        self.edit_button.clicked.connect(self._start_edit)
        self.password_button.clicked.connect(self._reset_password)
        self.toggle_button.clicked.connect(self._toggle_active)
        self.delete_button.clicked.connect(self._delete)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(self._start_edit)
        return page

    # -------------------------------------------------------- the form page

    def _build_form(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        row = QHBoxLayout()
        card = QFrame()
        card.setObjectName("panelCard")
        card.setFixedWidth(FORM_WIDTH)   # a form stretched across the
        # window is unreadable; it sits in a column of a sensible width
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.form_title = QLabel("New user")
        self.form_title.setObjectName("panelHeading")
        layout.addWidget(self.form_title)

        form = QFormLayout()
        form.setSpacing(9)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(14)

        self.f_username = QLineEdit()
        self.f_username.setPlaceholderText("no spaces, at least 3 characters")
        self.f_username.setMaxLength(50)
        self.f_username.setMinimumHeight(30)
        form.addRow("Username", self.f_username)

        self.f_full_name = QLineEdit()
        self.f_full_name.setMaxLength(120)
        self.f_full_name.setMinimumHeight(30)
        form.addRow("Full name", self.f_full_name)

        self.f_role = QComboBox()
        for role in Role:
            self.f_role.addItem(role.value.capitalize(), role)
        self.f_role.setMinimumHeight(30)
        form.addRow("Role", self.f_role)

        self.f_warehouse = QComboBox()
        self.f_warehouse.setMinimumHeight(30)
        form.addRow("Warehouse", self.f_warehouse)

        self.f_password = QLineEdit()
        self.f_password.setEchoMode(QLineEdit.Password)
        self.f_password.setPlaceholderText("at least 6 characters")
        self.f_password.setMinimumHeight(30)
        self._password_label = QLabel("Password")
        form.addRow(self._password_label, self.f_password)

        self.f_confirm = QLineEdit()
        self.f_confirm.setEchoMode(QLineEdit.Password)
        self.f_confirm.setMinimumHeight(30)
        self._confirm_label = QLabel("Repeat it")
        form.addRow(self._confirm_label, self.f_confirm)
        layout.addLayout(form)

        self.form_hint = QLabel("")
        self.form_hint.setObjectName("hintLabel")
        self.form_hint.setWordWrap(True)
        layout.addWidget(self.form_hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setMinimumHeight(32)
        buttons.addWidget(self.cancel_button)
        self.save_button = QPushButton("Save user")
        self.save_button.setProperty("primary", True)
        self.save_button.setMinimumHeight(32)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self.cancel_button.clicked.connect(self._close_form)
        self.save_button.clicked.connect(self._save)
        for field in (self.f_username, self.f_full_name,
                      self.f_password, self.f_confirm):
            field.returnPressed.connect(self._save)
        return page

    # ------------------------------------------------------------- helpers

    def _selected(self) -> User | None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        return self._users[rows[0].row()] if rows else None

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

    # --- the form -------------------------------------------------------

    def _open_form(self, user: User | None) -> None:
        """Show the form page, filled in for `user` or blank for a new one."""
        self._editing = user
        creating = user is None

        self.f_warehouse.clear()
        self.f_warehouse.addItem("— not posted to an outlet —", None)
        for warehouse in self._warehouse_options():
            self.f_warehouse.addItem(f"{warehouse.name} ({warehouse.code})", warehouse.id)

        if creating:
            self.form_title.setText("New user")
            self.heading.setText("Users › New user")
            self.f_username.clear()
            self.f_full_name.clear()
            self.f_role.setCurrentIndex(list(Role).index(Role.CASHIER))
            self.f_warehouse.setCurrentIndex(0)
            self.form_hint.setText(
                "The password is set here. The person can change it themselves "
                "after they sign in.")
        else:
            self.form_title.setText(f"Edit {user.username}")
            self.heading.setText(f"Users › {user.username}")
            self.f_username.setText(user.username)
            self.f_full_name.setText(user.full_name)
            self.f_role.setCurrentIndex(list(Role).index(user.role))
            if user.warehouse_id is not None:
                index = self.f_warehouse.findData(user.warehouse_id)
                if index >= 0:
                    self.f_warehouse.setCurrentIndex(index)
                else:
                    # Posted to an outlet that is closed; show it rather than
                    # silently clearing the posting on save.
                    self.f_warehouse.addItem(f"#{user.warehouse_id} (unavailable)",
                                             user.warehouse_id)
                    self.f_warehouse.setCurrentIndex(self.f_warehouse.count() - 1)
            self.form_hint.setText(
                "Use “Reset password” on the list to change this person's password.")

        # The password pair only belongs on a new account.
        for widget in (self._password_label, self.f_password,
                       self._confirm_label, self.f_confirm):
            widget.setVisible(creating)
        self.f_password.clear()
        self.f_confirm.clear()

        self.pages.setCurrentIndex(PAGE_FORM)
        self.f_username.setFocus()

    def _close_form(self) -> None:
        self._editing = None
        self.heading.setText("Users")
        self.pages.setCurrentIndex(PAGE_LIST)
        self.table.setFocus()

    def _start_create(self) -> None:
        self._open_form(None)

    def _start_edit(self) -> None:
        user = self._selected()
        if user is not None:
            self._open_form(user)

    def _save(self) -> None:
        creating = self._editing is None
        warehouse_id = self.f_warehouse.currentData()

        if creating and self.f_password.text() != self.f_confirm.text():
            QMessageBox.warning(self, "Check the password",
                                "The two passwords do not match.")
            self.f_confirm.setFocus()
            return

        self.save_button.setEnabled(False)
        try:
            if creating:
                self._auth.create_user(
                    self.f_username.text().strip(),
                    self.f_full_name.text().strip(),
                    self.f_password.text(),
                    self.f_role.currentData(),
                    warehouse_id)
            else:
                self._auth.update_user(
                    self._editing.id,
                    full_name=self.f_full_name.text().strip(),
                    role=self.f_role.currentData(),
                    username=self.f_username.text().strip())
                # update_user reads None as "leave it alone", so clearing a
                # posting has to go through the method that means it.
                if warehouse_id != self._editing.warehouse_id:
                    self._auth.set_warehouse(self._editing.id, warehouse_id)
        except Exception as exc:
            self._report(exc)
            return
        finally:
            self.save_button.setEnabled(True)

        self._close_form()
        self.refresh()

    # --- list actions ---------------------------------------------------

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
