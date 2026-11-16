"""Product master: create, edit, search, barcode labels, CSV import and export.

The list and the create/edit form are two pages of one screen; see
`smartpos.ui.widgets.form_page` for why there is no dialog here.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
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

from smartpos.config import get_settings
from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.models.product import Product
from smartpos.services.auth_service import AuthService
from smartpos.services.category_service import CategoryService
from smartpos.services.product_service import ProductService
from smartpos.ui.widgets.form_page import (
    PAGE_FORM, PAGE_LIST, FormCard, FormPage,
)

log = get_logger(__name__)

COLUMNS = ["SKU", "Name", "Category", "Unit", "Cost", "Price", "Tax %",
           "Reorder", "Barcode", "Status"]
MONEY_COLUMNS = {4, 5, 6, 7}


def _money_box(decimals: int = 2, maximum: float = 99_999_999.0) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setDecimals(decimals)
    box.setMaximum(maximum)
    box.setGroupSeparatorShown(True)
    box.setMinimumHeight(30)
    return box


class ProductView(QWidget):
    """Product master: create, edit, search, barcode labels, CSV import and export.

The list and the create/edit form are two pages of one screen; see
`smartpos.ui.widgets.form_page` for why there is no dialog here.
"""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._service = ProductService(auth)
        self._categories = CategoryService(auth)
        self._products: list[Product] = []

        self._editing: Product | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(14)

        self.heading = QLabel("Products")
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
        layout.setSpacing(12)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, SKU or barcode — or scan…")
        self.search.setMinimumHeight(34)
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search, 1)

        self.category_filter = QComboBox()
        self.category_filter.setMinimumHeight(34)
        self.category_filter.setMinimumWidth(160)
        bar.addWidget(self.category_filter)

        self.show_disabled = QCheckBox("Show disabled")
        bar.addWidget(self.show_disabled)

        self.add_button = QPushButton("New product")
        self.add_button.setProperty("primary", True)
        self.add_button.setMinimumHeight(34)
        bar.addWidget(self.add_button)
        layout.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.edit_button = QPushButton("Edit")
        self.barcode_button = QPushButton("Generate barcode")
        self.label_button = QPushButton("Print labels")
        self.toggle_button = QPushButton("Disable")
        for b in (self.edit_button, self.barcode_button,
                  self.label_button, self.toggle_button):
            b.setMinimumHeight(32)
            b.setEnabled(False)
            actions.addWidget(b)
        actions.addStretch(1)
        self.import_button = QPushButton("Import CSV")
        self.export_button = QPushButton("Export CSV")
        for b in (self.import_button, self.export_button):
            b.setMinimumHeight(32)
            actions.addWidget(b)
        layout.addLayout(actions)

        self.status = QLabel("")
        self.status.setObjectName("hintLabel")
        layout.addWidget(self.status)

        self.search.textChanged.connect(self.refresh)
        self.category_filter.currentIndexChanged.connect(self.refresh)
        self.show_disabled.toggled.connect(self.refresh)
        self.add_button.clicked.connect(self._create)
        self.edit_button.clicked.connect(self._edit)
        self.barcode_button.clicked.connect(self._generate_barcode)
        self.label_button.clicked.connect(self._print_labels)
        self.toggle_button.clicked.connect(self._toggle)
        self.import_button.clicked.connect(self._import)
        self.export_button.clicked.connect(self._export)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(self._edit)

        writable = auth.can("product.manage")
        for b in (self.add_button, self.edit_button, self.barcode_button,
                  self.toggle_button, self.import_button):
            b.setVisible(writable)

        self._load_categories()
        return page

    # -------------------------------------------------------- the form page

    def _build_form(self) -> QWidget:
        card = FormCard(save_text="Save product")
        self.card = card
        symbol = get_settings().currency_symbol

        self.f_sku = card.add_row("SKU", QLineEdit())
        self.f_sku.setPlaceholderText("RICE-5KG \u2014 no spaces")
        self.f_sku.setMaxLength(40)

        self.f_name = card.add_row("Name", QLineEdit())
        self.f_name.setPlaceholderText("Rice Miniket 5kg")
        self.f_name.setMaxLength(160)

        self.f_barcode = card.add_row("Barcode", QLineEdit())
        self.f_barcode.setPlaceholderText("leave blank to generate one")
        self.f_barcode.setMaxLength(64)

        self.f_category = card.add_row("Category", QComboBox())
        self.f_unit = card.add_row("Unit", QComboBox())

        self.f_cost = card.add_row(f"Cost ({symbol})", _money_box())
        self.f_price = card.add_row(f"Price ({symbol})", _money_box())
        self.f_tax = card.add_row("Tax rate", _money_box(maximum=999.99))
        self.f_tax.setSuffix(" %")
        self.f_reorder = card.add_row("Reorder at", _money_box(decimals=3))

        card.set_hint("Cost is what you pay; price is what the customer pays. "
                      "A sale records the cost of the day it happened.")
        card.save_clicked.connect(self._save)
        card.cancel_clicked.connect(self._close_form)
        for field in (self.f_sku, self.f_name, self.f_barcode):
            field.returnPressed.connect(self._save)
        return FormPage(card)

    # ------------------------------------------------------------- helpers

    def _load_categories(self) -> None:
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("All categories", None)
        try:
            for cat, depth in self._categories.flat_tree():
                self.category_filter.addItem(("    " * (depth - 1)) + cat.name, cat.id)
        except Exception:
            log.exception("Could not load categories for the filter")
        self.category_filter.blockSignals(False)

    def _selected(self) -> list[Product]:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        return [self._products[r.row()] for r in rows]

    def _selection_changed(self) -> None:
        chosen = self._selected()
        one = len(chosen) == 1
        self.edit_button.setEnabled(one)
        self.toggle_button.setEnabled(one)
        self.barcode_button.setEnabled(one and not chosen[0].barcode)
        self.label_button.setEnabled(bool(chosen))
        if one:
            self.toggle_button.setText("Enable" if not chosen[0].is_active else "Disable")

    def _report(self, exc: Exception) -> None:
        if isinstance(exc, SmartPOSError):
            QMessageBox.warning(self, "Cannot do that", str(exc))
        else:
            log.exception("Unexpected error in the product screen")
            QMessageBox.critical(
                self, "Something went wrong",
                "The action failed. The details were written to the log file.")

    # -------------------------------------------------------------- actions

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        try:
            self._products = self._service.search(
                self.search.text().strip(),
                limit=500,
                active_only=not self.show_disabled.isChecked(),
                category_id=self.category_filter.currentData(),
            )
        except Exception as exc:
            self._report(exc)
            return

        self.table.setRowCount(len(self._products))
        for row, p in enumerate(self._products):
            cells = [
                p.sku, p.name,
                getattr(p, "category_name", None) or "—",
                getattr(p, "unit_name", None) or "—",
                f"{p.purchase_price:,.2f}", f"{p.sale_price:,.2f}",
                f"{p.tax_rate:,.2f}", f"{p.reorder_level:,.3f}",
                p.barcode or "—",
                "Active" if p.is_active else "Disabled",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in MONEY_COLUMNS:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif col == 9:
                    item.setTextAlignment(Qt.AlignCenter)
                if not p.is_active:
                    item.setForeground(Qt.gray)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.status.setText(f"{len(self._products)} product(s)")
        self._selection_changed()

    def _editor_lists(self):
        try:
            return self._categories.flat_tree(), self._categories.list_units()
        except Exception:
            log.exception("Could not load categories or units")
            return [], []

    # --- the form -------------------------------------------------------

    def _open_form(self, product: Product | None) -> None:
        self._editing = product
        categories, units = self._editor_lists()

        self.f_category.clear()
        self.f_category.addItem("\u2014 none \u2014", None)
        for cat, depth in categories:
            self.f_category.addItem(("    " * (depth - 1)) + cat.name, cat.id)

        self.f_unit.clear()
        self.f_unit.addItem("\u2014 none \u2014", None)
        for unit in units:
            self.f_unit.addItem(f"{unit.name} ({unit.short_name})", unit.id)

        if product is None:
            self.card.set_title("New product")
            self.heading.setText("Products \u203a New product")
            self.f_sku.clear()
            self.f_name.clear()
            self.f_barcode.clear()
            self.f_category.setCurrentIndex(0)
            self.f_unit.setCurrentIndex(0)
            for box in (self.f_cost, self.f_price, self.f_tax, self.f_reorder):
                box.setValue(0.0)
        else:
            self.card.set_title(f"Edit {product.name}")
            self.heading.setText(f"Products \u203a {product.sku}")
            self.f_sku.setText(product.sku)
            self.f_name.setText(product.name)
            self.f_barcode.setText(product.barcode or "")
            for box, data in ((self.f_category, product.category_id),
                              (self.f_unit, product.unit_id)):
                index = box.findData(data) if data is not None else 0
                box.setCurrentIndex(index if index >= 0 else 0)
            self.f_cost.setValue(float(product.purchase_price or 0))
            self.f_price.setValue(float(product.sale_price or 0))
            self.f_tax.setValue(float(product.tax_rate or 0))
            self.f_reorder.setValue(float(product.reorder_level or 0))

        self.pages.setCurrentIndex(PAGE_FORM)
        self.f_sku.setFocus()
        self.f_sku.selectAll()

    def _close_form(self) -> None:
        self._editing = None
        self.heading.setText("Products")
        self.pages.setCurrentIndex(PAGE_LIST)
        self.table.setFocus()

    def _form_values(self) -> dict:
        return {
            "sku": self.f_sku.text().strip(),
            "name": self.f_name.text().strip(),
            "barcode": self.f_barcode.text().strip(),
            "category_id": self.f_category.currentData(),
            "unit_id": self.f_unit.currentData(),
            "purchase_price": Decimal(str(self.f_cost.value())),
            "sale_price": Decimal(str(self.f_price.value())),
            "tax_rate": Decimal(str(self.f_tax.value())),
            "reorder_level": Decimal(str(self.f_reorder.value())),
        }

    def _create(self) -> None:
        self._open_form(None)

    def _edit(self) -> None:
        chosen = self._selected()
        if len(chosen) == 1:
            self._open_form(chosen[0])

    def _save(self) -> None:
        v = self._form_values()
        self.card.set_busy(True)
        try:
            if self._editing is None:
                self._service.create(
                    v["sku"], v["name"], barcode=v["barcode"] or None,
                    category_id=v["category_id"], unit_id=v["unit_id"],
                    purchase_price=v["purchase_price"], sale_price=v["sale_price"],
                    tax_rate=v["tax_rate"], reorder_level=v["reorder_level"],
                    auto_barcode=not v["barcode"])
            else:
                self._service.update(self._editing.id, **v)
        except Exception as exc:
            # Stay on the form: a rejected SKU should be fixable in place.
            self._report(exc)
            return
        finally:
            self.card.set_busy(False)

        self._close_form()
        self.refresh()

    def _generate_barcode(self) -> None:
        chosen = self._selected()
        if len(chosen) != 1:
            return
        try:
            code = self._service.generate_barcode(chosen[0].id)
        except Exception as exc:
            self._report(exc)
            return
        QMessageBox.information(self, "Barcode assigned",
                                f"{chosen[0].name} is now {code}.")
        self.refresh()

    def _print_labels(self) -> None:
        chosen = self._selected()
        if not chosen:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Save label sheet", str(Path.home() / "labels.png"), "PNG image (*.png)")
        if not target:
            return
        try:
            written = self._service.render_labels([p.id for p in chosen], Path(target))
        except Exception as exc:
            self._report(exc)
            return
        QMessageBox.information(self, "Labels ready",
                                f"{len(chosen)} label(s) written to\n{written}")

    def _toggle(self) -> None:
        chosen = self._selected()
        if len(chosen) != 1:
            return
        try:
            if chosen[0].is_active:
                self._service.deactivate(chosen[0].id)
            else:
                self._service.activate(chosen[0].id)
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()

    def _export(self) -> None:
        target, _ = QFileDialog.getSaveFileName(
            self, "Export products", str(Path.home() / "products.csv"), "CSV file (*.csv)")
        if not target:
            return
        try:
            count = self._service.export_csv(Path(target))
        except Exception as exc:
            self._report(exc)
            return
        QMessageBox.information(self, "Exported",
                                f"{count} product(s) written to\n{target}")

    def _import(self) -> None:
        source, _ = QFileDialog.getOpenFileName(
            self, "Import products", str(Path.home()), "CSV file (*.csv)")
        if not source:
            return
        update = QMessageBox.question(
            self, "Existing products",
            "Update products whose SKU already exists?\n\n"
            "Choose No to skip them and report them instead.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        try:
            result = self._service.import_csv(Path(source), update_existing=update)
        except Exception as exc:
            self._report(exc)
            return

        message = result.summary()
        if result.failed:
            shown = "\n".join(f"  line {line}: {reason}"
                              for line, reason in result.failed[:12])
            more = (f"\n  … and {len(result.failed) - 12} more"
                    if len(result.failed) > 12 else "")
            QMessageBox.warning(
                self, "Imported with problems",
                f"{message}.\n\nThe rows that were skipped:\n{shown}{more}")
        else:
            QMessageBox.information(self, "Imported", f"{message}.")
        self.refresh()
