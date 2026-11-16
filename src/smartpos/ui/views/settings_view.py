"""Shop profile, currency, tax rate and invoice footer.

Backup and restore are deliberately not on this screen. A restore replaces
every row in the database, and a button that does it sitting next to the shop's
phone number is an accident waiting to happen. The capability is unchanged and
runs from `scripts/backup.py`, where reaching for it is a decision rather than a
stray click.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.services.auth_service import AuthService
from smartpos.services.settings_service import FIELDS, SettingsService

log = get_logger(__name__)

# A settings form stretched across a maximised window is unreadable; the
# fields sit in a column of a sensible width instead.
CARD_WIDTH = 620


class SettingsView(QWidget):
    """Shop profile, currency, tax rate and invoice footer."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._settings = SettingsService(auth)
        self._inputs: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        heading = QLabel("Settings")
        heading.setObjectName("viewHeading")
        layout.addWidget(heading)

        row = QHBoxLayout()
        row.addWidget(self._build_profile())
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)

        self.status = QLabel("")
        self.status.setObjectName("hintLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    # ------------------------------------------------------------- building

    def _build_profile(self) -> QWidget:
        card = QFrame()
        card.setObjectName("panelCard")
        card.setMaximumWidth(CARD_WIDTH)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
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
        # Left-aligned: these labels vary from 'Phone' to 'Lock the screen
        # after (minutes)', and right-aligning them leaves a ragged edge.
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(14)
        for key, (label, _default) in FIELDS.items():
            field = QLineEdit()
            field.setMinimumHeight(30)
            if key == "shop_logo":
                picker = QHBoxLayout()
                picker.setSpacing(6)
                picker.addWidget(field, 1)
                browse = QPushButton("Browse…")
                browse.setMinimumWidth(92)        # it was squeezed to "Krowse"
                browse.setMinimumHeight(30)
                browse.clicked.connect(self._pick_logo)
                picker.addWidget(browse, 0)
                holder = QWidget()
                holder.setLayout(picker)
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
        """Reload the shop profile from the settings table."""
        try:
            values = self._settings.all(refresh=True)
        except Exception as exc:
            self._report(exc)
            return
        for key, field in self._inputs.items():
            field.setText(values.get(key, ""))

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
