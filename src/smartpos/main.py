"""Application entry point.

Wires together configuration, logging, the database connection and the Qt
application, then shows the login dialog followed by the main window.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Start SmartPOS and return the process exit code.

    Intended order of work:
        1. load settings from the environment (`smartpos.config`)
        2. configure logging (`smartpos.core.logging`)
        3. open the database connection pool (`smartpos.db.connection`)
        4. create the QApplication and apply the stylesheet
        5. show LoginDialog; on success show MainWindow
        6. close the pool on exit
    """
    raise NotImplementedError("Application bootstrap is not implemented yet.")


if __name__ == "__main__":
    sys.exit(main())
