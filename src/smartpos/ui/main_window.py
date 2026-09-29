"""The main application window.

Holds the sidebar navigation and a QStackedWidget of the views in
`smartpos.ui.views`. Which entries appear depends on the signed-in user's role:
an entry a role cannot use is not disabled, it is not built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from smartpos import __app_name__, __version__
from smartpos.core.logging import get_logger
from smartpos.services.auth_service import AuthService
from smartpos.services.warehouse_service import WarehouseService
from smartpos.ui.views.category_view import CategoryView
from smartpos.ui.views.user_view import UserView
from smartpos.ui.views.warehouse_view import WarehouseView

log = get_logger(__name__)


@dataclass(frozen=True)
class NavEntry:
    """One sidebar destination."""

    key: str
    label: str
    permission: str | None      # None = everyone who is signed in
    factory: Callable[["MainWindow"], QWidget]


class PlaceholderView(QWidget):
    """A screen that has not been built yet.

    Deliberately explicit about which milestone delivers it, so the shell can
    be navigated now without pretending the feature exists.
    """

    def __init__(self, title: str, milestone: str, summary: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(10)

        heading = QLabel(title)
        heading.setObjectName("viewHeading")
        layout.addWidget(heading)

        badge = QLabel(f"Not built yet — arrives in {milestone}")
        badge.setObjectName("pendingBadge")
        layout.addWidget(badge, 0, Qt.AlignLeft)

        body = QLabel(summary)
        body.setObjectName("hintLabel")
        body.setWordWrap(True)
        layout.addWidget(body)
        layout.addStretch(1)


def _pending(title: str, milestone: str, summary: str):
    return lambda _win: PlaceholderView(title, milestone, summary)


NAVIGATION: tuple[NavEntry, ...] = (
    NavEntry("dashboard", "Dashboard", "report.view",
             _pending("Dashboard", "M11",
                      "Today's sales and collection, low-stock count and a sales "
                      "trend chart.")),
    NavEntry("pos", "POS Counter", "sale.create",
             _pending("POS Counter", "M9",
                      "Barcode entry, cart, discount, payment and invoice "
                      "printing. The screen cashiers live in.")),
    NavEntry("sales", "Sales", "sale.view",
             _pending("Sales", "M9", "Past invoices, with date and customer filters.")),
    NavEntry("returns", "Returns", "sale_return.create",
             _pending("Sale Returns", "M10",
                      "Return against an invoice number, for the whole invoice or "
                      "selected lines.")),
    NavEntry("purchases", "Purchases", "purchase.manage",
             _pending("Purchases", "M8", "Record goods received from a supplier.")),
    NavEntry("products", "Products", "product.view",
             _pending("Products", "M5",
                      "Catalogue, barcode labels, CSV import and export.")),
    NavEntry("categories", "Categories", "category.manage",
             lambda win: CategoryView(win.auth, win)),
    NavEntry("customers", "Customers", "customer.view",
             _pending("Customers", "M6", "Profiles, purchase history and dues.")),
    NavEntry("suppliers", "Suppliers", "supplier.manage",
             _pending("Suppliers", "M6", "Profiles, payables and payments made.")),
    NavEntry("stock", "Stock", "stock.manage",
             _pending("Stock", "M7",
                      "Levels per warehouse, transfers, adjustments and the "
                      "movement ledger.")),
    NavEntry("reports", "Reports", "report.view",
             _pending("Reports", "M11", "Sales, profit, stock and due statements.")),
    NavEntry("warehouses", "Warehouses", "warehouse.view",
             lambda win: WarehouseView(win.auth, win)),
    NavEntry("users", "Users", "user.manage",
             lambda win: UserView(win.auth, win)),
    NavEntry("settings", "Settings", "settings.manage",
             _pending("Settings", "M12",
                      "Shop profile, currency, tax rate, invoice footer and backup.")),
)


class MainWindow(QMainWindow):
    """Sidebar plus a stack of views, built for the signed-in user's role."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self.auth = auth
        self._views: dict[str, QWidget] = {}
        self._buttons: dict[str, QPushButton] = {}
        # True when the window closed because someone signed out, as opposed to
        # quitting the application. The entry point uses it to decide whether to
        # show the login dialog again -- a counter changes hands all day.
        self.sign_out_requested = False

        user = auth.current_user()
        self.setWindowTitle(f"{__app_name__} — {user.full_name if user else ''}")
        # Open filling the screen: a counter terminal is never a floating
        # window someone resizes. Setting the real geometry before the window is
        # realised means the first frame is already full size -- no resize flash.
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            # Never demand more than the screen has: a minimum wider than the
            # display forces Qt to resize on show, which is itself a flash.
            self.setMinimumSize(min(880, available.width()),
                                min(540, available.height()))
            self.setGeometry(available)
        else:
            self.setMinimumSize(880, 540)
        self.setWindowState(Qt.WindowMaximized)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.setObjectName("contentStack")
        root.addWidget(self.stack, 1)

        self.setCentralWidget(central)
        self._build_status_bar()
        self.build_navigation()

    # ------------------------------------------------------------- building

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(208)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 18, 0, 12)
        layout.setSpacing(2)

        brand = QLabel(__app_name__)
        brand.setObjectName("brand")
        brand.setContentsMargins(18, 0, 18, 14)
        layout.addWidget(brand)

        self._nav_layout = QVBoxLayout()
        self._nav_layout.setSpacing(1)
        self._nav_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._nav_layout)
        layout.addStretch(1)

        rule = QFrame()
        rule.setFrameShape(QFrame.HLine)
        rule.setObjectName("sidebarRule")
        layout.addWidget(rule)

        user = self.auth.current_user()
        who = QLabel(f"{user.username}\n{user.role.value.capitalize()}" if user else "")
        who.setObjectName("sidebarUser")
        who.setContentsMargins(18, 10, 18, 8)
        layout.addWidget(who)

        sign_out = QPushButton("Sign out")
        sign_out.setObjectName("signOut")
        sign_out.clicked.connect(self._sign_out)
        layout.addWidget(sign_out)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        return sidebar

    def build_navigation(self) -> None:
        """Create the sidebar, hiding entries the current role cannot use."""
        first_key: str | None = None
        for entry in NAVIGATION:
            if entry.permission and not self.auth.can(entry.permission):
                continue  # not built, not merely disabled

            button = QPushButton(entry.label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked, k=entry.key: self.show_view(k))
            self._nav_group.addButton(button)
            self._nav_layout.addWidget(button)
            self._buttons[entry.key] = button

            if first_key is None:
                first_key = entry.key

        if first_key:
            self.show_view(first_key)
        else:
            log.warning("Signed-in user has no permitted screens")

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        from smartpos.config import get_settings

        settings = get_settings()
        bar.addWidget(QLabel(f"Connected · {settings.database.name}"))

        # Which outlet this session is trading from. Sales leave it and
        # purchases land in it, so it belongs on screen at all times rather
        # than buried in a form.
        bar.addPermanentWidget(QLabel("Outlet"))
        self.warehouse_picker = QComboBox()
        self.warehouse_picker.setObjectName("warehousePicker")
        self.warehouse_picker.setMinimumWidth(180)
        bar.addPermanentWidget(self.warehouse_picker)
        self._load_warehouses()
        self.warehouse_picker.currentIndexChanged.connect(self._warehouse_changed)

        right = QLabel(f"{__app_name__} {__version__}")
        right.setObjectName("hintLabel")
        bar.addPermanentWidget(right)

    def _load_warehouses(self) -> None:
        """Fill the outlet picker, defaulting to the one the user is posted to."""
        self.warehouse_picker.blockSignals(True)
        self.warehouse_picker.clear()
        try:
            warehouses = WarehouseService().list_all(active_only=True)
        except Exception:
            log.exception("Could not load outlets for the status bar")
            warehouses = []

        for warehouse in warehouses:
            self.warehouse_picker.addItem(warehouse.name, warehouse.id)

        user = self.auth.current_user()
        if user is not None and user.warehouse_id is not None:
            index = self.warehouse_picker.findData(user.warehouse_id)
            if index >= 0:
                self.warehouse_picker.setCurrentIndex(index)
        self.warehouse_picker.blockSignals(False)

        self.active_warehouse_id = self.warehouse_picker.currentData()
        self.warehouse_picker.setEnabled(self.warehouse_picker.count() > 1)

    def _warehouse_changed(self) -> None:
        self.active_warehouse_id = self.warehouse_picker.currentData()
        log.info("Active outlet is now %s", self.warehouse_picker.currentText())
        current = self.stack.currentWidget()
        if hasattr(current, "refresh"):
            try:
                current.refresh()
            except Exception:
                log.exception("Refreshing after an outlet change failed")

    # ------------------------------------------------------------ behaviour

    def show_view(self, name: str) -> None:
        """Bring one of the stacked views to the front, building it on first use."""
        entry = next((e for e in NAVIGATION if e.key == name), None)
        if entry is None:
            return
        if entry.permission and not self.auth.can(entry.permission):
            QMessageBox.warning(self, "Not permitted",
                                "Your account cannot open that screen.")
            return

        if name not in self._views:
            try:
                self._views[name] = entry.factory(self)
            except Exception:
                log.exception("Could not build the %s view", name)
                QMessageBox.critical(
                    self, "Screen unavailable",
                    "That screen could not be opened. The details were written "
                    "to the log file.")
                return
            self.stack.addWidget(self._views[name])

        view = self._views[name]
        self.stack.setCurrentWidget(view)
        if hasattr(view, "refresh"):
            try:
                view.refresh()
            except Exception:
                log.exception("Refreshing the %s view failed", name)

        button = self._buttons.get(name)
        if button is not None:
            button.setChecked(True)

    def _sign_out(self) -> None:
        if QMessageBox.question(
            self, "Sign out", "Sign out of SmartPOS?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.sign_out_requested = True
        self.auth.logout()
        self.close()
