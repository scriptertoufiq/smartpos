"""Warehouses and outlets."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.models.warehouse import Warehouse
from smartpos.services.auth_service import AuthService
from smartpos.services.warehouse_service import WarehouseService

log = get_logger(__name__)

COLUMNS = ["ID", "Code", "Name", "Address", "Phone", "Status"]


class WarehouseEditor(QDialog):
    """Create or edit one outlet."""

    def __init__(self, parent=None, warehouse: Warehouse | None = None) -> None:
        super().__init__(parent)
        self.warehouse = warehouse
        creating = warehouse is None
        self.setWindowTitle("New outlet" if creating else f"Edit {warehouse.name}")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(10)

        self.code = QLineEdit(warehouse.code if warehouse else "")
        self.code.setPlaceholderText("MAIN, GODOWN — no spaces")
        self.code.setMaxLength(20)
        form.addRow("Code", self.code)

        self.name = QLineEdit(warehouse.name if warehouse else "")
        self.name.setPlaceholderText("Main Outlet")
        self.name.setMaxLength(120)
        form.addRow("Name", self.name)

        self.address = QLineEdit(warehouse.address or "" if warehouse else "")
        self.address.setMaxLength(255)
        form.addRow("Address", self.address)

        self.phone = QLineEdit(warehouse.phone or "" if warehouse else "")
        self.phone.setMaxLength(30)
        form.addRow("Phone", self.phone)

        hint = QLabel("The code is how this outlet is identified on documents. "
                      "It is stored in capitals.")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        form.addRow("", hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        return {
            "code": self.code.text().strip(),
            "name": self.name.text().strip(),
            "address": self.address.text().strip(),
            "phone": self.phone.text().strip(),
        }


class WarehouseView(QWidget):
    """Warehouses and outlets."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._service = WarehouseService(auth)
        self._warehouses: list[Warehouse] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        heading = QLabel("Warehouses")
        heading.setObjectName("viewHeading")
        layout.addWidget(heading)

        blurb = QLabel("Any stock location — the shop floor, a godown, a second "
                       "branch. Outlets are closed, never deleted, so old "
                       "documents keep resolving.")
        blurb.setObjectName("hintLabel")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.show_closed = QCheckBox("Show closed")
        self.show_closed.setChecked(True)
        bar.addWidget(self.show_closed)
        bar.addStretch(1)
        self.add_button = QPushButton("New outlet")
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
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.edit_button = QPushButton("Edit")
        self.toggle_button = QPushButton("Close outlet")
        for b in (self.edit_button, self.toggle_button):
            b.setMinimumHeight(32)
            b.setEnabled(False)
            actions.addWidget(b)
        actions.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("hintLabel")
        actions.addWidget(self.status)
        layout.addLayout(actions)

        self.show_closed.toggled.connect(self.refresh)
        self.add_button.clicked.connect(self._create)
        self.edit_button.clicked.connect(self._edit)
        self.toggle_button.clicked.connect(self._toggle)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(self._edit)

        # Read-only roles get the list without the buttons.
        writable = auth.can("warehouse.manage")
        self.add_button.setVisible(writable)
        self.edit_button.setVisible(writable)
        self.toggle_button.setVisible(writable)

    # ------------------------------------------------------------- helpers

    def _selected(self) -> Warehouse | None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        return self._warehouses[rows[0].row()] if rows else None

    def _selection_changed(self) -> None:
        warehouse = self._selected()
        chosen = warehouse is not None
        self.edit_button.setEnabled(chosen)
        self.toggle_button.setEnabled(chosen)
        if not chosen:
            self.status.setText("")
            return

        self.toggle_button.setText("Reopen outlet" if not warehouse.is_active
                                   else "Close outlet")
        try:
            usage = self._service.usage(warehouse.id)
        except Exception:
            log.exception("Could not read outlet usage")
            self.status.setText("")
            return
        bits = []
        if usage["products_in_stock"]:
            bits.append(f"{usage['products_in_stock']} product(s) in stock")
        if usage["users_posted"]:
            bits.append(f"{usage['users_posted']} user(s) posted")
        self.status.setText(" · ".join(bits))

    def _report(self, exc: Exception) -> None:
        if isinstance(exc, SmartPOSError):
            QMessageBox.warning(self, "Cannot do that", str(exc))
        else:
            log.exception("Unexpected error in the warehouse screen")
            QMessageBox.critical(
                self, "Something went wrong",
                "The action failed. The details were written to the log file.")

    # -------------------------------------------------------------- actions

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        try:
            self._warehouses = self._service.list_all(
                active_only=not self.show_closed.isChecked())
        except Exception as exc:
            self._report(exc)
            return

        self.table.setRowCount(len(self._warehouses))
        for row, w in enumerate(self._warehouses):
            cells = [str(w.id), w.code, w.name, w.address or "—", w.phone or "—",
                     "Active" if w.is_active else "Closed"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (0, 5):
                    item.setTextAlignment(Qt.AlignCenter)
                if not w.is_active:
                    item.setForeground(Qt.gray)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._selection_changed()

    def _create(self) -> None:
        dialog = WarehouseEditor(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        v = dialog.values()
        try:
            self._service.create(v["code"], v["name"], v["address"], v["phone"])
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()

    def _edit(self) -> None:
        warehouse = self._selected()
        if warehouse is None:
            return
        dialog = WarehouseEditor(self, warehouse)
        if dialog.exec_() != QDialog.Accepted:
            return
        v = dialog.values()
        try:
            self._service.update(warehouse.id, v["code"], v["name"],
                                 v["address"], v["phone"])
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()

    def _toggle(self) -> None:
        warehouse = self._selected()
        if warehouse is None:
            return
        try:
            if warehouse.is_active:
                usage = self._service.usage(warehouse.id)
                if usage["users_posted"]:
                    ok = QMessageBox.question(
                        self, "Close outlet",
                        f"{usage['users_posted']} user(s) are posted to "
                        f"{warehouse.name}. Their posting will point at a closed "
                        "outlet until you move them.\n\nClose it anyway?",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                    if ok != QMessageBox.Yes:
                        return
                self._service.deactivate(warehouse.id)
            else:
                self._service.activate(warehouse.id)
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()
