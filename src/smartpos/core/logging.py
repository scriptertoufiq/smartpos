"""Logging setup.

Writes to both the console and a rotating file under `logs/`, so a problem at
the counter can be diagnosed after the fact.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_CONSOLE_FORMAT = "%(levelname)-8s %(name)-28s %(message)s"
_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)-28s %(message)s"

_MAX_BYTES = 2 * 1024 * 1024  # 2 MB per file
_BACKUP_COUNT = 5  # ~10 MB of history, enough to cover a trading week

_configured = False


def configure_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """Install the console and rotating-file handlers on the root logger.

    Calling this twice is harmless -- the second call is ignored rather than
    doubling every message.
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    root.addHandler(console)

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_dir / "smartpos.log",
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
            root.addHandler(file_handler)
        except OSError as exc:
            # An unwritable log directory must not stop the shop trading.
            root.warning("File logging disabled: cannot write to %s (%s)", log_dir, exc)

    # The connector logs every statement at DEBUG, which drowns everything else.
    logging.getLogger("mysql.connector").setLevel(logging.WARNING)

    _configured = True


def reset_logging() -> None:
    """Forget that logging was configured. For tests."""
    global _configured
    _configured = False


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger."""
    return logging.getLogger(name)
