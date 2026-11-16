"""The sign-in screen.

A page inside the main window, not a window of its own.

It used to be a QDialog, which meant signing in and signing out each threw away
the window you were looking at and built a fresh one -- and a fresh window opens
in whatever state its constructor asks for, not the state you had put it in. If
you were full screen, you were dropped out of it (on macOS, out of its Space
entirely) twice per shift change. Keeping one window for the whole run of the
application makes that impossible by construction: there is nothing to restore
because nothing is ever replaced.

The card stays centred on an otherwise empty screen, so the first thing a
cashier sees at the start of a shift is the one thing they need to do.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
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


class LoginPage(QWidget):
    """Collects a username and password and hands them to AuthService.

    Emits `signed_in` with the User once the credentials are accepted. The
    window listening for it swaps itself over to the application shell.
    """

    signed_in = pyqtSignal(object)

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self.user: User | None = None
        self.setObjectName("loginWindow")
        # A plain QWidget ignores a stylesheet background unless it is told to
        # paint one; without this the sign-in page shows the window's grey.
        self.setAttribute(Qt.WA_StyledBackground, True)

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

        subtitle = QLabel("Point of Sale & Inventory")
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
        self.sign_in.setAutoDefault(True)
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

    def reset(self) -> None:
        """Blank the form, ready for the next person on the counter."""
        self.user = None
        self.username.clear()
        self.password.clear()
        self.error.clear()
        self.error.setVisible(False)
        self.sign_in.setEnabled(True)
        self.sign_in.setText("Sign in")
        self.username.setFocus()

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

        self.signed_in.emit(self.user)
