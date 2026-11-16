"""The sales counter.

Designed for a queue: the barcode field keeps focus at all times, every action
has a function key, and totals recompute in Decimal on every keystroke.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from PyQt5.QtCore import QEvent, QPoint, QTimer, Qt
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
from smartpos.services.stock_service import StockService
from smartpos.reports import invoice as invoice_report

log = get_logger(__name__)

CART_COLUMNS = ["#", "Product", "Qty", "", "Price", "Disc", "Total"]
STEP_COLUMN = 3                 # the - / + buttons live here
QTY_STEP = Decimal("1")
ZERO = Decimal("0.00")

# Suggestions. Short enough that the list never covers the cart, and slow
# enough that a barcode scanner -- which types a whole code in well under
# 100ms and then presses Enter -- is finished before the first query runs.
SUGGEST_LIMIT = 8
SUGGEST_DELAY_MS = 180
SUGGEST_MIN_CHARS = 2


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
        self._stock = StockService(auth)
        self._lines: list[SaleLine] = []
        self._suggestions: list = []
        self._names: dict[int, str] = {}
        self._resumed_id: int | None = None
        self._busy = False

        root = QHBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(14)
        root.addWidget(self._build_left(), 3)
        root.addWidget(self._build_right(), 1)

        self._build_suggestions()
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
        self.entry.setPlaceholderText(
            "barcode, SKU or name — Enter to add, ↓ to pick from the list")
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
        self.cart.verticalHeader().setDefaultSectionSize(32)   # fits a stepper
        self.cart.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.cart, 1)

        keys = QHBoxLayout()
        keys.setSpacing(6)
        for text, slot in (("F2  Hold", self._hold), ("F3  Recall", self._recall),
                           ("F4  Qty", self._edit_quantity),
                           ("Ctrl \u2212  Less", lambda: self._step_selected(-QTY_STEP)),
                           ("Ctrl +  More", lambda: self._step_selected(QTY_STEP)),
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

    # ---------------------------------------------------------- suggestions

    def _build_suggestions(self) -> None:
        """The list that drops under the search box as the cashier types.

        It is an ordinary child widget, not a Qt popup window. A popup grabs the
        keyboard, and this screen's whole design is that the search box never
        loses focus -- so the list is drawn over the cart instead and steers
        itself from keys the box forwards to it.
        """
        self._suggest = QListWidget(self)
        self._suggest.setObjectName("posSuggest")
        self._suggest.setFocusPolicy(Qt.NoFocus)      # the entry keeps focus
        self._suggest.setSelectionMode(QAbstractItemView.SingleSelection)
        self._suggest.setUniformItemSizes(True)
        self._suggest.hide()
        self._suggest.itemClicked.connect(lambda _item: self._take_suggestion())

        self._suggest_timer = QTimer(self)
        self._suggest_timer.setSingleShot(True)
        self._suggest_timer.setInterval(SUGGEST_DELAY_MS)
        self._suggest_timer.timeout.connect(self._load_suggestions)
        self.entry.textEdited.connect(self._suggest_timer.start)

    def _hide_suggestions(self) -> None:
        self._suggest_timer.stop()
        self._suggestions = []
        self._suggest.clear()
        self._suggest.hide()

    def _load_suggestions(self) -> None:
        """Query the products the typed term matches and show them."""
        term = self.entry.text().strip()
        if len(term) < SUGGEST_MIN_CHARS:
            self._hide_suggestions()
            return
        try:
            matches = self._products.search(term, limit=SUGGEST_LIMIT)
        except Exception:
            # A dead list is a nuisance; a dead till is a disaster. Searching is
            # a convenience, so it fails quietly and Enter still rings up.
            log.exception("Could not load product suggestions")
            self._hide_suggestions()
            return
        if not matches:
            self._hide_suggestions()
            return

        on_hand = self._stock_for(term)
        currency = self._currency()
        self._suggestions = matches
        self._suggest.clear()
        for product in matches:
            bits = [product.name, product.sku]
            bits.append(f"{currency} {Decimal(str(product.sale_price)):,.2f}")
            quantity = on_hand.get(product.id)
            if quantity is not None:
                bits.append(f"{quantity:,.3f}".rstrip("0").rstrip(".") + " in stock")
            item = QListWidgetItem("   ·   ".join(bits))
            item.setData(Qt.UserRole, product.id)
            if quantity is not None and quantity <= 0:
                item.setForeground(Qt.gray)     # sellable only if you allow it
            self._suggest.addItem(item)

        # Nothing is highlighted to begin with, which is what keeps a scan safe:
        # Enter rings up what was typed unless the cashier has arrowed into the
        # list on purpose.
        self._suggest.setCurrentRow(-1)
        self._position_suggestions()
        self._suggest.show()
        self._suggest.raise_()

    def _stock_for(self, term: str) -> dict:
        """product_id -> quantity at this till's outlet, in one query."""
        warehouse_id = self._warehouse_id()
        if warehouse_id is None:
            return {}
        try:
            rows = self._stock.levels(warehouse_id, search=term)
        except Exception:
            log.exception("Could not read stock for the suggestion list")
            return {}
        return {row["product_id"]: Decimal(str(row["quantity"])) for row in rows}

    def _currency(self) -> str:
        try:
            return self._settings.currency()
        except Exception:
            return ""

    def _position_suggestions(self) -> None:
        """Hang the list off the bottom-left of the search box."""
        top_left = self.entry.mapTo(self, QPoint(0, self.entry.height()))
        rows = min(self._suggest.count(), SUGGEST_LIMIT)
        row_height = self._suggest.sizeHintForRow(0) if rows else 24
        height = rows * row_height + 2 * self._suggest.frameWidth()
        height = min(height, max(0, self.height() - top_left.y() - 12))
        self._suggest.setGeometry(top_left.x(), top_left.y(),
                                  self.entry.width(), height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._suggest.isVisible():
            self._position_suggestions()

    def _suggestion_keys(self) -> set:
        """Which keys the list owns right now.

        Enter is on this list only once a row is highlighted. That is the whole
        scanner guarantee: a scan types, no row is highlighted, and its Enter
        goes to the normal barcode path.
        """
        keys = {Qt.Key_Down, Qt.Key_Up, Qt.Key_Escape}
        if self._suggest.currentRow() >= 0:
            keys |= {Qt.Key_Return, Qt.Key_Enter}
        return keys

    def _suggestion_key(self, event) -> bool:
        key = event.key()
        last = self._suggest.count() - 1
        row = self._suggest.currentRow()
        if key == Qt.Key_Down:
            self._suggest.setCurrentRow(min(row + 1, last) if row >= 0 else 0)
            return True
        if key == Qt.Key_Up:
            # Arrowing off the top lets go of the list again, so Enter goes back
            # to meaning "ring up exactly what I typed".
            self._suggest.setCurrentRow(row - 1 if row > 0 else -1)
            return True
        if key == Qt.Key_Escape:
            self._hide_suggestions()
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._take_suggestion()
            return True
        return False

    def _take_suggestion(self) -> None:
        row = self._suggest.currentRow()
        if row < 0:
            row = self._suggest.row(self._suggest.currentItem())
        if not 0 <= row < len(self._suggestions):
            return
        product = self._suggestions[row]
        self._hide_suggestions()
        self._add_product(product)

    def _install_shortcuts(self) -> None:
        # Ctrl is deliberate: a bare + or - is printable, and the event filter
        # sends every printable key to the scanner box by design.
        for sequence, slot in (("Ctrl++", lambda: self._step_selected(QTY_STEP)),
                               ("Ctrl+=", lambda: self._step_selected(QTY_STEP)),
                               ("Ctrl+-", lambda: self._step_selected(-QTY_STEP))):
            QShortcut(QKeySequence(sequence), self, slot)
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
        self._hide_suggestions()
        self.entry.clear()
        self.entry.setFocus(Qt.OtherFocusReason)

    def showEvent(self, event) -> None:
        """Focus the scanner, and start watching for keys that land elsewhere."""
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)
        self._focus_entry()

    def hideEvent(self, event) -> None:
        self._hide_suggestions()
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
        etype = event.type()
        if etype not in (QEvent.KeyPress, QEvent.ShortcutOverride):
            return super().eventFilter(obj, event)
        if not self.isVisible():
            return super().eventFilter(obj, event)
        if not isinstance(obj, QWidget) or obj.window() is not self.window():
            return super().eventFilter(obj, event)

        # While the suggestion list is open it owns the arrow keys, Escape, and
        # -- only once a row is highlighted -- Enter. ShortcutOverride has to be
        # claimed too, or Escape would reach the F-key shortcut and clear the
        # whole bill instead of just closing the list.
        if self._suggest.isVisible() and event.key() in self._suggestion_keys():
            if etype == QEvent.ShortcutOverride:
                event.accept()
                return True
            return self._suggestion_key(event)

        if etype != QEvent.KeyPress:
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
                    # Show them instead of sending the cashier back to the
                    # keyboard. The term is deliberately NOT cleared here --
                    # _focus_entry() would wipe both it and the list.
                    self.message.setText(
                        f"“{term}” matches more than one product — pick one "
                        "from the list with ↓ and Enter, or scan it.")
                    self.message.setStyleSheet("")
                    self._load_suggestions()
                    self.entry.setFocus(Qt.OtherFocusReason)
                    return
                product = matches[0]
        except Exception as exc:
            self._report(exc)
            return

        self._add_product(product)

    def _add_product(self, product) -> None:
        """Put one of `product` in the cart, or bump the line already there."""
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

    def _stepper(self) -> QWidget:
        """The - / + pair that sits in one cart row.

        Two things are deliberate. Neither button takes focus -- the search box
        has to keep it, or the next scan lands on a button instead of in the
        cart. And neither captures its row number: a stepper outlives the rows
        around it, and a captured index would point at the wrong line as soon as
        something above it was removed. The row is looked up when it is clicked.
        """
        holder = QWidget()
        # Qt sizes a cell widget from the row's text height, not the row height,
        # which clipped the buttons; a fixed height overrides that.
        holder.setFixedHeight(26)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(2, 3, 2, 3)
        layout.setSpacing(4)
        for text, delta, tip in (("\u2212", -QTY_STEP, "One fewer (Ctrl\u2212)"),
                                 ("+", QTY_STEP, "One more (Ctrl+)")):
            button = QPushButton(text)
            button.setObjectName("cartStep")
            button.setFocusPolicy(Qt.NoFocus)
            button.setFixedSize(30, 20)
            button.setToolTip(tip)
            button.clicked.connect(
                lambda _checked, b=button, d=delta: self._stepper_clicked(b, d))
            layout.addWidget(button)
        return holder

    def _stepper_clicked(self, button, delta: Decimal) -> None:
        """Work out which row this button belongs to, then move that line."""
        holder = button.parentWidget()
        for row in range(self.cart.rowCount()):
            if self.cart.cellWidget(row, STEP_COLUMN) is holder:
                self._step_quantity(row, delta)
                return

    def _step_quantity(self, row: int, delta: Decimal) -> None:
        """Move one line's quantity by `delta`, removing it when it hits zero."""
        if not 0 <= row < len(self._lines):
            return
        line = self._lines[row]
        wanted = line.quantity + delta
        if wanted <= 0:
            name = self._names.get(line.product_id, "the line")
            del self._lines[row]
            self.message.setText(f"Removed {name}")
        else:
            line.quantity = wanted
            self.message.setText(
                f"{self._names.get(line.product_id, '')} \u00d7 {wanted:,.3f}"
                .replace(".000", ""))
        self.message.setStyleSheet("")
        self._recalculate()
        self._reselect(row)
        self.entry.setFocus(Qt.OtherFocusReason)

    def _reselect(self, row: int) -> None:
        """Keep the same line under the cursor after a redraw."""
        if not self._lines:
            return
        self.cart.selectRow(min(row, len(self._lines) - 1))

    def _step_selected(self, delta: Decimal) -> None:
        """Ctrl+ / Ctrl- act on the selected line, or the one just added."""
        if not self._lines:
            return
        row = self._selected_row()
        if row is None:
            row = len(self._lines) - 1
        self._step_quantity(row, delta)

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
            cells = {0: str(row + 1),
                     1: self._names.get(line.product_id, "?"),
                     2: f"{line.quantity:,.3f}",
                     4: f"{line.unit_price:,.2f}",
                     5: f"{line.discount:,.2f}",
                     6: f"{line.line_total:,.2f}"}
            for col, text in cells.items():
                item = QTableWidgetItem(text)
                if col >= 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif col == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self.cart.setItem(row, col, item)
            if self.cart.cellWidget(row, STEP_COLUMN) is None:
                self.cart.setCellWidget(row, STEP_COLUMN, self._stepper())
        self.cart.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.cart.horizontalHeader().setSectionResizeMode(
            STEP_COLUMN, QHeaderView.Fixed)
        self.cart.setColumnWidth(STEP_COLUMN, 74)

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
