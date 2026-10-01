"""Create a return against an invoice number, for the whole invoice or
selected lines."""

from __future__ import annotations

from decimal import Decimal

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from smartpos.config import get_settings
from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.services.auth_service import AuthService
from smartpos.services.sale_return_service import (
    REFUND_CASH, REFUND_DUE, ReturnDraft, ReturnLine, SaleReturnService,
)
from smartpos.services.sale_service import SaleService

log = get_logger(__name__)

LINE_COLUMNS = ["Product", "Sold", "Already back", "Can return", "Returning", "Refund"]
HISTORY_COLUMNS = ["Reference", "Date", "Invoice", "Customer", "Amount", "Refund", "By"]
ZERO = Decimal("0.00")


class SaleReturnView(QWidget):
    """Look up an invoice, choose lines, take the goods back."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._returns = SaleReturnService(auth)
        self._sales = SaleService(auth)
        self._sale: dict | None = None
        self._lines: list = []
        self._spins: list[QDoubleSpinBox] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        heading = QLabel("Sale returns")
        heading.setObjectName("viewHeading")
        layout.addWidget(heading)

        blurb = QLabel("A completed sale is never edited. Goods come back "
                       "through a return, which puts them on the shelf and "
                       "settles the money.")
        blurb.setObjectName("hintLabel")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        lookup = QHBoxLayout()
        lookup.setSpacing(8)
        lookup.addWidget(QLabel("Invoice"))
        self.invoice = QLineEdit()
        self.invoice.setPlaceholderText("INV-2026-000042 — or scan the receipt")
        self.invoice.setMinimumHeight(36)
        lookup.addWidget(self.invoice, 1)
        self.find_button = QPushButton("Find")
        self.find_button.setMinimumHeight(36)
        lookup.addWidget(self.find_button)
        layout.addLayout(lookup)

        self.summary = QLabel("Enter an invoice number to begin.")
        self.summary.setObjectName("hintLabel")
        layout.addWidget(self.summary)

        splitter = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(LINE_COLUMNS))
        self.table.setHorizontalHeaderLabels(LINE_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        splitter.addWidget(self.table)

        history_panel = QWidget()
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(0, 8, 0, 0)
        history_layout.setSpacing(6)
        self.history_heading = QLabel("Recent returns")
        self.history_heading.setObjectName("panelHeading")
        history_layout.addWidget(self.history_heading)
        self.history = QTableWidget(0, len(HISTORY_COLUMNS))
        self.history.setHorizontalHeaderLabels(HISTORY_COLUMNS)
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history.verticalHeader().setVisible(False)
        self.history.setAlternatingRowColors(True)
        self.history.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        history_layout.addWidget(self.history)
        splitter.addWidget(history_panel)
        splitter.setSizes([360, 240])
        layout.addWidget(splitter, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.return_all = QPushButton("Return everything")
        self.return_all.setMinimumHeight(34)
        footer.addWidget(self.return_all)

        footer.addWidget(QLabel("Refund by"))
        self.refund_method = QComboBox()
        self.refund_method.addItem("Cash", REFUND_CASH)
        self.refund_method.addItem("Reduce what they owe", REFUND_DUE)
        self.refund_method.setMinimumHeight(34)
        footer.addWidget(self.refund_method)

        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Reason (optional)")
        self.reason.setMinimumHeight(34)
        footer.addWidget(self.reason, 1)

        self.total_label = QLabel("0.00")
        self.total_label.setObjectName("balanceStrong")
        footer.addWidget(self.total_label)

        self.submit = QPushButton("Process return")
        self.submit.setProperty("primary", True)
        self.submit.setMinimumHeight(38)
        self.submit.setEnabled(False)
        footer.addWidget(self.submit)
        layout.addLayout(footer)

        self.invoice.returnPressed.connect(self._find)
        self.find_button.clicked.connect(self._find)
        self.return_all.clicked.connect(self._fill_all)
        self.submit.clicked.connect(self._process)

        writable = auth.can("sale_return.create")
        for widget in (self.return_all, self.submit, self.refund_method, self.reason):
            widget.setVisible(writable)

    # ------------------------------------------------------------- helpers

    def _report(self, exc: Exception) -> None:
        if isinstance(exc, SmartPOSError):
            QMessageBox.warning(self, "Cannot do that", str(exc))
        else:
            log.exception("Unexpected error in the returns screen")
            QMessageBox.critical(
                self, "Something went wrong",
                "The action failed. The details were written to the log file.")

    def _clear_lines(self) -> None:
        self._sale = None
        self._lines = []
        self._spins = []
        self.table.setRowCount(0)
        self.submit.setEnabled(False)
        self.total_label.setText("0.00")

    # -------------------------------------------------------------- actions

    def refresh(self) -> None:
        """Reload the recent returns list."""
        try:
            rows = self._returns.list(limit=100)
        except Exception as exc:
            self._report(exc)
            return

        symbol = get_settings().currency_symbol
        self.history.setRowCount(len(rows))
        for r, row in enumerate(rows):
            when = row["return_date"]
            cells = [row["reference_no"],
                     when.strftime("%d %b %Y, %H:%M") if when else "—",
                     row["invoice_no"], row["customer_name"],
                     f"{symbol} {Decimal(str(row['total_amount'])):,.2f}",
                     "Cash" if row["refund_method"] == REFUND_CASH else "Off the due",
                     row.get("username") or "—"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 4:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.history.setItem(r, c, item)
        self.history.resizeColumnsToContents()
        self.history.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

    def _find(self) -> None:
        reference = self.invoice.text().strip()
        if not reference:
            return
        self._clear_lines()
        try:
            sale = self._sales.get_by_invoice(reference)
            if sale is None:
                self.summary.setText(f"No sale found for “{reference}”.")
                return
            lines = self._returns.returnable_quantities(sale["id"])
        except Exception as exc:
            self._report(exc)
            return

        self._sale = sale
        self._lines = lines
        symbol = get_settings().currency_symbol
        when = sale["sale_date"]
        already = self._returns.total_returned(sale["id"])
        self.summary.setText(
            f"{sale['invoice_no']} · {sale['customer_name']} · "
            f"{when.strftime('%d %b %Y, %H:%M') if when else ''} · "
            f"billed {symbol} {Decimal(str(sale['grand_total'])):,.2f}"
            + (f" · {symbol} {already:,.2f} already returned" if already else ""))

        self.table.setRowCount(len(lines))
        self._spins = []
        for row, line in enumerate(lines):
            cells = [line.product_name,
                     f"{line.sold.normalize():f}",
                     f"{line.already_returned.normalize():f}",
                     f"{line.returnable.normalize():f}"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, col, item)

            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setMaximum(float(line.returnable))
            spin.setEnabled(line.returnable > 0)
            spin.valueChanged.connect(self._recalculate)
            self._spins.append(spin)
            self.table.setCellWidget(row, 4, spin)

            refund = QTableWidgetItem("0.00")
            refund.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 5, refund)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._recalculate()

    def _fill_all(self) -> None:
        for spin, line in zip(self._spins, self._lines):
            spin.setValue(float(line.returnable))

    def _recalculate(self) -> None:
        total = ZERO
        for row, (spin, line) in enumerate(zip(self._spins, self._lines)):
            quantity = Decimal(str(spin.value()))
            amount = (quantity * line.unit_price).quantize(Decimal("0.01"))
            total += amount
            cell = self.table.item(row, 5)
            if cell is not None:
                cell.setText(f"{amount:,.2f}")
        self.total_label.setText(f"{total:,.2f}")
        self.submit.setEnabled(total > ZERO and self._auth.can("sale_return.create"))

    def _process(self) -> None:
        if self._sale is None:
            return
        lines = [ReturnLine(line.sale_item_id, Decimal(str(spin.value())))
                 for spin, line in zip(self._spins, self._lines)
                 if spin.value() > 0]
        if not lines:
            return

        if QMessageBox.question(
            self, "Process return",
            f"Take back {len(lines)} line(s) from {self._sale['invoice_no']} "
            f"and refund {self.total_label.text()}?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return

        try:
            return_id = self._returns.create(ReturnDraft(
                sale_id=self._sale["id"], lines=lines,
                refund_method=self.refund_method.currentData(),
                reason=self.reason.text().strip() or None))
            saved = self._returns.get(return_id)
        except Exception as exc:
            self._report(exc)
            return

        QMessageBox.information(
            self, "Return processed",
            f"{saved['reference_no']} recorded.\n"
            f"The goods are back in {saved['warehouse_name']}.")
        self.reason.clear()
        self._find()          # reload so the new maxima show
        self.refresh()
