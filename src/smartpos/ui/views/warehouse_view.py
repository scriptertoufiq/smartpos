"""Warehouses and outlets.

The list and the form are two pages of one screen rather than a dialog over the
top — same shape as the user screen, so the two feel like one application.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
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

from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.models.warehouse import Warehouse
from smartpos.services.auth_service import AuthService
from smartpos.services.warehouse_service import WarehouseService

log = get_logger(__name__)

COLUMNS = ["ID", "Code", "Name", "Address", "Phone", "Status"]
FORM_WIDTH = 560

PAGE_LIST = 0
PAGE_FORM = 1


class WarehouseView(QWidget):
    """Warehouses and outlets."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._service = WarehouseService(auth)
        self._warehouses: list[Warehouse] = []
        self._editing: Warehouse | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        self.heading = QLabel("Warehouses")
        self.heading.setObjectName("viewHeading")
        layout.addWidget(self.heading)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_list())      # PAGE_LIST
        self.pages.addWidget(self._build_form())      # PAGE_FORM
        layout.addWidget(self.pages, 1)

        # Read-only roles get the list without the buttons.
        writable = auth.can("warehouse.manage")
        self.add_button.setVisible(writable)
        self.edit_button.setVisible(writable)
        self.toggle_button.setVisible(writable)

    # -------------------------------------------------------- the list page

    def _build_list(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

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
        self.add_button.clicked.connect(self._start_create)
        self.edit_button.clicked.connect(self._start_edit)
        self.toggle_button.clicked.connect(self._toggle)
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

        self.form_title = QLabel("New outlet")
        self.form_title.setObjectName("panelHeading")
        layout.addWidget(self.form_title)

        form = QFormLayout()
        form.setSpacing(9)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(14)

        self.f_code = QLineEdit()
        self.f_code.setPlaceholderText("MAIN, GODOWN — no spaces")
        self.f_code.setMaxLength(20)
        self.f_code.setMinimumHeight(30)
        form.addRow("Code", self.f_code)

        self.f_name = QLineEdit()
        self.f_name.setPlaceholderText("Main Outlet")
        self.f_name.setMaxLength(120)
        self.f_name.setMinimumHeight(30)
        form.addRow("Name", self.f_name)

        self.f_address = QLineEdit()
        self.f_address.setMaxLength(255)
        self.f_address.setMinimumHeight(30)
        form.addRow("Address", self.f_address)

        self.f_phone = QLineEdit()
        self.f_phone.setMaxLength(30)
        self.f_phone.setMinimumHeight(30)
        form.addRow("Phone", self.f_phone)
        layout.addLayout(form)

        hint = QLabel("The code is how this outlet is identified on documents. "
                      "It is stored in capitals.")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setMinimumHeight(32)
        buttons.addWidget(self.cancel_button)
        self.save_button = QPushButton("Save outlet")
        self.save_button.setProperty("primary", True)
        self.save_button.setMinimumHeight(32)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self.cancel_button.clicked.connect(self._close_form)
        self.save_button.clicked.connect(self._save)
        for field in (self.f_code, self.f_name, self.f_address, self.f_phone):
            field.returnPressed.connect(self._save)
        return page

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

    # --- the form -------------------------------------------------------

    def _open_form(self, warehouse: Warehouse | None) -> None:
        """Show the form page, filled in for `warehouse` or blank for a new one."""
        self._editing = warehouse
        if warehouse is None:
            self.form_title.setText("New outlet")
            self.heading.setText("Warehouses › New outlet")
            self.f_code.clear()
            self.f_name.clear()
            self.f_address.clear()
            self.f_phone.clear()
        else:
            self.form_title.setText(f"Edit {warehouse.name}")
            self.heading.setText(f"Warehouses › {warehouse.code}")
            self.f_code.setText(warehouse.code)
            self.f_name.setText(warehouse.name)
            self.f_address.setText(warehouse.address or "")
            self.f_phone.setText(warehouse.phone or "")

        self.pages.setCurrentIndex(PAGE_FORM)
        self.f_code.setFocus()

    def _close_form(self) -> None:
        self._editing = None
        self.heading.setText("Warehouses")
        self.pages.setCurrentIndex(PAGE_LIST)
        self.table.setFocus()

    def _start_create(self) -> None:
        self._open_form(None)

    def _start_edit(self) -> None:
        warehouse = self._selected()
        if warehouse is not None:
            self._open_form(warehouse)

    def _save(self) -> None:
        code = self.f_code.text().strip()
        name = self.f_name.text().strip()
        address = self.f_address.text().strip()
        phone = self.f_phone.text().strip()

        self.save_button.setEnabled(False)
        try:
            if self._editing is None:
                self._service.create(code, name, address, phone)
            else:
                self._service.update(self._editing.id, code, name, address, phone)
        except Exception as exc:
            self._report(exc)
            return
        finally:
            self.save_button.setEnabled(True)

        self._close_form()
        self.refresh()

    # --- list actions ---------------------------------------------------

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
