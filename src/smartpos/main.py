"""Application entry point.

Wires together configuration, logging, the database connection and the Qt
application, then shows the login dialog followed by the main window.
"""

from __future__ import annotations

import sys
from pathlib import Path

from smartpos import __app_name__, __version__
from smartpos.config import get_settings
from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import configure_logging, get_logger
from smartpos.db import connection

log = get_logger(__name__)

EXIT_OK = 0
EXIT_FAILURE = 1


def _startup() -> None:
    """Load settings, start logging, open the pool, prove the database answers."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_dir)

    log.info("%s %s starting (%s)", __app_name__, __version__, settings.env)
    connection.init_pool()

    connection.check_connection()
    log.info("MySQL %s reachable at %s", connection.server_version(), settings.database.dsn)


def _shutdown() -> None:
    connection.close_pool()
    log.info("%s stopped", __app_name__)


def main(argv: list[str] | None = None) -> int:
    """Start SmartPOS and return the process exit code."""
    argv = sys.argv[1:] if argv is None else argv

    try:
        _startup()
    except SmartPOSError as exc:
        # Expected failure: bad settings, unreachable server, wrong credentials.
        # The message is written for the person running the shop.
        print(f"{__app_name__} could not start.\n\n  {exc}\n", file=sys.stderr)
        return EXIT_FAILURE
    except Exception:
        log.exception("Unexpected error during startup")
        print(
            f"{__app_name__} could not start because of an unexpected error.\n"
            "The details were written to the log file.\n",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    try:
        if "--check" in argv:
            # Health check for CI and for diagnosing a shop machine.
            print(f"{__app_name__} {__version__}: database reachable.")
            return EXIT_OK
        return _run_gui()
    except SmartPOSError as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        _shutdown()


def _light_palette():
    """The palette the application paints with, whatever the desktop is set to.

    The stylesheet hard-codes light surfaces -- white cards, pale panels. A
    widget that takes its text colour from the desktop instead then paints white
    text on those white cards when the machine is in dark mode, and the labels
    simply vanish. Carrying our own palette means the application looks the same
    on every machine and nothing depends on a system setting.

    A proper dark theme would mean a second set of surface colours throughout
    the stylesheet; this is the honest interim, not a substitute for one.
    """
    from PyQt5.QtGui import QColor, QPalette

    ink = QColor("#111827")
    muted = QColor("#6b7280")
    surface = QColor("#ffffff")
    ground = QColor("#f5f6f8")
    accent = QColor("#2563eb")

    palette = QPalette()
    palette.setColor(QPalette.Window, ground)
    palette.setColor(QPalette.WindowText, ink)
    palette.setColor(QPalette.Base, surface)
    palette.setColor(QPalette.AlternateBase, QColor("#f9fafb"))
    palette.setColor(QPalette.Text, ink)
    palette.setColor(QPalette.Button, surface)
    palette.setColor(QPalette.ButtonText, ink)
    palette.setColor(QPalette.BrightText, QColor("#b91c1c"))
    palette.setColor(QPalette.ToolTipBase, QColor("#111827"))
    palette.setColor(QPalette.ToolTipText, surface)
    palette.setColor(QPalette.PlaceholderText, QColor("#9ca3af"))
    palette.setColor(QPalette.Highlight, accent)
    palette.setColor(QPalette.HighlightedText, surface)
    palette.setColor(QPalette.Link, accent)

    for role, colour in ((QPalette.WindowText, muted), (QPalette.Text, muted),
                         (QPalette.ButtonText, muted)):
        palette.setColor(QPalette.Disabled, role, colour)
    return palette


def _run_gui() -> int:
    """Open the one window the application has, starting on its sign-in page."""
    # Imported here so `--check` and the CLI scripts never need a display.
    from PyQt5.QtGui import QColor, QPalette
    from PyQt5.QtWidgets import QApplication

    from smartpos.services.auth_service import AuthService
    from smartpos.ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setPalette(_light_palette())

    stylesheet = Path(__file__).resolve().parent / "ui" / "resources" / "styles.qss"
    if stylesheet.is_file():
        app.setStyleSheet(stylesheet.read_text(encoding="utf-8"))

    auth = AuthService()

    # One window for the whole run. Signing in and out swap the page inside it,
    # which is what lets a maximised or full-screen window stay that way across
    # a shift change: there is no second window whose constructor could disagree
    # about the state, and nothing to rebuild and reposition.
    window = MainWindow(auth)
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
