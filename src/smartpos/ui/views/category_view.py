"""Product categories, including sub-categories.

The list and the create/edit form are two pages of one screen; see
`smartpos.ui.widgets.form_page` for why there is no dialog here.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.models.category import Category
from smartpos.services.auth_service import AuthService
from smartpos.services.category_service import CategoryNode, CategoryService
from smartpos.ui.widgets.form_page import (
    PAGE_FORM, PAGE_LIST, FormCard, FormPage,
)

log = get_logger(__name__)

COLUMNS = ["Category", "Products", "Status"]
ROLE_ID = Qt.UserRole


class CategoryView(QWidget):
    """Product categories, including sub-categories.

The list and the create/edit form are two pages of one screen; see
`smartpos.ui.widgets.form_page` for why there is no dialog here.
"""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._service = CategoryService(auth)
        self._by_id: dict[int, Category] = {}

        self._editing: Category | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(14)

        self.heading = QLabel("Categories")
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

        blurb = QLabel("How products are grouped on the counter screen and in "
                       "reports. Two levels: a category, and sub-categories inside it.")
        blurb.setObjectName("hintLabel")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.show_disabled = QCheckBox("Show disabled")
        self.show_disabled.setChecked(True)
        bar.addWidget(self.show_disabled)
        bar.addStretch(1)
        self.add_button = QPushButton("New category")
        self.add_button.setProperty("primary", True)
        self.add_button.setMinimumHeight(34)
        bar.addWidget(self.add_button)
        layout.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 90)
        layout.addWidget(self.tree, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.add_sub_button = QPushButton("Add sub-category")
        self.edit_button = QPushButton("Edit")
        self.toggle_button = QPushButton("Disable")
        self.delete_button = QPushButton("Delete")
        self.delete_button.setProperty("danger", True)
        for b in (self.add_sub_button, self.edit_button,
                  self.toggle_button, self.delete_button):
            b.setMinimumHeight(32)
            b.setEnabled(False)
            actions.addWidget(b)
        actions.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("hintLabel")
        actions.addWidget(self.status)
        layout.addLayout(actions)

        self.show_disabled.toggled.connect(self.refresh)
        self.add_button.clicked.connect(lambda: self._create(None))
        self.add_sub_button.clicked.connect(self._create_sub)
        self.edit_button.clicked.connect(self._edit)
        self.toggle_button.clicked.connect(self._toggle)
        self.delete_button.clicked.connect(self._delete)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.doubleClicked.connect(self._edit)

        writable = auth.can("category.manage")
        for b in (self.add_button, self.add_sub_button, self.edit_button,
                  self.toggle_button, self.delete_button):
            b.setVisible(writable)
        return page

    # -------------------------------------------------------- the form page

    def _build_form(self) -> QWidget:
        card = FormCard(save_text="Save category")
        self.card = card

        self.f_name = card.add_row("Name", QLineEdit())
        self.f_name.setPlaceholderText("Grocery, Beverages, Rice\u2026")
        self.f_name.setMaxLength(120)

        self.f_parent = card.add_row("Sits under", QComboBox())

        card.set_hint("Categories go two levels deep: a category, and "
                      "sub-categories inside it.")
        card.save_clicked.connect(self._save)
        card.cancel_clicked.connect(self._close_form)
        self.f_name.returnPressed.connect(self._save)
        return FormPage(card)

    # ------------------------------------------------------------- helpers

    def _selected(self) -> Category | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        return self._by_id.get(item.data(0, ROLE_ID))

    def _selection_changed(self) -> None:
        category = self._selected()
        chosen = category is not None
        self.edit_button.setEnabled(chosen)
        self.toggle_button.setEnabled(chosen)
        self.delete_button.setEnabled(chosen)
        # Only a top-level category may take a sub-category.
        self.add_sub_button.setEnabled(chosen and category.parent_id is None)

        if not chosen:
            self.status.setText("")
            return
        self.toggle_button.setText("Enable" if not category.is_active else "Disable")
        try:
            usage = self._service.usage(category.id)
        except Exception:
            log.exception("Could not read category usage")
            self.status.setText("")
            return
        bits = []
        if usage["products"]:
            bits.append(f"{usage['products']} product(s)")
        if usage["sub_categories"]:
            bits.append(f"{usage['sub_categories']} sub-categor"
                        f"{'ies' if usage['sub_categories'] != 1 else 'y'}")
        self.status.setText(" · ".join(bits) or "not used yet")

    def _report(self, exc: Exception) -> None:
        if isinstance(exc, SmartPOSError):
            QMessageBox.warning(self, "Cannot do that", str(exc))
        else:
            log.exception("Unexpected error in the category screen")
            QMessageBox.critical(
                self, "Something went wrong",
                "The action failed. The details were written to the log file.")

    # -------------------------------------------------------------- actions

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        keep = self.tree.currentItem().data(0, ROLE_ID) if self.tree.currentItem() else None
        try:
            roots = self._service.tree(
                active_only=not self.show_disabled.isChecked(), with_counts=True)
        except Exception as exc:
            self._report(exc)
            return

        self.tree.clear()
        self._by_id.clear()
        restore: QTreeWidgetItem | None = None

        def add(node: CategoryNode, parent_item: QTreeWidgetItem | None) -> None:
            nonlocal restore
            cat = node.category
            self._by_id[cat.id] = cat
            item = QTreeWidgetItem([
                cat.name,
                str(node.product_count) if node.product_count else "—",
                "Active" if cat.is_active else "Disabled",
            ])
            item.setData(0, ROLE_ID, cat.id)
            item.setTextAlignment(1, Qt.AlignCenter)
            item.setTextAlignment(2, Qt.AlignCenter)
            if not cat.is_active:
                for col in range(len(COLUMNS)):
                    item.setForeground(col, Qt.gray)
            if parent_item is None:
                self.tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            if cat.id == keep:
                restore = item
            for child in node.children:
                add(child, item)

        for root in roots:
            add(root, None)
        self.tree.expandAll()
        if restore is not None:
            self.tree.setCurrentItem(restore)
        self._selection_changed()

    def _parent_options(self) -> list[tuple[Category, int]]:
        try:
            return self._service.flat_tree()
        except Exception:
            log.exception("Could not load categories for the parent picker")
            return []

    # --- the form -------------------------------------------------------

    def _fill_parents(self, category: Category | None) -> None:
        """Only a top-level category can be a parent, and nothing can parent
        itself; both are enforced in the service too."""
        self.f_parent.clear()
        self.f_parent.addItem("\u2014 top level \u2014", None)
        for cat, depth in self._parent_options():
            if depth != 1 or (category is not None and cat.id == category.id):
                continue
            self.f_parent.addItem(cat.name, cat.id)

    def _open_form(self, category: Category | None,
                   parent_id: int | None = None) -> None:
        self._editing = category
        self._fill_parents(category)

        if category is None:
            self.card.set_title("New category")
            self.heading.setText("Categories \u203a New category")
            self.f_name.clear()
            wanted = parent_id
        else:
            self.card.set_title(f"Edit {category.name}")
            self.heading.setText(f"Categories \u203a {category.name}")
            self.f_name.setText(category.name)
            wanted = category.parent_id

        index = self.f_parent.findData(wanted) if wanted is not None else 0
        self.f_parent.setCurrentIndex(index if index >= 0 else 0)

        self.pages.setCurrentIndex(PAGE_FORM)
        self.f_name.setFocus()
        self.f_name.selectAll()

    def _close_form(self) -> None:
        self._editing = None
        self.heading.setText("Categories")
        self.pages.setCurrentIndex(PAGE_LIST)
        self.tree.setFocus()

    def _create(self, parent_id: int | None) -> None:
        self._open_form(None, parent_id)

    def _create_sub(self) -> None:
        category = self._selected()
        if category is not None:
            self._open_form(None, category.id)

    def _edit(self) -> None:
        category = self._selected()
        if category is not None:
            self._open_form(category)

    def _save(self) -> None:
        name = self.f_name.text().strip()
        parent_id = self.f_parent.currentData()

        self.card.set_busy(True)
        try:
            if self._editing is None:
                self._service.create(name, parent_id)
            else:
                self._service.update(self._editing.id, name=name,
                                     parent_id=parent_id, reparent=True)
        except Exception as exc:
            # Stay on the form so the typing survives and the name can be fixed.
            self._report(exc)
            return
        finally:
            self.card.set_busy(False)

        self._close_form()
        self.refresh()

    def _toggle(self) -> None:
        category = self._selected()
        if category is None:
            return
        try:
            if category.is_active:
                self._service.deactivate(category.id)
            else:
                self._service.activate(category.id)
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()

    def _delete(self) -> None:
        category = self._selected()
        if category is None:
            return
        if QMessageBox.question(
            self, "Delete category",
            f"Delete {category.name!r}?\n\nThis cannot be undone. If any product "
            "still uses it the deletion will be refused — disable it instead.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            self._service.delete(category.id)
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()
