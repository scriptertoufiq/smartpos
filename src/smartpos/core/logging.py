"""Logging setup.

Writes to both the console and a rotating file under `logs/`, so a problem at
the counter can be diagnosed after the fact.
"""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """Install the console and rotating-file handlers on the root logger."""
    raise NotImplementedError


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger."""
    return logging.getLogger(name)
