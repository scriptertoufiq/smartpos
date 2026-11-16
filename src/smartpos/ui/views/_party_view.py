"""Shared screen for customers and suppliers.

The two differ only in wording and in which direction their money runs, so the
layout, the ledger panel and the payment form live here once. Each subclass
supplies its own labels and service calls.

The list and the create/edit form are two pages of one screen; see
`smartpos.ui.widgets.form_page` for why there is no dialog here.
"""

from __future__ import annotations

from decimal import Decimal

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from smartpos.config import get_settings
from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.services.auth_service import AuthService
from smartpos.ui.widgets.form_page import (
    PAGE_FORM, PAGE_LIST, FormCard, FormPage,
)

log = get_logger(__name__)

METHODS = ("cash", "card", "mobile", "bank")
LEDGER_COLUMNS = ["Date", "Kind", "Reference", "Charged", "Settled"]


class PaymentDialog(QDialog):
    """Take money in from a customer, or pay it out to a supplier."""

    def __init__(self, parent=None, *, title: str, party_name: str,
                 outstanding: Decimal, prompt: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        symbol = get_settings().currency_symbol

        form = QFormLayout(self)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(10)

        form.addRow("Party", QLabel(party_name))

        outstanding_label = QLabel(f"{symbol} {outstanding:,.2f}")
        outstanding_label.setObjectName("balanceStrong")
        form.addRow(prompt, outstanding_label)

        self.amount = QDoubleSpinBox()
        self.amount.setDecimals(2)
        self.amount.setMaximum(float(max(outstanding, Decimal("0"))))
        self.amount.setGroupSeparatorShown(True)
        self.amount.setValue(float(max(outstanding, Decimal("0"))))
        self.amount.setMinimumHeight(32)
        form.addRow(f"Amount ({symbol})", self.amount)

        self.method = QComboBox()
        for m in METHODS:
            self.method.addItem(m.capitalize(), m)
        self.method.setMinimumHeight(30)
        form.addRow("Method", self.method)

        self.note = QLineEdit()
        self.note.setMaxLength(255)
        form.addRow("Note", self.note)

        hint = QLabel("The amount is capped at what is outstanding. Anything "
                      "more would be money the books cannot account for.")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        form.addRow("", hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        return {"amount": Decimal(str(self.amount.value())),
                "method": self.method.currentData(),
                "note": self.note.text().strip()}


class PartyView(QWidget):
    """Base screen. Subclasses set the wording and wire their own service."""

    noun = "party"
    plural = "Parties"
    blurb = ""
    balance_heading = "Balance"
    payment_verb = "Record payment"
    payment_prompt = "Outstanding"
    manage_permission = "customer.manage"

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._parties: list = []
        self._balances: dict[int, Decimal] = {}

        self._editing = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(12)

        self.heading = QLabel(self.plural)
        self.heading.setObjectName("viewHeading")
        outer.addWidget(self.heading)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_list(auth))     # PAGE_LIST
        self.pages.addWidget(self._build_form())         # PAGE_FORM
        outer.addWidget(self.pages, 1)

    # -------------------------------------------------------- the list page

    def _build_list(self, auth: AuthService) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        if self.blurb:
            note = QLabel(self.blurb)
            note.setObjectName("hintLabel")
            note.setWordWrap(True)
            layout.addWidget(note)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, phone or email…")
        self.search.setMinimumHeight(34)
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search, 1)
        self.show_disabled = QCheckBox("Show disabled")
        bar.addWidget(self.show_disabled)
        self.add_button = QPushButton(f"New {self.noun}")
        self.add_button.setProperty("primary", True)
        self.add_button.setMinimumHeight(34)
        bar.addWidget(self.add_button)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Vertical)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Phone", "Address", "Opening", self.balance_heading, "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        splitter.addWidget(self.table)

        ledger_panel = QWidget()
        ledger_layout = QVBoxLayout(ledger_panel)
        ledger_layout.setContentsMargins(0, 8, 0, 0)
        ledger_layout.setSpacing(6)
        self.ledger_heading = QLabel("Ledger")
        self.ledger_heading.setObjectName("panelHeading")
        ledger_layout.addWidget(self.ledger_heading)
        self.ledger = QTableWidget(0, len(LEDGER_COLUMNS))
        self.ledger.setHorizontalHeaderLabels(LEDGER_COLUMNS)
        self.ledger.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ledger.verticalHeader().setVisible(False)
        self.ledger.setAlternatingRowColors(True)
        self.ledger.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        ledger_layout.addWidget(self.ledger)
        splitter.addWidget(ledger_panel)
        splitter.setSizes([420, 240])
        layout.addWidget(splitter, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.edit_button = QPushButton("Edit")
        self.payment_button = QPushButton(self.payment_verb)
        self.payment_button.setProperty("primary", True)
        self.toggle_button = QPushButton("Disable")
        self.delete_button = QPushButton("Delete")
        self.delete_button.setProperty("danger", True)
        for b in (self.edit_button, self.payment_button,
                  self.toggle_button, self.delete_button):
            b.setMinimumHeight(32)
            b.setEnabled(False)
            actions.addWidget(b)
        actions.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("hintLabel")
        actions.addWidget(self.status)
        layout.addLayout(actions)

        self.search.textChanged.connect(self.refresh)
        self.show_disabled.toggled.connect(self.refresh)
        self.add_button.clicked.connect(self._create)
        self.edit_button.clicked.connect(self._edit)
        self.payment_button.clicked.connect(self._take_payment)
        self.toggle_button.clicked.connect(self._toggle)
        self.delete_button.clicked.connect(self._delete)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(self._edit)

        writable = auth.can(self.manage_permission)
        for b in (self.add_button, self.edit_button, self.payment_button,
                  self.toggle_button, self.delete_button):
            b.setVisible(writable)
        return page

    # -------------------------------------------------------- the form page

    def _build_form(self) -> QWidget:
        card = FormCard(save_text=f"Save {self.noun}")
        self.card = card

        self.f_name = card.add_row("Name", QLineEdit())
        self.f_name.setMaxLength(120)
        self.f_phone = card.add_row("Phone", QLineEdit())
        self.f_phone.setMaxLength(30)
        self.f_email = card.add_row("Email", QLineEdit())
        self.f_email.setMaxLength(120)
        self.f_address = card.add_row("Address", QLineEdit())
        self.f_address.setMaxLength(255)

        self.f_opening = QDoubleSpinBox()
        self.f_opening.setDecimals(2)
        self.f_opening.setRange(-99_999_999.0, 99_999_999.0)
        self.f_opening.setGroupSeparatorShown(True)
        card.add_row(f"Opening balance ({get_settings().currency_symbol})",
                     self.f_opening)

        card.set_hint("The opening balance is what was already owed when the "
                      "shop started using SmartPOS. Leave it at zero for "
                      "someone new.")
        card.save_clicked.connect(self._save)
        card.cancel_clicked.connect(self._close_form)
        for field in (self.f_name, self.f_phone, self.f_email, self.f_address):
            field.returnPressed.connect(self._save)
        return FormPage(card)

    # ------------------------------------------------- subclass responsibility

    def _service(self):
        raise NotImplementedError

    def _balance_of(self, party_id: int) -> Decimal:
        raise NotImplementedError

    def _record_payment(self, party_id: int, values: dict) -> None:
        raise NotImplementedError

    def _is_protected(self, party) -> bool:
        return False

    # ------------------------------------------------------------- behaviour

    def _selected(self):
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        return self._parties[rows[0].row()] if rows else None

    def _report(self, exc: Exception) -> None:
        if isinstance(exc, SmartPOSError):
            QMessageBox.warning(self, "Cannot do that", str(exc))
        else:
            log.exception("Unexpected error in the %s screen", self.noun)
            QMessageBox.critical(
                self, "Something went wrong",
                "The action failed. The details were written to the log file.")

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        try:
            self._parties = self._service().search(
                self.search.text().strip(), limit=300,
                active_only=not self.show_disabled.isChecked())
            self._balances = {p.id: self._balance_of(p.id) for p in self._parties}
        except Exception as exc:
            self._report(exc)
            return

        symbol = get_settings().currency_symbol
        self.table.setRowCount(len(self._parties))
        for row, p in enumerate(self._parties):
            balance = self._balances.get(p.id, Decimal("0.00"))
            cells = [p.name, p.phone or "—", p.address or "—",
                     f"{p.opening_balance:,.2f}",
                     f"{symbol} {balance:,.2f}",
                     "Active" if p.is_active else "Disabled"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (3, 4):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif col == 5:
                    item.setTextAlignment(Qt.AlignCenter)
                if col == 4 and balance > 0:
                    item.setForeground(Qt.darkRed)
                if not p.is_active:
                    item.setForeground(Qt.gray)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.status.setText(f"{len(self._parties)} {self.noun}(s)")
        self._selection_changed()

    def _selection_changed(self) -> None:
        party = self._selected()
        chosen = party is not None
        protected = chosen and self._is_protected(party)

        self.edit_button.setEnabled(chosen)
        self.toggle_button.setEnabled(chosen and not protected)
        self.delete_button.setEnabled(chosen and not protected)
        self.payment_button.setEnabled(
            chosen and self._balances.get(party.id, Decimal("0")) > 0)
        if chosen:
            self.toggle_button.setText("Enable" if not party.is_active else "Disable")

        self._load_ledger(party)

    def _load_ledger(self, party) -> None:
        if party is None:
            self.ledger.setRowCount(0)
            self.ledger_heading.setText("Ledger")
            return
        self.ledger_heading.setText(f"Ledger — {party.name}")
        try:
            rows = self._service().ledger(party.id, limit=200)
        except Exception:
            log.exception("Could not load the ledger")
            self.ledger.setRowCount(0)
            return

        self.ledger.setRowCount(len(rows))
        for r, entry in enumerate(rows):
            when = entry["happened_at"]
            cells = [
                when.strftime("%d %b %Y, %H:%M") if when else "—",
                entry["kind"], entry["reference"] or "—",
                f"{Decimal(str(entry['debit'])):,.2f}" if entry["debit"] else "",
                f"{Decimal(str(entry['credit'])):,.2f}" if entry["credit"] else "",
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c in (3, 4):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.ledger.setItem(r, c, item)
        self.ledger.resizeColumnsToContents()
        self.ledger.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

    # ---------------------------------------------------------------- actions

    # --- the form -------------------------------------------------------

    def _open_form(self, party) -> None:
        self._editing = party
        protected = party is not None and self._is_protected(party)

        if party is None:
            self.card.set_title(f"New {self.noun}")
            self.heading.setText(f"{self.plural} \u203a New {self.noun}")
            for field in (self.f_name, self.f_phone, self.f_email, self.f_address):
                field.clear()
            self.f_opening.setValue(0.0)
        else:
            self.card.set_title(f"Edit {party.name}")
            self.heading.setText(f"{self.plural} \u203a {party.name}")
            self.f_name.setText(party.name)
            self.f_phone.setText(party.phone or "")
            self.f_email.setText(party.email or "")
            self.f_address.setText(party.address or "")
            self.f_opening.setValue(float(party.opening_balance or 0))

        # The walk-in customer is the one row the service refuses to rename.
        self.f_name.setReadOnly(protected)
        self.f_name.setToolTip("The walk-in customer cannot be renamed."
                               if protected else "")

        self.pages.setCurrentIndex(PAGE_FORM)
        target = self.f_phone if protected else self.f_name
        target.setFocus()
        target.selectAll()

    def _close_form(self) -> None:
        self._editing = None
        self.heading.setText(self.plural)
        self.pages.setCurrentIndex(PAGE_LIST)
        self.table.setFocus()

    def _create(self) -> None:
        self._open_form(None)

    def _edit(self) -> None:
        party = self._selected()
        if party is not None:
            self._open_form(party)

    def _save(self) -> None:
        values = {
            "name": self.f_name.text().strip(),
            "phone": self.f_phone.text().strip(),
            "email": self.f_email.text().strip(),
            "address": self.f_address.text().strip(),
            "opening_balance": Decimal(str(self.f_opening.value())),
        }
        party = self._editing
        if party is not None and self._is_protected(party):
            values.pop("name")          # the service refuses it anyway

        self.card.set_busy(True)
        try:
            if party is None:
                self._service().create(**values)
            else:
                self._service().update(party.id, **values)
        except Exception as exc:
            # Stay on the form so the entry survives and can be corrected.
            self._report(exc)
            return
        finally:
            self.card.set_busy(False)

        self._close_form()
        self.refresh()

    def _take_payment(self) -> None:
        party = self._selected()
        if party is None:
            return
        outstanding = self._balances.get(party.id, Decimal("0.00"))
        dialog = PaymentDialog(self, title=self.payment_verb, party_name=party.name,
                               outstanding=outstanding, prompt=self.payment_prompt)
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            self._record_payment(party.id, dialog.values())
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()

    def _toggle(self) -> None:
        party = self._selected()
        if party is None:
            return
        try:
            if party.is_active:
                self._service().deactivate(party.id)
            else:
                self._service().activate(party.id)
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()

    def _delete(self) -> None:
        party = self._selected()
        if party is None:
            return
        if QMessageBox.question(
            self, f"Delete {self.noun}",
            f"Delete {party.name!r}?\n\nThis cannot be undone. If they appear on "
            "any document the deletion will be refused — disable them instead.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            self._service().delete(party.id)
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()
