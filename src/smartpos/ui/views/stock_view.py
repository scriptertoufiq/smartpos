"""Stock on hand per warehouse, transfers, adjustments and the ledger.

The levels list, the adjustment form and the transfer form are three pages of
one screen; see `smartpos.ui.widgets.form_page` for why there are no dialogs
here.
"""

from __future__ import annotations

from decimal import Decimal

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.services.auth_service import AuthService
from smartpos.services.product_service import ProductService
from smartpos.services.stock_service import StockService
from smartpos.services.warehouse_service import WarehouseService
from smartpos.ui.widgets.form_page import FormCard, FormPage

log = get_logger(__name__)

LEVEL_COLUMNS = ["SKU", "Product", "Outlet", "On hand", "Unit", "Reorder at", "State"]
LEDGER_COLUMNS = ["When", "Outlet", "Movement", "Change", "Balance", "Document", "By"]

PAGE_LEVELS = 0
PAGE_ADJUST = 1
PAGE_TRANSFER = 2


class StockView(QWidget):
    """Stock levels per warehouse, transfers, adjustments and the ledger."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._stock = StockService(auth)
        self._warehouses = WarehouseService(auth)
        self._products = ProductService(auth)
        self._levels: list[dict] = []

        self._adjusting: dict | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(12)

        self.heading = QLabel("Stock")
        self.heading.setObjectName("viewHeading")
        outer.addWidget(self.heading)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_list(auth))       # PAGE_LEVELS
        self.pages.addWidget(self._build_adjust())         # PAGE_ADJUST
        self.pages.addWidget(self._build_transfer())       # PAGE_TRANSFER
        outer.addWidget(self.pages, 1)

    # ------------------------------------------------------- the levels page

    def _build_list(self, auth: AuthService) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

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
        return page

    # ------------------------------------------------------ the two form pages

    def _build_adjust(self) -> QWidget:
        card = FormCard(save_text="Record adjustment")
        self.adjust_card = card

        self.a_product = card.add_row("Product", QLabel(""))
        self.a_outlet = card.add_row("Outlet", QLabel(""))
        self.a_current = card.add_row("System says", QLabel(""))

        self.a_counted = QDoubleSpinBox()
        self.a_counted.setDecimals(3)
        self.a_counted.setMaximum(9_999_999.0)
        card.add_row("Actually counted", self.a_counted)

        self.a_reason = card.add_row("Reason", QLineEdit())
        self.a_reason.setPlaceholderText("3 packets damaged in transit")
        self.a_reason.setMaxLength(255)

        card.set_hint("The difference is written to the ledger, not the new "
                      "total \u2014 so the correction itself is on record.")
        card.save_clicked.connect(self._save_adjustment)
        card.cancel_clicked.connect(self._close_form)
        self.a_reason.returnPressed.connect(self._save_adjustment)
        return FormPage(card)

    def _build_transfer(self) -> QWidget:
        card = FormCard(save_text="Move the stock")
        self.transfer_card = card

        self.t_product = card.add_row("Product", QComboBox())
        self.t_source = card.add_row("From", QComboBox())
        self.t_destination = card.add_row("To", QComboBox())

        self.t_quantity = QDoubleSpinBox()
        self.t_quantity.setDecimals(3)
        self.t_quantity.setMaximum(9_999_999.0)
        card.add_row("Quantity", self.t_quantity)

        self.t_note = card.add_row("Note", QLineEdit())
        self.t_note.setMaxLength(255)

        card.set_hint("Both outlets' levels move in one transaction, so the "
                      "total on hand never changes.")
        card.save_clicked.connect(self._save_transfer)
        card.cancel_clicked.connect(self._close_form)
        self.t_note.returnPressed.connect(self._save_transfer)
        return FormPage(card)

    def _close_form(self) -> None:
        self._adjusting = None
        self.heading.setText("Stock")
        self.pages.setCurrentIndex(PAGE_LEVELS)
        self.table.setFocus()

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

    # --- adjusting ------------------------------------------------------

    def _adjust(self) -> None:
        level = self._selected()
        if level is None:
            return
        self._adjusting = level
        current = Decimal(str(level["quantity"]))

        self.adjust_card.set_title(f"Adjust {level['product_name']}")
        self.heading.setText(f"Stock \u203a Adjust {level['product_name']}")
        self.a_product.setText(level["product_name"])
        self.a_outlet.setText(level["warehouse_name"])
        self.a_current.setText(f"{current:,.3f}")
        self.a_counted.setValue(float(current))
        self.a_reason.clear()

        self.pages.setCurrentIndex(PAGE_ADJUST)
        self.a_counted.setFocus()
        self.a_counted.selectAll()

    def _save_adjustment(self) -> None:
        level = self._adjusting
        if level is None:
            return
        reason = self.a_reason.text().strip()
        if not reason:
            QMessageBox.warning(self, "Reason needed", "An adjustment must say why.")
            self.a_reason.setFocus()
            return

        self.adjust_card.set_busy(True)
        try:
            self._stock.adjust(level["product_id"], level["warehouse_id"],
                               Decimal(str(self.a_counted.value())), reason)
        except Exception as exc:
            self._report(exc)
            return
        finally:
            self.adjust_card.set_busy(False)

        self._close_form()
        self.refresh()

    # --- transferring ---------------------------------------------------

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

        self.t_product.clear()
        for product in products:
            self.t_product.addItem(f"{product.name} ({product.sku})", product.id)

        self.t_source.clear()
        self.t_destination.clear()
        for warehouse in warehouses:
            self.t_source.addItem(warehouse.name, warehouse.id)
            self.t_destination.addItem(warehouse.name, warehouse.id)
        index = self.t_source.findData(self.outlet.currentData())
        if index >= 0:
            self.t_source.setCurrentIndex(index)
        # Default the destination to anywhere but the source, so the first
        # thing the form shows is already a legal transfer.
        self.t_destination.setCurrentIndex(
            1 if self.t_source.currentIndex() == 0 else 0)

        self.t_quantity.setValue(0.0)
        self.t_note.clear()
        self.transfer_card.set_title("Transfer stock")
        self.heading.setText("Stock \u203a Transfer")
        self.pages.setCurrentIndex(PAGE_TRANSFER)
        self.t_product.setFocus()

    def _save_transfer(self) -> None:
        if self.t_source.currentData() == self.t_destination.currentData():
            QMessageBox.warning(self, "Same outlet", "Choose two different outlets.")
            return
        if self.t_quantity.value() <= 0:
            QMessageBox.warning(self, "Nothing to move",
                                "Enter a quantity greater than zero.")
            self.t_quantity.setFocus()
            return

        self.transfer_card.set_busy(True)
        try:
            self._stock.transfer(
                self.t_source.currentData(), self.t_destination.currentData(),
                [(self.t_product.currentData(),
                  Decimal(str(self.t_quantity.value())))],
                self.t_note.text().strip())
        except Exception as exc:
            # Stay on the form: "not enough stock" is fixed by typing a
            # smaller number, not by starting again.
            self._report(exc)
            return
        finally:
            self.transfer_card.set_busy(False)

        self._close_form()
        self.refresh()
