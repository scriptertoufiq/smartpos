"""Record a purchase from a supplier into a warehouse."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from smartpos.config import get_settings
from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.services.auth_service import AuthService
from smartpos.services.product_service import ProductService
from smartpos.services.purchase_service import PurchaseDraft, PurchaseLine, PurchaseService
from smartpos.services.supplier_service import SupplierService
from smartpos.services.warehouse_service import WarehouseService

log = get_logger(__name__)

LIST_COLUMNS = ["Reference", "Date", "Supplier", "Outlet", "Total", "Paid", "Status"]
ITEM_COLUMNS = ["SKU", "Product", "Quantity", "Unit cost", "Discount", "Tax", "Line total"]
CART_COLUMNS = ["Product", "Quantity", "Unit cost", "Line total"]


class PurchaseEntryDialog(QDialog):
    """Build a purchase: supplier, outlet, lines, totals."""

    def __init__(self, parent, suppliers, warehouses, products,
                 default_warehouse=None, reference: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"New purchase  {reference}".strip())
        self.setMinimumSize(720, 560)
        self._products = {p.id: p for p in products}
        self._lines: list[PurchaseLine] = []
        symbol = get_settings().currency_symbol

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)

        head = QFormLayout()
        head.setSpacing(8)
        self.supplier = QComboBox()
        for s in suppliers:
            self.supplier.addItem(s.name, s.id)
        head.addRow("Supplier", self.supplier)

        self.warehouse = QComboBox()
        for w in warehouses:
            self.warehouse.addItem(w.name, w.id)
        if default_warehouse is not None:
            index = self.warehouse.findData(default_warehouse)
            if index >= 0:
                self.warehouse.setCurrentIndex(index)
        head.addRow("Goods land in", self.warehouse)
        layout.addLayout(head)

        picker = QHBoxLayout()
        picker.setSpacing(6)
        self.product = QComboBox()
        self.product.setMinimumWidth(240)
        for p in products:
            self.product.addItem(f"{p.name} ({p.sku})", p.id)
        picker.addWidget(self.product, 1)

        self.quantity = QDoubleSpinBox()
        self.quantity.setDecimals(3)
        self.quantity.setMaximum(999_999.0)
        self.quantity.setValue(1)
        self.quantity.setPrefix("qty ")
        picker.addWidget(self.quantity)

        self.unit_cost = QDoubleSpinBox()
        self.unit_cost.setDecimals(2)
        self.unit_cost.setMaximum(9_999_999.0)
        self.unit_cost.setPrefix(f"{symbol} ")
        picker.addWidget(self.unit_cost)

        add = QPushButton("Add line")
        add.setProperty("primary", True)
        picker.addWidget(add)
        layout.addLayout(picker)

        self.cart = QTableWidget(0, len(CART_COLUMNS))
        self.cart.setHorizontalHeaderLabels(CART_COLUMNS)
        self.cart.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cart.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cart.verticalHeader().setVisible(False)
        self.cart.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.cart, 1)

        remove = QPushButton("Remove selected line")
        remove.setMaximumWidth(200)
        layout.addWidget(remove)

        totals = QFormLayout()
        totals.setSpacing(8)
        self.discount = QDoubleSpinBox()
        self.discount.setDecimals(2)
        self.discount.setMaximum(9_999_999.0)
        totals.addRow(f"Discount ({symbol})", self.discount)
        self.tax = QDoubleSpinBox()
        self.tax.setDecimals(2)
        self.tax.setMaximum(9_999_999.0)
        totals.addRow(f"Tax ({symbol})", self.tax)
        self.paid = QDoubleSpinBox()
        self.paid.setDecimals(2)
        self.paid.setMaximum(9_999_999.0)
        totals.addRow(f"Paid now ({symbol})", self.paid)
        self.method = QComboBox()
        for m in ("cash", "card", "mobile", "bank"):
            self.method.addItem(m.capitalize(), m)
        totals.addRow("Method", self.method)
        self.note = QLineEdit()
        totals.addRow("Note", self.note)
        layout.addLayout(totals)

        self.total_label = QLabel()
        self.total_label.setObjectName("balanceStrong")
        self.total_label.setAlignment(Qt.AlignRight)
        layout.addWidget(self.total_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Record purchase")
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        add.clicked.connect(self._add_line)
        remove.clicked.connect(self._remove_line)
        self.product.currentIndexChanged.connect(self._suggest_cost)
        for box in (self.discount, self.tax):
            box.valueChanged.connect(self._recalculate)
        self._suggest_cost()
        self._recalculate()

    def _suggest_cost(self) -> None:
        product = self._products.get(self.product.currentData())
        if product is not None and product.purchase_price:
            self.unit_cost.setValue(float(product.purchase_price))

    def _add_line(self) -> None:
        product_id = self.product.currentData()
        if product_id is None:
            return
        quantity = Decimal(str(self.quantity.value()))
        if quantity <= 0:
            QMessageBox.warning(self, "Nothing to add", "Enter a quantity.")
            return
        for line in self._lines:
            if line.product_id == product_id:
                QMessageBox.warning(
                    self, "Already on the purchase",
                    "That product is already a line. Remove it first to change it.")
                return
        self._lines.append(PurchaseLine(product_id, quantity,
                                        Decimal(str(self.unit_cost.value()))))
        self._recalculate()

    def _remove_line(self) -> None:
        rows = self.cart.selectionModel().selectedRows()
        if rows:
            del self._lines[rows[0].row()]
            self._recalculate()

    def _recalculate(self) -> None:
        self.cart.setRowCount(len(self._lines))
        for row, line in enumerate(self._lines):
            product = self._products.get(line.product_id)
            cells = [product.name if product else f"#{line.product_id}",
                     f"{line.quantity:,.3f}", f"{line.unit_cost:,.2f}",
                     f"{line.line_total:,.2f}"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.cart.setItem(row, col, item)

        draft = self._draft()
        symbol = get_settings().currency_symbol
        self.total_label.setText(
            f"Subtotal {symbol} {draft.subtotal:,.2f}     "
            f"Total {symbol} {draft.grand_total:,.2f}")
        self.paid.setMaximum(float(max(draft.grand_total, Decimal("0"))))

    def _draft(self) -> PurchaseDraft:
        return PurchaseDraft(
            supplier_id=self.supplier.currentData(),
            warehouse_id=self.warehouse.currentData(),
            lines=list(self._lines),
            discount=Decimal(str(self.discount.value())),
            tax_amount=Decimal(str(self.tax.value())),
            paid_amount=Decimal(str(self.paid.value())),
            payment_method=self.method.currentData(),
            note=self.note.text().strip() or None)

    def _check(self) -> None:
        if not self._lines:
            QMessageBox.warning(self, "Nothing to record",
                                "Add at least one product.")
            return
        self.accept()

    def draft(self) -> PurchaseDraft:
        return self._draft()


class PurchaseView(QWidget):
    """Record goods received from a supplier, and review past purchases."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._service = PurchaseService(auth)
        self._suppliers = SupplierService(auth)
        self._warehouses = WarehouseService(auth)
        self._products = ProductService(auth)
        self._rows: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        heading = QLabel("Purchases")
        heading.setObjectName("viewHeading")
        layout.addWidget(heading)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.supplier_filter = QComboBox()
        self.supplier_filter.setMinimumHeight(34)
        self.supplier_filter.setMinimumWidth(190)
        bar.addWidget(self.supplier_filter)
        self.status_filter = QComboBox()
        self.status_filter.addItem("Any status", None)
        for s in ("due", "partial", "paid"):
            self.status_filter.addItem(s.capitalize(), s)
        self.status_filter.setMinimumHeight(34)
        bar.addWidget(self.status_filter)
        bar.addStretch(1)
        self.add_button = QPushButton("New purchase")
        self.add_button.setProperty("primary", True)
        self.add_button.setMinimumHeight(34)
        bar.addWidget(self.add_button)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(LIST_COLUMNS))
        self.table.setHorizontalHeaderLabels(LIST_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        splitter.addWidget(self.table)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 8, 0, 0)
        panel_layout.setSpacing(6)
        self.items_heading = QLabel("Lines")
        self.items_heading.setObjectName("panelHeading")
        panel_layout.addWidget(self.items_heading)
        self.items = QTableWidget(0, len(ITEM_COLUMNS))
        self.items.setHorizontalHeaderLabels(ITEM_COLUMNS)
        self.items.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.items.verticalHeader().setVisible(False)
        self.items.setAlternatingRowColors(True)
        self.items.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        panel_layout.addWidget(self.items)
        splitter.addWidget(panel)
        splitter.setSizes([380, 260])
        layout.addWidget(splitter, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.pay_button = QPushButton("Record payment")
        self.pay_button.setMinimumHeight(32)
        self.pay_button.setEnabled(False)
        actions.addWidget(self.pay_button)
        actions.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("hintLabel")
        actions.addWidget(self.status)
        layout.addLayout(actions)

        self.supplier_filter.currentIndexChanged.connect(self.refresh)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.add_button.clicked.connect(self._create)
        self.pay_button.clicked.connect(self._pay)
        self.table.itemSelectionChanged.connect(self._selection_changed)

        writable = auth.can("purchase.manage")
        self.add_button.setVisible(writable)
        self.pay_button.setVisible(writable)
        self._load_suppliers()

    # ------------------------------------------------------------- helpers

    def _load_suppliers(self) -> None:
        self.supplier_filter.blockSignals(True)
        self.supplier_filter.clear()
        self.supplier_filter.addItem("All suppliers", None)
        try:
            for s in self._suppliers.search("", limit=500):
                self.supplier_filter.addItem(s.name, s.id)
        except Exception:
            log.exception("Could not load suppliers")
        self.supplier_filter.blockSignals(False)

    def _selected(self) -> dict | None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        return self._rows[rows[0].row()] if rows else None

    def _report(self, exc: Exception) -> None:
        if isinstance(exc, SmartPOSError):
            QMessageBox.warning(self, "Cannot do that", str(exc))
        else:
            log.exception("Unexpected error in the purchase screen")
            QMessageBox.critical(
                self, "Something went wrong",
                "The action failed. The details were written to the log file.")

    # -------------------------------------------------------------- actions

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        try:
            self._rows = self._service.list(
                supplier_id=self.supplier_filter.currentData(),
                payment_status=self.status_filter.currentData())
        except Exception as exc:
            self._report(exc)
            return

        symbol = get_settings().currency_symbol
        self.table.setRowCount(len(self._rows))
        for row, p in enumerate(self._rows):
            when = p["purchase_date"]
            cells = [p["reference_no"],
                     when.strftime("%d %b %Y") if when else "—",
                     p["supplier_name"], p["warehouse_name"],
                     f"{symbol} {Decimal(str(p['grand_total'])):,.2f}",
                     f"{Decimal(str(p['paid_amount'])):,.2f}",
                     p["payment_status"].capitalize()]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (4, 5):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif col == 6:
                    item.setTextAlignment(Qt.AlignCenter)
                    if p["payment_status"] == "due":
                        item.setForeground(Qt.darkRed)
                    elif p["payment_status"] == "paid":
                        item.setForeground(Qt.darkGreen)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        outstanding = sum(
            (Decimal(str(p["grand_total"])) - Decimal(str(p["paid_amount"])))
            for p in self._rows)
        self.status.setText(
            f"{len(self._rows)} purchase(s) · {symbol} {outstanding:,.2f} still to pay")
        self._selection_changed()

    def _selection_changed(self) -> None:
        purchase = self._selected()
        if purchase is None:
            self.items.setRowCount(0)
            self.items_heading.setText("Lines")
            self.pay_button.setEnabled(False)
            return

        outstanding = (Decimal(str(purchase["grand_total"]))
                       - Decimal(str(purchase["paid_amount"])))
        self.pay_button.setEnabled(outstanding > 0)
        self.items_heading.setText(f"Lines — {purchase['reference_no']}")

        try:
            rows = self._service.items_of(purchase["id"])
        except Exception:
            log.exception("Could not load purchase lines")
            self.items.setRowCount(0)
            return

        self.items.setRowCount(len(rows))
        for r, item_row in enumerate(rows):
            cells = [item_row["sku"], item_row["product_name"],
                     f"{Decimal(str(item_row['quantity'])):,.3f}",
                     f"{Decimal(str(item_row['unit_cost'])):,.2f}",
                     f"{Decimal(str(item_row['discount'])):,.2f}",
                     f"{Decimal(str(item_row['tax_amount'])):,.2f}",
                     f"{Decimal(str(item_row['line_total'])):,.2f}"]
            for c, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if c >= 2:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.items.setItem(r, c, cell)
        self.items.resizeColumnsToContents()
        self.items.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

    def _create(self) -> None:
        try:
            suppliers = self._suppliers.search("", limit=500)
            warehouses = self._warehouses.list_all(active_only=True)
            products = self._products.search("", limit=500)
        except Exception as exc:
            self._report(exc)
            return

        if not suppliers:
            QMessageBox.information(self, "No suppliers",
                                    "Add a supplier before recording a purchase.")
            return
        if not products:
            QMessageBox.information(self, "No products",
                                    "Add a product before recording a purchase.")
            return

        default = getattr(self.window(), "active_warehouse_id", None)
        dialog = PurchaseEntryDialog(self, suppliers, warehouses, products,
                                     default, self._service.next_reference_no())
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            self._service.create(dialog.draft())
        except Exception as exc:
            self._report(exc)
            return
        self._load_suppliers()
        self.refresh()

    def _pay(self) -> None:
        purchase = self._selected()
        if purchase is None:
            return
        outstanding = (Decimal(str(purchase["grand_total"]))
                       - Decimal(str(purchase["paid_amount"])))

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Pay {purchase['reference_no']}")
        form = QFormLayout(dialog)
        form.setContentsMargins(24, 20, 24, 20)
        symbol = get_settings().currency_symbol
        form.addRow("Supplier", QLabel(purchase["supplier_name"]))
        form.addRow("Outstanding", QLabel(f"{symbol} {outstanding:,.2f}"))
        amount = QDoubleSpinBox()
        amount.setDecimals(2)
        amount.setMaximum(float(outstanding))
        amount.setValue(float(outstanding))
        amount.setMinimumHeight(32)
        form.addRow(f"Amount ({symbol})", amount)
        method = QComboBox()
        for m in ("cash", "card", "mobile", "bank"):
            method.addItem(m.capitalize(), m)
        form.addRow("Method", method)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            self._service.record_payment(purchase["id"],
                                         Decimal(str(amount.value())),
                                         method.currentData())
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()
