"""Stock on hand per warehouse, transfers, adjustments and the ledger."""

from __future__ import annotations

from decimal import Decimal

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.services.auth_service import AuthService
from smartpos.services.product_service import ProductService
from smartpos.services.stock_service import StockService
from smartpos.services.warehouse_service import WarehouseService

log = get_logger(__name__)

LEVEL_COLUMNS = ["SKU", "Product", "Outlet", "On hand", "Unit", "Reorder at", "State"]
LEDGER_COLUMNS = ["When", "Outlet", "Movement", "Change", "Balance", "Document", "By"]


class AdjustDialog(QDialog):
    """Correct a level after a physical count."""

    def __init__(self, parent, product_name: str, outlet: str, current: Decimal):
        super().__init__(parent)
        self.setWindowTitle("Adjust stock")
        self.setMinimumWidth(400)
        form = QFormLayout(self)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(10)

        form.addRow("Product", QLabel(product_name))
        form.addRow("Outlet", QLabel(outlet))
        form.addRow("System says", QLabel(f"{current:,.3f}"))

        self.counted = QDoubleSpinBox()
        self.counted.setDecimals(3)
        self.counted.setMaximum(9_999_999.0)
        self.counted.setValue(float(current))
        self.counted.setMinimumHeight(32)
        form.addRow("Actually counted", self.counted)

        self.reason = QLineEdit()
        self.reason.setPlaceholderText("3 packets damaged in transit")
        self.reason.setMaxLength(255)
        form.addRow("Reason", self.reason)

        hint = QLabel("The difference is written to the ledger, not the new "
                      "total — so the correction itself is on record.")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        form.addRow("", hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _check(self) -> None:
        if not self.reason.text().strip():
            QMessageBox.warning(self, "Reason needed",
                                "An adjustment must say why.")
            return
        self.accept()

    def values(self) -> dict:
        return {"quantity": Decimal(str(self.counted.value())),
                "reason": self.reason.text().strip()}


class TransferDialog(QDialog):
    """Move one product between two outlets."""

    def __init__(self, parent, warehouses, products, default_from=None):
        super().__init__(parent)
        self.setWindowTitle("Transfer stock")
        self.setMinimumWidth(420)
        form = QFormLayout(self)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(10)

        self.product = QComboBox()
        for p in products:
            self.product.addItem(f"{p.name} ({p.sku})", p.id)
        form.addRow("Product", self.product)

        self.source = QComboBox()
        self.destination = QComboBox()
        for w in warehouses:
            self.source.addItem(w.name, w.id)
            self.destination.addItem(w.name, w.id)
        if default_from is not None:
            index = self.source.findData(default_from)
            if index >= 0:
                self.source.setCurrentIndex(index)
        if self.destination.count() > 1:
            self.destination.setCurrentIndex(
                1 if self.source.currentIndex() == 0 else 0)
        form.addRow("From", self.source)
        form.addRow("To", self.destination)

        self.quantity = QDoubleSpinBox()
        self.quantity.setDecimals(3)
        self.quantity.setMaximum(9_999_999.0)
        self.quantity.setMinimumHeight(32)
        form.addRow("Quantity", self.quantity)

        self.note = QLineEdit()
        self.note.setMaxLength(255)
        form.addRow("Note", self.note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _check(self) -> None:
        if self.source.currentData() == self.destination.currentData():
            QMessageBox.warning(self, "Same outlet",
                                "Choose two different outlets.")
            return
        if self.quantity.value() <= 0:
            QMessageBox.warning(self, "Nothing to move",
                                "Enter a quantity greater than zero.")
            return
        self.accept()

    def values(self) -> dict:
        return {"product_id": self.product.currentData(),
                "from_id": self.source.currentData(),
                "to_id": self.destination.currentData(),
                "quantity": Decimal(str(self.quantity.value())),
                "note": self.note.text().strip()}


class StockView(QWidget):
    """Stock levels per warehouse, transfers, adjustments and the ledger."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._stock = StockService(auth)
        self._warehouses = WarehouseService(auth)
        self._products = ProductService(auth)
        self._levels: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        heading = QLabel("Stock")
        heading.setObjectName("viewHeading")
        layout.addWidget(heading)

        blurb = QLabel("What is on the shelves, and why. Every figure here is "
                       "explained by the ledger below it.")
        blurb.setObjectName("hintLabel")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by product, SKU or barcode…")
        self.search.setMinimumHeight(34)
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search, 1)
        self.outlet = QComboBox()
        self.outlet.setMinimumHeight(34)
        self.outlet.setMinimumWidth(170)
        bar.addWidget(self.outlet)
        self.low_only = QCheckBox("Low stock only")
        bar.addWidget(self.low_only)
        self.hide_empty = QCheckBox("Hide empty")
        bar.addWidget(self.hide_empty)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(LEVEL_COLUMNS))
        self.table.setHorizontalHeaderLabels(LEVEL_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        splitter.addWidget(self.table)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 8, 0, 0)
        panel_layout.setSpacing(6)
        self.ledger_heading = QLabel("Movement ledger")
        self.ledger_heading.setObjectName("panelHeading")
        panel_layout.addWidget(self.ledger_heading)
        self.ledger = QTableWidget(0, len(LEDGER_COLUMNS))
        self.ledger.setHorizontalHeaderLabels(LEDGER_COLUMNS)
        self.ledger.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ledger.verticalHeader().setVisible(False)
        self.ledger.setAlternatingRowColors(True)
        panel_layout.addWidget(self.ledger)
        splitter.addWidget(panel)
        splitter.setSizes([400, 260])
        layout.addWidget(splitter, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.adjust_button = QPushButton("Adjust")
        self.transfer_button = QPushButton("Transfer")
        self.transfer_button.setProperty("primary", True)
        for b in (self.adjust_button, self.transfer_button):
            b.setMinimumHeight(32)
            actions.addWidget(b)
        self.adjust_button.setEnabled(False)
        actions.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("hintLabel")
        actions.addWidget(self.status)
        layout.addLayout(actions)

        self.search.textChanged.connect(self.refresh)
        self.outlet.currentIndexChanged.connect(self.refresh)
        self.low_only.toggled.connect(self.refresh)
        self.hide_empty.toggled.connect(self.refresh)
        self.adjust_button.clicked.connect(self._adjust)
        self.transfer_button.clicked.connect(self._transfer)
        self.table.itemSelectionChanged.connect(self._selection_changed)

        writable = auth.can("stock.manage")
        self.adjust_button.setVisible(writable)
        self.transfer_button.setVisible(writable)
        self._load_outlets()

    # ------------------------------------------------------------- helpers

    def _load_outlets(self) -> None:
        self.outlet.blockSignals(True)
        self.outlet.clear()
        self.outlet.addItem("All outlets", None)
        try:
            for w in self._warehouses.list_all(active_only=True):
                self.outlet.addItem(w.name, w.id)
        except Exception:
            log.exception("Could not load outlets")
        self.outlet.blockSignals(False)

    def _selected(self) -> dict | None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        return self._levels[rows[0].row()] if rows else None

    def _report(self, exc: Exception) -> None:
        if isinstance(exc, SmartPOSError):
            QMessageBox.warning(self, "Cannot do that", str(exc))
        else:
            log.exception("Unexpected error in the stock screen")
            QMessageBox.critical(
                self, "Something went wrong",
                "The action failed. The details were written to the log file.")

    # -------------------------------------------------------------- actions

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        warehouse_id = self.outlet.currentData()
        try:
            if self.low_only.isChecked():
                rows = self._stock.low_stock(warehouse_id)
                for r in rows:
                    r.setdefault("unit_name", None)
                    r.setdefault("sale_price", None)
            else:
                rows = self._stock.levels(warehouse_id,
                                          self.search.text().strip(),
                                          only_nonzero=self.hide_empty.isChecked())
            self._levels = rows
        except Exception as exc:
            self._report(exc)
            return

        self.table.setRowCount(len(rows))
        for row, level in enumerate(rows):
            quantity = Decimal(str(level["quantity"]))
            reorder = Decimal(str(level.get("reorder_level") or 0))
            state = "OK"
            if quantity <= 0:
                state = "Empty"
            elif reorder > 0 and quantity <= reorder:
                state = "Low"
            cells = [level["sku"], level["product_name"], level["warehouse_name"],
                     f"{quantity:,.3f}", level.get("unit_name") or "—",
                     f"{reorder:,.3f}" if reorder else "—", state]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if col in (3, 5):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif col == 6:
                    item.setTextAlignment(Qt.AlignCenter)
                    if state == "Low":
                        item.setForeground(Qt.darkYellow)
                    elif state == "Empty":
                        item.setForeground(Qt.gray)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        low = sum(1 for r in rows
                  if Decimal(str(r.get("reorder_level") or 0)) > 0
                  and Decimal(str(r["quantity"])) <= Decimal(str(r["reorder_level"])))
        self.status.setText(f"{len(rows)} line(s) · {low} at or below reorder level")
        self._selection_changed()

    def _selection_changed(self) -> None:
        level = self._selected()
        self.adjust_button.setEnabled(level is not None)
        if level is None:
            self.ledger.setRowCount(0)
            self.ledger_heading.setText("Movement ledger")
            return

        self.ledger_heading.setText(
            f"Movement ledger — {level['product_name']} at {level['warehouse_name']}")
        try:
            rows = self._stock.ledger(level["product_id"], level["warehouse_id"], 200)
        except Exception:
            log.exception("Could not load the ledger")
            self.ledger.setRowCount(0)
            return

        self.ledger.setRowCount(len(rows))
        for r, m in enumerate(rows):
            when = m["created_at"]
            document = (f"{m['reference_type']} #{m['reference_id']}"
                        if m["reference_type"] and m["reference_id"]
                        else (m["reference_type"] or "—"))
            change = Decimal(str(m["quantity"]))
            cells = [when.strftime("%d %b %Y, %H:%M") if when else "—",
                     m["warehouse_name"],
                     str(m["movement_type"]).replace("_", " "),
                     f"{change:+,.3f}",
                     f"{Decimal(str(m['balance_after'])):,.3f}",
                     document, m.get("username") or "—"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c in (3, 4):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 3:
                    item.setForeground(Qt.darkGreen if change > 0 else Qt.darkRed)
                self.ledger.setItem(r, c, item)
        self.ledger.resizeColumnsToContents()

    def _adjust(self) -> None:
        level = self._selected()
        if level is None:
            return
        dialog = AdjustDialog(self, level["product_name"], level["warehouse_name"],
                              Decimal(str(level["quantity"])))
        if dialog.exec_() != QDialog.Accepted:
            return
        v = dialog.values()
        try:
            self._stock.adjust(level["product_id"], level["warehouse_id"],
                               v["quantity"], v["reason"])
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()

    def _transfer(self) -> None:
        try:
            warehouses = self._warehouses.list_all(active_only=True)
            products = self._products.search("", limit=500)
        except Exception as exc:
            self._report(exc)
            return
        if len(warehouses) < 2:
            QMessageBox.information(
                self, "Only one outlet",
                "A transfer needs somewhere to move stock to. Create a second "
                "outlet on the Warehouses screen first.")
            return
        if not products:
            QMessageBox.information(self, "No products",
                                    "Add a product before transferring stock.")
            return

        dialog = TransferDialog(self, warehouses, products, self.outlet.currentData())
        if dialog.exec_() != QDialog.Accepted:
            return
        v = dialog.values()
        try:
            self._stock.transfer(v["from_id"], v["to_id"],
                                 [(v["product_id"], v["quantity"])], v["note"])
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()
