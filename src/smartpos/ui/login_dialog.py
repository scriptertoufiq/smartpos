"""Login dialog, shown before the main window.

Fills the screen the way a counter terminal should, with the sign-in card
centred. Nothing else is on screen to click, so the first thing a cashier sees
at the start of a shift is the one thing they need to do.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from smartpos import __app_name__, __version__
from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.models.user import User
from smartpos.services.auth_service import AuthService

log = get_logger(__name__)

CARD_WIDTH = 400


class LoginDialog(QDialog):
    """Collects a username and password and hands them to AuthService.

    Closes with Accepted only once someone is signed in; `user` then holds them.

    `signed_in` fires while this dialog is still on screen, so the caller can
    build and show the main window before this one disappears. Without that,
    the desktop shows through the gap between the two.
    """

    signed_in = pyqtSignal(object)

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self.user: User | None = None

        self.setWindowTitle(f"{__app_name__} — Sign in")
        self.setObjectName("loginWindow")
        self.setModal(True)
        # Give it the full screen geometry *before* it is ever realised, so the
        # first frame is already the right size. Setting only the maximized
        # state makes Qt paint at the size hint first and then resize, which is
        # the flash you see.
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.availableGeometry())
        # Keep the title bar so the window can still be closed on any platform;
        # true full screen traps a user on macOS.
        self.setWindowState(Qt.WindowMaximized)

        # Outer layout exists only to centre the card in the middle of the screen.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._build_card())
        row.addStretch(1)
        outer.addLayout(row)

        outer.addStretch(1)
        outer.addWidget(self._build_footer())

        self.username.setFocus()

    # ------------------------------------------------------------- building

    def _build_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("loginCard")
        card.setFixedWidth(CARD_WIDTH)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(38, 36, 38, 36)
        layout.setSpacing(10)

        title = QLabel(__app_name__)
        title.setObjectName("loginTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Point of Sale &amp; Inventory")
        subtitle.setObjectName("loginSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)
        layout.addSpacing(22)

        self.username = QLineEdit()
        self.username.setPlaceholderText("Username")
        self.username.setMinimumHeight(42)
        layout.addWidget(self.username)

        self.password = QLineEdit()
        self.password.setPlaceholderText("Password")
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setMinimumHeight(42)
        layout.addWidget(self.password)

        self.error = QLabel("")
        self.error.setObjectName("loginError")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        layout.addWidget(self.error)

        layout.addSpacing(4)
        self.sign_in = QPushButton("Sign in")
        self.sign_in.setProperty("primary", True)
        self.sign_in.setMinimumHeight(44)
        self.sign_in.setDefault(True)
        self.sign_in.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.sign_in)

        # Enter submits from either field.
        self.sign_in.clicked.connect(self._attempt)
        self.username.returnPressed.connect(self._attempt)
        self.password.returnPressed.connect(self._attempt)
        return card

    def _build_footer(self) -> QWidget:
        footer = QLabel(f"{__app_name__} {__version__}")
        footer.setObjectName("loginFooter")
        footer.setAlignment(Qt.AlignCenter)
        footer.setContentsMargins(0, 0, 0, 18)
        return footer

    # ------------------------------------------------------------ behaviour

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(True)
        self.password.selectAll()
        self.password.setFocus()

    def _attempt(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        if not username or not password:
            self._show_error("Enter your username and password.")
            return

        self.sign_in.setEnabled(False)
        self.sign_in.setText("Signing in…")
        try:
            self.user = self._auth.login(username, password)
        except SmartPOSError as exc:
            self._show_error(str(exc))
            return
        except Exception:
            log.exception("Unexpected error during sign in")
            QMessageBox.critical(
                self, "Sign in failed",
                "Something went wrong signing in. The details were written to "
                "the log file.",
            )
            return
        finally:
            self.sign_in.setEnabled(True)
            self.sign_in.setText("Sign in")

        # Hand off while still visible: the listener puts the main window up,
        # and only then does this dialog close behind it.
        self.signed_in.emit(self.user)
        self.accept()
