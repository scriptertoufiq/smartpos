"""Past invoices, with date and customer filters."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from PyQt5.QtCore import QDate, Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from smartpos.config import get_settings
from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.reports import invoice as invoice_report
from smartpos.services.auth_service import AuthService
from smartpos.services.customer_service import CustomerService
from smartpos.services.sale_service import SaleService
from smartpos.services.settings_service import SettingsService

log = get_logger(__name__)

LIST_COLUMNS = ["Invoice", "Date", "Customer", "Outlet", "Total", "Paid", "Status", "By"]
ITEM_COLUMNS = ["SKU", "Product", "Qty", "Price", "Disc", "Line total"]


class SaleListView(QWidget):
    """Past invoices, with filters and reprint."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._sales = SaleService(auth)
        self._customers = CustomerService(auth)
        self._settings = SettingsService(auth)
        self._rows: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        heading = QLabel("Sales")
        heading.setObjectName("viewHeading")
        layout.addWidget(heading)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("From"))
        self.date_from = QDateEdit(QDate.currentDate().addDays(-30))
        self.date_from.setCalendarPopup(True)
        self.date_from.setMinimumHeight(32)
        bar.addWidget(self.date_from)
        bar.addWidget(QLabel("to"))
        self.date_to = QDateEdit(QDate.currentDate())
        self.date_to.setCalendarPopup(True)
        self.date_to.setMinimumHeight(32)
        bar.addWidget(self.date_to)

        self.customer_filter = QComboBox()
        self.customer_filter.setMinimumHeight(32)
        self.customer_filter.setMinimumWidth(180)
        bar.addWidget(self.customer_filter)

        self.status_filter = QComboBox()
        self.status_filter.addItem("Any payment", None)
        for s in ("paid", "partial", "due"):
            self.status_filter.addItem(s.capitalize(), s)
        self.status_filter.setMinimumHeight(32)
        bar.addWidget(self.status_filter)
        bar.addStretch(1)
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
        splitter.setSizes([380, 240])
        layout.addWidget(splitter, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.receipt_button = QPushButton("Reprint receipt")
        self.invoice_button = QPushButton("Save A4 invoice")
        self.collect_button = QPushButton("Collect payment")
        for b in (self.receipt_button, self.invoice_button, self.collect_button):
            b.setMinimumHeight(32)
            b.setEnabled(False)
            actions.addWidget(b)
        actions.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("hintLabel")
        actions.addWidget(self.status)
        layout.addLayout(actions)

        self.date_from.dateChanged.connect(self.refresh)
        self.date_to.dateChanged.connect(self.refresh)
        self.customer_filter.currentIndexChanged.connect(self.refresh)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.receipt_button.clicked.connect(lambda: self._render(thermal=True))
        self.invoice_button.clicked.connect(lambda: self._render(thermal=False))
        self.collect_button.clicked.connect(self._collect)
        self._load_customers()

    # ------------------------------------------------------------- helpers

    def _load_customers(self) -> None:
        self.customer_filter.blockSignals(True)
        self.customer_filter.clear()
        self.customer_filter.addItem("All customers", None)
        try:
            for c in self._customers.search("", limit=300):
                self.customer_filter.addItem(c.name, c.id)
        except Exception:
            log.exception("Could not load customers")
        self.customer_filter.blockSignals(False)

    def _selected(self) -> dict | None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        return self._rows[rows[0].row()] if rows else None

    def _report(self, exc: Exception) -> None:
        if isinstance(exc, SmartPOSError):
            QMessageBox.warning(self, "Cannot do that", str(exc))
        else:
            log.exception("Unexpected error in the sales list")
            QMessageBox.critical(
                self, "Something went wrong",
                "The action failed. The details were written to the log file.")

    # -------------------------------------------------------------- actions

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        try:
            rows = self._sales.list(
                date_from=self.date_from.date().toPyDate(),
                date_to=self.date_to.date().toPyDate(),
                customer_id=self.customer_filter.currentData(),
                limit=500)
            wanted = self.status_filter.currentData()
            if wanted:
                rows = [r for r in rows if r["payment_status"] == wanted]
            self._rows = rows
        except Exception as exc:
            self._report(exc)
            return

        symbol = get_settings().currency_symbol
        self.table.setRowCount(len(rows))
        for row, s in enumerate(rows):
            when = s["sale_date"]
            cells = [s["invoice_no"],
                     when.strftime("%d %b %Y, %H:%M") if when else "—",
                     s["customer_name"], s["warehouse_name"],
                     f"{symbol} {Decimal(str(s['grand_total'])):,.2f}",
                     f"{Decimal(str(s['paid_amount'])):,.2f}",
                     s["payment_status"].capitalize(), s.get("username") or "—"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (4, 5):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif col == 6:
                    item.setTextAlignment(Qt.AlignCenter)
                    if s["payment_status"] == "due":
                        item.setForeground(Qt.darkRed)
                    elif s["payment_status"] == "paid":
                        item.setForeground(Qt.darkGreen)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        total = sum(Decimal(str(s["grand_total"])) for s in rows)
        outstanding = sum(Decimal(str(s["grand_total"])) - Decimal(str(s["paid_amount"]))
                          for s in rows)
        self.status.setText(
            f"{len(rows)} sale(s) · {symbol} {total:,.2f} billed · "
            f"{symbol} {outstanding:,.2f} outstanding")
        self._selection_changed()

    def _selection_changed(self) -> None:
        sale = self._selected()
        chosen = sale is not None
        self.receipt_button.setEnabled(chosen)
        self.invoice_button.setEnabled(chosen)
        self.collect_button.setEnabled(
            chosen and Decimal(str(sale["grand_total"])) > Decimal(str(sale["paid_amount"])))

        if not chosen:
            self.items.setRowCount(0)
            self.items_heading.setText("Lines")
            return

        self.items_heading.setText(f"Lines — {sale['invoice_no']}")
        try:
            rows = self._sales.items_of(sale["id"])
        except Exception:
            log.exception("Could not load sale lines")
            self.items.setRowCount(0)
            return

        self.items.setRowCount(len(rows))
        for r, item_row in enumerate(rows):
            cells = [item_row["sku"], item_row["product_name"],
                     f"{Decimal(str(item_row['quantity'])):,.3f}",
                     f"{Decimal(str(item_row['unit_price'])):,.2f}",
                     f"{Decimal(str(item_row['discount'])):,.2f}",
                     f"{Decimal(str(item_row['line_total'])):,.2f}"]
            for c, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if c >= 2:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.items.setItem(r, c, cell)
        self.items.resizeColumnsToContents()
        self.items.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

    def _render(self, thermal: bool) -> None:
        row = self._selected()
        if row is None:
            return
        suggested = str(Path.home() / f"{row['invoice_no']}.pdf")
        target, _ = QFileDialog.getSaveFileName(
            self, "Save receipt" if thermal else "Save invoice",
            suggested, "PDF (*.pdf)")
        if not target:
            return
        try:
            sale = self._sales.get(row["id"])
            renderer = (invoice_report.render_thermal if thermal
                        else invoice_report.render_a4)
            written = renderer(sale, Path(target),
                               shop=self._settings.shop_profile())
        except Exception as exc:
            self._report(exc)
            return
        QMessageBox.information(self, "Written", f"Saved to\n{written}")

    def _collect(self) -> None:
        sale = self._selected()
        if sale is None:
            return
        outstanding = (Decimal(str(sale["grand_total"]))
                       - Decimal(str(sale["paid_amount"])))
        from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Collect against {sale['invoice_no']}")
        form = QFormLayout(dialog)
        form.setContentsMargins(24, 20, 24, 20)
        symbol = get_settings().currency_symbol
        form.addRow("Customer", QLabel(sale["customer_name"]))
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
            self._sales.record_payment(sale["id"], Decimal(str(amount.value())),
                                       method.currentData())
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()
