"""The sales counter.

Designed for a queue: the barcode field keeps focus at all times, every action
has a function key, and totals recompute in Decimal on every keystroke.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QComboBox, QDialog,
    QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QShortcut, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from smartpos.config import get_settings
from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.services.auth_service import AuthService
from smartpos.services.customer_service import CustomerService
from smartpos.services.product_service import ProductService
from smartpos.services.sale_service import SaleDraft, SaleLine, SaleService
from smartpos.services.settings_service import SettingsService
from smartpos.reports import invoice as invoice_report

log = get_logger(__name__)

CART_COLUMNS = ["#", "Product", "Qty", "Price", "Disc", "Total"]
ZERO = Decimal("0.00")


class HeldBillsDialog(QDialog):
    """Pick a parked bill to bring back."""

    def __init__(self, parent, held: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("Held bills")
        self.setMinimumSize(460, 320)
        self.chosen: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(QLabel("These bills are parked. None of them hold stock."))

        self.list = QListWidget()
        symbol = get_settings().currency_symbol
        for bill in held:
            when = bill["sale_date"]
            item = QListWidgetItem(
                f"{bill['invoice_no']}   {symbol} "
                f"{Decimal(str(bill['grand_total'])):,.2f}   "
                f"{when.strftime('%H:%M') if when else ''}   "
                f"{bill['customer_name']}")
            item.setData(Qt.UserRole, bill["id"])
            self.list.addItem(item)
        layout.addWidget(self.list, 1)

        buttons = QDialogButtonBox()
        resume = buttons.addButton("Resume", QDialogButtonBox.AcceptRole)
        discard = buttons.addButton("Discard", QDialogButtonBox.DestructiveRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._resume)
        buttons.rejected.connect(self.reject)
        discard.clicked.connect(self._discard)
        resume.setDefault(True)
        layout.addWidget(buttons)
        self.discard_requested = False
        self.list.doubleClicked.connect(self._resume)

    def _selected_id(self) -> int | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _resume(self) -> None:
        self.chosen = self._selected_id()
        if self.chosen is not None:
            self.accept()

    def _discard(self) -> None:
        self.chosen = self._selected_id()
        if self.chosen is None:
            return
        self.discard_requested = True
        self.accept()


class PosView(QWidget):
    """Barcode entry, cart, discount, payment and invoice printing."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._sales = SaleService(auth)
        self._products = ProductService(auth)
        self._customers = CustomerService(auth)
        self._settings = SettingsService(auth)
        self._lines: list[SaleLine] = []
        self._names: dict[int, str] = {}
        self._resumed_id: int | None = None
        self._busy = False

        root = QHBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(14)
        root.addWidget(self._build_left(), 3)
        root.addWidget(self._build_right(), 1)

        self._install_shortcuts()
        self._recalculate()

    # ------------------------------------------------------------- building

    def _build_left(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        label = QLabel("Scan or search")
        label.setObjectName("hintLabel")
        top.addWidget(label)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("barcode, SKU or name — then Enter")
        self.entry.setMinimumHeight(40)
        self.entry.setObjectName("posEntry")
        top.addWidget(self.entry, 1)

        top.addWidget(QLabel("Customer"))
        self.customer = QComboBox()
        self.customer.setMinimumHeight(36)
        self.customer.setMinimumWidth(170)
        top.addWidget(self.customer)
        layout.addLayout(top)

        self.cart = QTableWidget(0, len(CART_COLUMNS))
        self.cart.setHorizontalHeaderLabels(CART_COLUMNS)
        self.cart.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cart.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cart.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cart.verticalHeader().setVisible(False)
        self.cart.setAlternatingRowColors(True)
        self.cart.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.cart, 1)

        keys = QHBoxLayout()
        keys.setSpacing(6)
        for text, slot in (("F2  Hold", self._hold), ("F3  Recall", self._recall),
                           ("F4  Qty", self._edit_quantity),
                           ("Del  Remove", self._remove_line),
                           ("Esc  Clear", self._clear)):
            button = QPushButton(text)
            button.setMinimumHeight(30)
            button.clicked.connect(slot)
            keys.addWidget(button)
        keys.addStretch(1)
        layout.addLayout(keys)
        return panel

    def _build_right(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("posTotals")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        symbol = get_settings().currency_symbol

        grid = QGridLayout()
        grid.setVerticalSpacing(6)
        self._value_labels: dict[str, QLabel] = {}
        for row, key in enumerate(("Subtotal", "Discount", "Tax")):
            name = QLabel(key)
            value = QLabel("0.00")
            value.setAlignment(Qt.AlignRight)
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            self._value_labels[key] = value
        layout.addLayout(grid)

        discount_row = QHBoxLayout()
        self.discount_value = QDoubleSpinBox()
        self.discount_value.setDecimals(2)
        self.discount_value.setMaximum(9_999_999.0)
        self.discount_value.setMinimumHeight(30)
        discount_row.addWidget(self.discount_value, 1)
        self.discount_type = QComboBox()
        self.discount_type.addItem(symbol, "fixed")
        self.discount_type.addItem("%", "percent")
        self.discount_type.setMinimumHeight(30)
        discount_row.addWidget(self.discount_type)
        layout.addLayout(discount_row)

        self.tax = QDoubleSpinBox()
        self.tax.setDecimals(2)
        self.tax.setMaximum(9_999_999.0)
        self.tax.setPrefix(f"Tax {symbol} ")
        self.tax.setMinimumHeight(30)
        layout.addWidget(self.tax)

        rule = QFrame()
        rule.setFrameShape(QFrame.HLine)
        layout.addWidget(rule)

        self.total_label = QLabel("0.00")
        self.total_label.setObjectName("posTotal")
        self.total_label.setAlignment(Qt.AlignRight)
        total_caption = QLabel(f"TOTAL ({symbol})")
        total_caption.setObjectName("hintLabel")
        layout.addWidget(total_caption)
        layout.addWidget(self.total_label)

        self.tendered = QDoubleSpinBox()
        self.tendered.setDecimals(2)
        self.tendered.setMaximum(9_999_999.0)
        self.tendered.setPrefix(f"Paid {symbol} ")
        self.tendered.setMinimumHeight(34)
        layout.addWidget(self.tendered)

        self.change_label = QLabel("Change 0.00")
        self.change_label.setObjectName("posChange")
        self.change_label.setAlignment(Qt.AlignRight)
        layout.addWidget(self.change_label)

        self.method = QComboBox()
        for m in ("cash", "card", "mobile", "bank"):
            self.method.addItem(m.capitalize(), m)
        self.method.setMinimumHeight(30)
        layout.addWidget(self.method)

        layout.addStretch(1)
        self.complete = QPushButton("F12 — Complete sale")
        self.complete.setObjectName("posComplete")
        self.complete.setMinimumHeight(52)
        layout.addWidget(self.complete)

        self.message = QLabel("")
        self.message.setObjectName("hintLabel")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        self.entry.returnPressed.connect(self._add_from_entry)
        self.discount_value.valueChanged.connect(self._recalculate)
        self.discount_type.currentIndexChanged.connect(self._recalculate)
        self.tax.valueChanged.connect(self._recalculate)
        self.tendered.valueChanged.connect(self._update_change)
        self.complete.clicked.connect(self._complete)
        return panel

    def _install_shortcuts(self) -> None:
        for key, slot in ((Qt.Key_F2, self._hold), (Qt.Key_F3, self._recall),
                          (Qt.Key_F4, self._edit_quantity),
                          (Qt.Key_F12, self._complete),
                          (Qt.Key_Escape, self._clear),
                          (Qt.Key_Delete, self._remove_line)):
            QShortcut(QKeySequence(key), self, slot)

    # ------------------------------------------------------------- behaviour

    def refresh(self) -> None:
        """Reload customers and return focus to the scanner."""
        current = self.customer.currentData()
        self.customer.blockSignals(True)
        self.customer.clear()
        try:
            walk_in = self._customers.get_walk_in()
            self.customer.addItem(walk_in.name, walk_in.id)
            for c in self._customers.search("", limit=300):
                if not c.is_walk_in:
                    self.customer.addItem(c.name, c.id)
        except Exception:
            log.exception("Could not load customers")
        if current is not None:
            index = self.customer.findData(current)
            if index >= 0:
                self.customer.setCurrentIndex(index)
        self.customer.blockSignals(False)
        self._focus_entry()

    def _focus_entry(self) -> None:
        """The scanner types here. Nothing else may hold focus for long."""
        self.entry.clear()
        self.entry.setFocus(Qt.OtherFocusReason)

    def showEvent(self, event) -> None:
        """Focus the scanner, and start watching for keys that land elsewhere."""
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)
        self._focus_entry()

    def hideEvent(self, event) -> None:
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, obj, event) -> bool:
        """Make sure a scan is never swallowed by whatever holds focus.

        A barcode scanner is just a keyboard. If the cashier last clicked a
        cart row or a button, that widget takes the keystrokes -- a QTableWidget
        even consumes them for its own type-to-search -- and the item silently
        fails to ring up. That is the worst failure a till can have, so keys are
        caught here, before the focused widget sees them, and sent to the
        barcode field instead.

        Three things are deliberately left alone: anything typed into a real
        input (the tender box, the discount box, the customer picker), any key
        held with a modifier, and anything inside a dialog opened over this
        screen.
        """
        if event.type() != QEvent.KeyPress or not self.isVisible():
            return super().eventFilter(obj, event)
        if not isinstance(obj, QWidget) or obj.window() is not self.window():
            return super().eventFilter(obj, event)

        # Stand aside if the key is going to something that legitimately takes
        # typing. The receiving widget is checked, not just focusWidget(): a
        # spin box delivers to its internal line edit, and focusWidget() is not
        # always reliable.
        if self._is_text_input(obj) or self._is_text_input(QApplication.focusWidget()):
            return super().eventFilter(obj, event)

        text = event.text()
        if not text or not text.isprintable():
            return super().eventFilter(obj, event)
        if event.modifiers() & ~Qt.ShiftModifier:
            return super().eventFilter(obj, event)

        self.entry.setFocus(Qt.OtherFocusReason)
        self.entry.setText(self.entry.text() + text)
        return True

    def _is_text_input(self, widget) -> bool:
        """True if this widget, or anything it sits inside, accepts typing."""
        while isinstance(widget, QWidget) and widget is not self:
            if isinstance(widget, (QLineEdit, QAbstractSpinBox, QComboBox)):
                return True
            widget = widget.parentWidget()
        return False

    def _warehouse_id(self) -> int | None:
        return getattr(self.window(), "active_warehouse_id", None)

    def _report(self, exc: Exception) -> None:
        if isinstance(exc, SmartPOSError):
            self.message.setText(str(exc))
            self.message.setStyleSheet("color: #b91c1c;")
            QMessageBox.warning(self, "Cannot do that", str(exc))
        else:
            log.exception("Unexpected error at the counter")
            QMessageBox.critical(
                self, "Something went wrong",
                "The sale was not saved. The details were written to the log file.")
        self._focus_entry()

    # ---------------------------------------------------------------- cart

    def _add_from_entry(self) -> None:
        term = self.entry.text().strip()
        if not term:
            return
        try:
            product = self._products.get_by_barcode(term)
            if product is None:
                matches = self._products.search(term, limit=2)
                if not matches:
                    self.message.setText(f"Nothing matches “{term}”.")
                    self._focus_entry()
                    return
                if len(matches) > 1:
                    self.message.setText(
                        f"“{term}” matches more than one product — scan it or "
                        "type the full SKU.")
                    self._focus_entry()
                    return
                product = matches[0]
        except Exception as exc:
            self._report(exc)
            return

        for line in self._lines:
            if line.product_id == product.id:
                line.quantity += Decimal("1")
                break
        else:
            self._lines.append(SaleLine(product.id, Decimal("1"),
                                        Decimal(str(product.sale_price))))
            self._names[product.id] = product.name

        self.message.setText(f"Added {product.name}")
        self.message.setStyleSheet("")
        self._recalculate()
        self._focus_entry()

    def _selected_row(self) -> int | None:
        rows = self.cart.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _remove_line(self) -> None:
        row = self._selected_row()
        if row is not None:
            del self._lines[row]
            self._recalculate()
        self._focus_entry()

    def _edit_quantity(self) -> None:
        row = self._selected_row()
        if row is None:
            self.message.setText("Select a line first.")
            return
        line = self._lines[row]
        dialog = QDialog(self)
        dialog.setWindowTitle("Line")
        form = QFormLayout(dialog)
        form.addRow("Product", QLabel(self._names.get(line.product_id, "")))
        quantity = QDoubleSpinBox()
        quantity.setDecimals(3)
        quantity.setMaximum(999_999.0)
        quantity.setValue(float(line.quantity))
        form.addRow("Quantity", quantity)
        discount = QDoubleSpinBox()
        discount.setDecimals(2)
        discount.setMaximum(9_999_999.0)
        discount.setValue(float(line.discount))
        form.addRow("Line discount", discount)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec_() == QDialog.Accepted:
            if quantity.value() <= 0:
                del self._lines[row]
            else:
                line.quantity = Decimal(str(quantity.value()))
                line.discount = Decimal(str(discount.value()))
            self._recalculate()
        self._focus_entry()

    def _clear(self) -> None:
        if self._lines and QMessageBox.question(
            self, "Clear the cart", "Discard every line?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._reset()

    def _reset(self, keep_message: bool = False) -> None:
        self._lines = []
        self._names = {}
        self._resumed_id = None
        self.discount_value.setValue(0)
        self.tax.setValue(0)
        self.tendered.setValue(0)
        if not keep_message:
            self.message.setText("")
            self.message.setStyleSheet("")
        self._recalculate()
        self._focus_entry()

    # --------------------------------------------------------------- totals

    def _draft(self) -> SaleDraft:
        return SaleDraft(
            warehouse_id=self._warehouse_id(),
            customer_id=self.customer.currentData(),
            lines=list(self._lines),
            discount_type=self.discount_type.currentData(),
            discount_value=Decimal(str(self.discount_value.value())),
            tax_amount=Decimal(str(self.tax.value())),
            tendered=Decimal(str(self.tendered.value())),
            payment_method=self.method.currentData())

    def _recalculate(self) -> None:
        self.cart.setRowCount(len(self._lines))
        for row, line in enumerate(self._lines):
            cells = [str(row + 1), self._names.get(line.product_id, "?"),
                     f"{line.quantity:,.3f}", f"{line.unit_price:,.2f}",
                     f"{line.discount:,.2f}", f"{line.line_total:,.2f}"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col >= 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif col == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self.cart.setItem(row, col, item)
        self.cart.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        draft = self._draft()
        self._value_labels["Subtotal"].setText(f"{draft.subtotal:,.2f}")
        self._value_labels["Discount"].setText(f"{draft.discount:,.2f}")
        self._value_labels["Tax"].setText(f"{draft.tax_amount:,.2f}")
        self.total_label.setText(f"{draft.grand_total:,.2f}")
        self.complete.setEnabled(bool(self._lines) and not self._busy)
        self._update_change()

    def _update_change(self) -> None:
        draft = self._draft()
        self.change_label.setText(f"Change {draft.change:,.2f}")
        due = draft.grand_total - draft.applied
        if due > ZERO and self._lines:
            self.change_label.setText(f"Due {due:,.2f}")

    # --------------------------------------------------------------- actions

    def _hold(self) -> None:
        if not self._lines:
            return
        try:
            if self._resumed_id is not None:
                self._sales.cancel_held(self._resumed_id)
            self._sales.hold(self._draft())
        except Exception as exc:
            self._report(exc)
            return
        self._reset()
        self.message.setText("Bill held. Recall it with F3.")

    def _recall(self) -> None:
        try:
            held = self._sales.list_held(self._warehouse_id())
        except Exception as exc:
            self._report(exc)
            return
        if not held:
            self.message.setText("No held bills.")
            return

        dialog = HeldBillsDialog(self, held)
        if dialog.exec_() != QDialog.Accepted or dialog.chosen is None:
            self._focus_entry()
            return
        try:
            if dialog.discard_requested:
                self._sales.cancel_held(dialog.chosen)
                self.message.setText("Held bill discarded.")
                self._focus_entry()
                return
            sale = self._sales.resume(dialog.chosen)
        except Exception as exc:
            self._report(exc)
            return

        self._lines = [SaleLine(i["product_id"], Decimal(str(i["quantity"])),
                                Decimal(str(i["unit_price"])),
                                Decimal(str(i["discount"])))
                       for i in sale["items"]]
        self._names = {i["product_id"]: i["product_name"] for i in sale["items"]}
        self._resumed_id = sale["id"]
        self.message.setText(f"Resumed {sale['invoice_no']}.")
        self._recalculate()
        self._focus_entry()

    def _complete(self) -> None:
        if not self._lines or self._busy:
            return
        if self._warehouse_id() is None:
            QMessageBox.warning(self, "No outlet",
                                "Choose the outlet you are selling from, in the "
                                "status bar.")
            return

        # Disabled while in flight: a double press must not produce two invoices.
        self._busy = True
        self.complete.setEnabled(False)
        self.complete.setText("Saving…")
        try:
            if self._resumed_id is not None:
                self._sales.cancel_held(self._resumed_id)
            sale_id = self._sales.create(self._draft())
        except Exception as exc:
            self._report(exc)
            return
        finally:
            self._busy = False
            self.complete.setText("F12 — Complete sale")
            self.complete.setEnabled(bool(self._lines))

        sale = self._sales.get(sale_id)
        change = Decimal(str(sale["change_amount"]))
        summary = f"{sale['invoice_no']} saved"
        if change > ZERO:
            summary += f"  ·  CHANGE {change:,.2f}"

        try:
            path = Path.home() / "SmartPOS-invoices" / f"{sale['invoice_no']}.pdf"
            # The shop's own name and footer, as set in Settings.
            invoice_report.render_thermal(
                sale, path, shop=self._settings.shop_profile())
        except Exception:
            log.exception("The sale saved but the receipt did not render")
            summary += "  ·  receipt could not be written"

        # Clear first, then report: _reset() would otherwise wipe the one line
        # the cashier has to read before serving the next customer.
        self._reset()
        self.message.setText(summary)
        self.message.setStyleSheet("color: #047857; font-weight: 600;")
