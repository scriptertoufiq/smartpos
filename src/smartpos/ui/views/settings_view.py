"""Shop profile, currency, tax rate, invoice footer and backup."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.services.auth_service import AuthService
from smartpos.services.backup_service import CONFIRMATION, BackupService
from smartpos.services.settings_service import FIELDS, SettingsService

log = get_logger(__name__)

BACKUP_COLUMNS = ["File", "Taken", "Size (KB)"]


class RestoreDialog(QDialog):
    """Make the person type the word. A click is too easy to make by accident."""

    def __init__(self, parent, name: str):
        super().__init__(parent)
        self.setWindowTitle("Restore from backup")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        warning = QLabel(
            f"<b>This replaces every row in the database</b> with the contents "
            f"of {name}.<br><br>Anything recorded since that backup was taken "
            f"will be gone: sales, stock, payments, all of it.<br><br>"
            f"A copy of the current database is saved first, so this can itself "
            f"be undone.")
        warning.setWordWrap(True)
        warning.setObjectName("loginError")
        layout.addWidget(warning)

        layout.addWidget(QLabel(f"Type <b>{CONFIRMATION}</b> to continue:"))
        self.confirmation = QLineEdit()
        self.confirmation.setMinimumHeight(34)
        layout.addWidget(self.confirmation)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._ok.setText("Restore")
        self._ok.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.confirmation.textChanged.connect(
            lambda text: self._ok.setEnabled(text == CONFIRMATION))

    def value(self) -> str:
        return self.confirmation.text()


class SettingsView(QWidget):
    """Shop profile, currency, tax rate, invoice footer and backup."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._settings = SettingsService(auth)
        self._backup = BackupService(auth)
        self._inputs: dict[str, QLineEdit] = {}
        self._backups: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        heading = QLabel("Settings")
        heading.setObjectName("viewHeading")
        layout.addWidget(heading)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._build_profile(), 1)
        body.addWidget(self._build_backup(), 1)
        layout.addLayout(body)
        layout.addStretch(1)

        self.status = QLabel("")
        self.status.setObjectName("hintLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    # ------------------------------------------------------------- building

    def _build_profile(self) -> QWidget:
        card = QFrame()
        card.setObjectName("panelCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = QLabel("Shop profile")
        title.setObjectName("panelHeading")
        layout.addWidget(title)

        note = QLabel("These print on every invoice and appear throughout the "
                      "application.")
        note.setObjectName("hintLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        form.setSpacing(8)
        for key, (label, _default) in FIELDS.items():
            field = QLineEdit()
            field.setMinimumHeight(30)
            if key == "shop_logo":
                row = QHBoxLayout()
                row.setSpacing(6)
                row.addWidget(field, 1)
                browse = QPushButton("Browse…")
                browse.clicked.connect(self._pick_logo)
                row.addWidget(browse)
                holder = QWidget()
                holder.setLayout(row)
                form.addRow(label, holder)
            else:
                form.addRow(label, field)
            self._inputs[key] = field
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.revert_button = QPushButton("Revert")
        self.revert_button.setMinimumHeight(32)
        buttons.addWidget(self.revert_button)
        self.save_button = QPushButton("Save settings")
        self.save_button.setProperty("primary", True)
        self.save_button.setMinimumHeight(32)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self.save_button.clicked.connect(self._save)
        self.revert_button.clicked.connect(self.refresh)

        writable = self._auth.can("settings.manage")
        self.save_button.setVisible(writable)
        self.revert_button.setVisible(writable)
        for field in self._inputs.values():
            field.setReadOnly(not writable)
        return card

    def _build_backup(self) -> QWidget:
        card = QFrame()
        card.setObjectName("panelCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = QLabel("Backup and restore")
        title.setObjectName("panelHeading")
        layout.addWidget(title)

        self.backup_note = QLabel("")
        self.backup_note.setObjectName("hintLabel")
        self.backup_note.setWordWrap(True)
        layout.addWidget(self.backup_note)

        self.backup_table = QTableWidget(0, len(BACKUP_COLUMNS))
        self.backup_table.setHorizontalHeaderLabels(BACKUP_COLUMNS)
        self.backup_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.backup_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.backup_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.backup_table.verticalHeader().setVisible(False)
        self.backup_table.setAlternatingRowColors(True)
        self.backup_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.backup_table, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.backup_now = QPushButton("Back up now")
        self.backup_now.setProperty("primary", True)
        self.restore_button = QPushButton("Restore…")
        self.restore_button.setProperty("danger", True)
        self.delete_button = QPushButton("Delete")
        for b in (self.backup_now, self.restore_button, self.delete_button):
            b.setMinimumHeight(32)
            buttons.addWidget(b)
        self.restore_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.backup_now.clicked.connect(self._create_backup)
        self.restore_button.clicked.connect(self._restore)
        self.delete_button.clicked.connect(self._delete)
        self.backup_table.itemSelectionChanged.connect(self._selection_changed)

        writable = self._auth.can("backup.manage")
        for b in (self.backup_now, self.restore_button, self.delete_button):
            b.setVisible(writable)
        return card

    # ------------------------------------------------------------- behaviour

    def _report(self, exc: Exception) -> None:
        if isinstance(exc, SmartPOSError):
            QMessageBox.warning(self, "Cannot do that", str(exc))
        else:
            log.exception("Unexpected error in settings")
            QMessageBox.critical(
                self, "Something went wrong",
                "The action failed. The details were written to the log file.")

    def refresh(self) -> None:
        """Reload settings and the list of backups."""
        try:
            values = self._settings.all(refresh=True)
        except Exception as exc:
            self._report(exc)
            return
        for key, field in self._inputs.items():
            field.setText(values.get(key, ""))

        self._load_backups()

    def _load_backups(self) -> None:
        try:
            self._backups = self._backup.list_backups()
            folder = self._backup.backup_dir()
        except Exception as exc:
            self._report(exc)
            return

        tool = ("mysqldump" if self._backup.mysqldump_available()
                else "the application itself (mysqldump is not installed)")
        self.backup_note.setText(f"Saved in {folder}. Written by {tool}.")

        self.backup_table.setRowCount(len(self._backups))
        for row, item in enumerate(self._backups):
            cells = [item["name"],
                     item["taken_at"].strftime("%d %b %Y, %H:%M"),
                     f"{item['size_kb']:,.1f}"]
            for column, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if column == 2:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.backup_table.setItem(row, column, cell)
        self.backup_table.resizeColumnsToContents()
        self.backup_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._selection_changed()

    def _selection_changed(self) -> None:
        chosen = bool(self.backup_table.selectionModel()
                      and self.backup_table.selectionModel().selectedRows())
        self.restore_button.setEnabled(chosen)
        self.delete_button.setEnabled(chosen)

    def _selected(self) -> dict | None:
        rows = self.backup_table.selectionModel().selectedRows()
        return self._backups[rows[0].row()] if rows else None

    # ---------------------------------------------------------------- actions

    def _pick_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a logo", str(Path.home()), "Images (*.png *.jpg *.jpeg)")
        if path:
            self._inputs["shop_logo"].setText(path)

    def _save(self) -> None:
        values = {key: field.text() for key, field in self._inputs.items()}
        try:
            self._settings.save(values)
        except Exception as exc:
            self._report(exc)
            return
        self.status.setText("Settings saved. They appear on the next invoice.")
        self.status.setStyleSheet("color: #047857;")
        self.refresh()

    def _create_backup(self) -> None:
        self.backup_now.setEnabled(False)
        self.backup_now.setText("Backing up…")
        try:
            path = self._backup.create_backup()
        except Exception as exc:
            self._report(exc)
            return
        finally:
            self.backup_now.setEnabled(True)
            self.backup_now.setText("Back up now")

        size = path.stat().st_size / 1024
        self.status.setText(f"Backed up to {path} ({size:,.1f} KB).")
        self.status.setStyleSheet("color: #047857;")
        self._load_backups()

    def _restore(self) -> None:
        chosen = self._selected()
        if chosen is None:
            return
        dialog = RestoreDialog(self, chosen["name"])
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            result = self._backup.restore_backup(chosen["path"], dialog.value())
        except Exception as exc:
            self._report(exc)
            return

        QMessageBox.information(
            self, "Restored",
            f"{chosen['name']} was restored ({result['statements']} statements).\n\n"
            f"The database as it was a moment ago is saved at\n"
            f"{result['safety_copy'].name}\n\n"
            "Sign out and back in so every screen reloads.")
        self.refresh()

    def _delete(self) -> None:
        chosen = self._selected()
        if chosen is None:
            return
        if QMessageBox.question(
            self, "Delete backup", f"Delete {chosen['name']}?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self._backup.delete_backup(chosen["path"])
        except Exception as exc:
            self._report(exc)
            return
        self._load_backups()
