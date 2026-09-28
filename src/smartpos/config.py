"""Application settings, loaded from the environment / .env file.

Nothing else in the codebase should read os.environ directly; everything goes
through `get_settings()` so that tests can substitute their own values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DatabaseSettings:
    """Connection details for the MySQL server."""

    host: str
    port: int
    name: str
    user: str
    password: str
    pool_size: int


@dataclass(frozen=True)
class Settings:
    """Everything the application needs to know at startup."""

    env: str
    log_level: str
    database: DatabaseSettings
    backup_dir: Path
    currency_symbol: str
    default_tax_rate: float


def load_settings(env_file: Path | None = None) -> Settings:
    """Read .env plus the process environment and build a `Settings`."""
    raise NotImplementedError


def get_settings() -> Settings:
    """Return the process-wide settings, loading them on first use."""
    raise NotImplementedError
