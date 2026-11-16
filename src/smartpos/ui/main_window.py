"""The main application window -- the only window the application ever has.

Holds two pages: the sign-in screen and the application shell (sidebar plus a
QStackedWidget of the views in `smartpos.ui.views`). Which sidebar entries
appear depends on the signed-in user's role: an entry a role cannot use is not
disabled, it is not built.

Signing in and out swaps the page. It deliberately does NOT create or destroy a
window. The two used to be separate top-level windows, and because a window's
state is decided by its constructor, every shift change dragged the user back to
a maximised window -- out of full screen, and on macOS out of its Space. One
window cannot do that: there is no second window to disagree about the state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
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
from smartpos.ui.login_page import LoginPage
from smartpos.ui.views.category_view import CategoryView
from smartpos.ui.views.dashboard_view import DashboardView
from smartpos.ui.views.customer_view import CustomerView
from smartpos.ui.views.pos_view import PosView
from smartpos.ui.views.product_view import ProductView
from smartpos.ui.views.report_view import ReportView
from smartpos.ui.views.settings_view import SettingsView
from smartpos.ui.views.sale_list_view import SaleListView
from smartpos.ui.views.sale_return_view import SaleReturnView
from smartpos.ui.views.purchase_view import PurchaseView
from smartpos.ui.views.stock_view import StockView
from smartpos.ui.views.supplier_view import SupplierView
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
             lambda win: DashboardView(win.auth, win)),
    NavEntry("pos", "POS Counter", "sale.create",
             lambda win: PosView(win.auth, win)),
    NavEntry("sales", "Sales", "sale.view",
             lambda win: SaleListView(win.auth, win)),
    NavEntry("returns", "Returns", "sale_return.view",
             lambda win: SaleReturnView(win.auth, win)),
    NavEntry("purchases", "Purchases", "purchase.manage",
             lambda win: PurchaseView(win.auth, win)),
    NavEntry("products", "Products", "product.view",
             lambda win: ProductView(win.auth, win)),
    NavEntry("categories", "Categories", "category.manage",
             lambda win: CategoryView(win.auth, win)),
    NavEntry("customers", "Customers", "customer.view",
             lambda win: CustomerView(win.auth, win)),
    NavEntry("suppliers", "Suppliers", "supplier.manage",
             lambda win: SupplierView(win.auth, win)),
    NavEntry("stock", "Stock", "stock.manage",
             lambda win: StockView(win.auth, win)),
    NavEntry("reports", "Reports", "report.view",
             lambda win: ReportView(win.auth, win)),
    NavEntry("warehouses", "Warehouses", "warehouse.view",
             lambda win: WarehouseView(win.auth, win)),
    NavEntry("users", "Users", "user.manage",
             lambda win: UserView(win.auth, win)),
    NavEntry("settings", "Settings", "settings.manage",
             lambda win: SettingsView(win.auth, win)),
)


class MainWindow(QMainWindow):
    """Sidebar plus a stack of views, built for the signed-in user's role."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self.auth = auth
        self._views: dict[str, QWidget] = {}
        self._buttons: dict[str, QPushButton] = {}

        self.setWindowTitle(f"{__app_name__} — Sign in")
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
        # Ask for the full set of window controls rather than trusting the
        # platform default, so minimise and zoom are always there.
        self.setWindowFlags(Qt.Window
                            | Qt.WindowMinimizeButtonHint
                            | Qt.WindowMaximizeButtonHint
                            | Qt.WindowCloseButtonHint)
        self.setWindowState(Qt.WindowMaximized)

        # Page 0 signs in, page 1 is the application. The window itself never
        # changes, whichever one is showing.
        self.pages = QStackedWidget()
        self.login_page = LoginPage(auth)
        self.login_page.signed_in.connect(self._on_signed_in)
        self.pages.addWidget(self.login_page)
        self.pages.addWidget(self._build_shell())
        self.setCentralWidget(self.pages)

        self._build_status_bar()
        self._build_window_menu()
        self._show_login()

    def _build_shell(self) -> QWidget:
        """Sidebar plus the stack of views."""
        shell = QWidget()
        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.setObjectName("contentStack")
        root.addWidget(self.stack, 1)
        return shell

    def _build_window_menu(self) -> None:
        """Minimise, zoom and full screen, from the menu bar or the keyboard."""
        menu = self.menuBar().addMenu("&Window")

        minimise = menu.addAction("Minimise")
        minimise.setShortcut(QKeySequence("Ctrl+M"))
        minimise.triggered.connect(self.showMinimized)

        self._zoom_action = menu.addAction("Zoom")
        self._zoom_action.setShortcut(QKeySequence(Qt.Key_F11))
        self._zoom_action.triggered.connect(self._toggle_maximised)

        full_screen = menu.addAction("Full screen")
        full_screen.setShortcut(QKeySequence("Ctrl+Shift+F"))
        full_screen.setCheckable(True)
        full_screen.triggered.connect(self._toggle_full_screen)
        self._full_screen_action = full_screen

        menu.addSeparator()
        self._sign_out_action = menu.addAction("Sign out")
        self._sign_out_action.triggered.connect(self._sign_out)

    def _toggle_maximised(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _toggle_full_screen(self, checked: bool) -> None:
        """Kiosk mode for a dedicated terminal. Escapable, deliberately:
        the same shortcut and the menu item both bring it back."""
        if checked:
            self.showFullScreen()
        else:
            self.showMaximized()

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

        self._who = QLabel("")
        self._who.setObjectName("sidebarUser")
        self._who.setContentsMargins(18, 10, 18, 8)
        layout.addWidget(self._who)

        sign_out = QPushButton("Sign out")
        sign_out.setObjectName("signOut")
        sign_out.clicked.connect(self._sign_out)
        layout.addWidget(sign_out)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        return sidebar

    def build_navigation(self) -> None:
        """Create the sidebar, hiding entries the current role cannot use.

        Runs again on every sign-in, so it clears what the previous user left
        behind -- the next person on the counter may hold a different role.
        """
        self._clear_navigation()
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

    def _clear_navigation(self) -> None:
        """Forget the previous session's buttons and the views they opened."""
        for button in self._buttons.values():
            self._nav_group.removeButton(button)
            self._nav_layout.removeWidget(button)
            button.deleteLater()
        self._buttons.clear()

        for view in self._views.values():
            self.stack.removeWidget(view)
            view.deleteLater()
        self._views.clear()

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

    # ---------------------------------------------------- signing in and out

    def _show_login(self) -> None:
        """Put the sign-in page up and strip the window back to it.

        The sidebar and the outlet picker belong to a session, so they go; the
        window's size, position and full-screen state are not touched, because
        those belong to the person using it.
        """
        self.setWindowTitle(f"{__app_name__} \u2014 Sign in")
        self.statusBar().setVisible(False)
        self._sign_out_action.setEnabled(False)
        self.pages.setCurrentWidget(self.login_page)
        self.login_page.reset()

    def _on_signed_in(self, user) -> None:
        """Swap to the application shell for the user who just signed in."""
        self.setWindowTitle(f"{__app_name__} \u2014 {user.full_name}")
        self._who.setText(f"{user.username}\n{user.role.value.capitalize()}")
        self._load_warehouses()
        self.build_navigation()
        self.statusBar().setVisible(True)
        self._sign_out_action.setEnabled(True)
        self.pages.setCurrentIndex(1)
        log.info("Signed in as %s", user.username)

    def _sign_out(self) -> None:
        if QMessageBox.question(
            self, "Sign out", "Sign out of SmartPOS?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.auth.logout()
        self._clear_navigation()
        self._show_login()
        log.info("Signed out; back to the sign-in page")
