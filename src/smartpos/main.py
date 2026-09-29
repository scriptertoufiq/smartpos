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


def _run_gui() -> int:
    """Show the login dialog and, on success, the main window."""
    # Imported here so `--check` and the CLI scripts never need a display.
    from PyQt5.QtWidgets import QApplication

    from smartpos.services.auth_service import AuthService
    from smartpos.ui.login_dialog import LoginDialog
    from smartpos.ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")

    stylesheet = Path(__file__).resolve().parent / "ui" / "resources" / "styles.qss"
    if stylesheet.is_file():
        app.setStyleSheet(stylesheet.read_text(encoding="utf-8"))

    auth = AuthService()

    # Sign in, work, sign out, sign in again: the loop a shared counter needs.
    # Closing the login dialog is how you leave the application.
    while True:
        built: dict[str, MainWindow] = {}

        def _hand_off(_user, _built=built) -> None:
            """Put the main window up while the login dialog still covers the
            screen. Building it after the dialog closed left a frame of bare
            desktop showing through -- that gap is the flicker."""
            window = MainWindow(auth)
            window.show()
            window.raise_()
            _built["window"] = window

        login = LoginDialog(auth)
        login.signed_in.connect(_hand_off)
        accepted = login.exec_() == login.Accepted
        login.deleteLater()

        if not accepted:
            log.info("Sign in cancelled; exiting")
            return EXIT_OK

        window = built.get("window") or MainWindow(auth)
        if not window.isVisible():
            window.show()
        window.activateWindow()

        exit_code = app.exec_()
        if not window.sign_out_requested:
            return exit_code

        # Drop the window before reopening the dialog so nothing of the previous
        # session is still on screen or holding a reference to its user.
        window.deleteLater()
        app.processEvents()
        log.info("Signed out; returning to the login screen")


if __name__ == "__main__":
    sys.exit(main())
