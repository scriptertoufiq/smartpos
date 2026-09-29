"""Product categories, including sub-categories."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
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

log = get_logger(__name__)

COLUMNS = ["Category", "Products", "Status"]
ROLE_ID = Qt.UserRole


class CategoryEditor(QDialog):
    """Create or edit one category."""

    def __init__(self, parent=None, category: Category | None = None,
                 parents: list[tuple[Category, int]] | None = None) -> None:
        super().__init__(parent)
        self.category = category
        creating = category is None
        self.setWindowTitle("New category" if creating else f"Edit {category.name}")
        self.setMinimumWidth(400)

        form = QFormLayout(self)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(10)

        self.name = QLineEdit(category.name if category else "")
        self.name.setPlaceholderText("Grocery, Beverages, Rice…")
        self.name.setMaxLength(120)
        form.addRow("Name", self.name)

        self.parent_box = QComboBox()
        self.parent_box.addItem("— top level —", None)
        for cat, depth in parents or []:
            # Only a top-level category can be a parent, and nothing can parent
            # itself; both are enforced in the service too.
            if depth != 1 or (category is not None and cat.id == category.id):
                continue
            self.parent_box.addItem(cat.name, cat.id)
        if category is not None and category.parent_id is not None:
            index = self.parent_box.findData(category.parent_id)
            if index >= 0:
                self.parent_box.setCurrentIndex(index)
        form.addRow("Sits under", self.parent_box)

        hint = QLabel("Categories go two levels deep: a category, and "
                      "sub-categories inside it.")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        form.addRow("", hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        return {"name": self.name.text().strip(),
                "parent_id": self.parent_box.currentData()}


class CategoryView(QWidget):
    """Product categories, including sub-categories."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._service = CategoryService(auth)
        self._by_id: dict[int, Category] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        heading = QLabel("Categories")
        heading.setObjectName("viewHeading")
        layout.addWidget(heading)

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

    def _create(self, parent_id: int | None) -> None:
        dialog = CategoryEditor(self, None, self._parent_options())
        if parent_id is not None:
            index = dialog.parent_box.findData(parent_id)
            if index >= 0:
                dialog.parent_box.setCurrentIndex(index)
        if dialog.exec_() != QDialog.Accepted:
            return
        v = dialog.values()
        try:
            self._service.create(v["name"], v["parent_id"])
        except Exception as exc:
            self._report(exc)
            return
        self.refresh()

    def _create_sub(self) -> None:
        category = self._selected()
        if category is not None:
            self._create(category.id)

    def _edit(self) -> None:
        category = self._selected()
        if category is None:
            return
        dialog = CategoryEditor(self, category, self._parent_options())
        if dialog.exec_() != QDialog.Accepted:
            return
        v = dialog.values()
        try:
            self._service.update(category.id, name=v["name"],
                                 parent_id=v["parent_id"], reparent=True)
        except Exception as exc:
            self._report(exc)
            return
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
