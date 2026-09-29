"""Application settings, loaded from the environment / .env file.

Nothing else in the codebase should read os.environ directly; everything goes
through `get_settings()` so that tests can substitute their own values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from smartpos.core.exceptions import ConfigurationError

# src/smartpos/config.py -> src/smartpos -> src -> the project root
BASE_DIR = Path(__file__).resolve().parents[2]

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True)
class DatabaseSettings:
    """Connection details for the MySQL server."""

    host: str
    port: int
    name: str
    user: str
    password: str
    pool_size: int

    @property
    def dsn(self) -> str:
        """Human-readable target, safe to log -- carries no password."""
        return f"{self.user}@{self.host}:{self.port}/{self.name}"


@dataclass(frozen=True)
class Settings:
    """Everything the application needs to know at startup."""

    env: str
    log_level: str
    database: DatabaseSettings
    backup_dir: Path
    log_dir: Path
    currency_symbol: str
    default_tax_rate: float
    mysqldump_path: str
    mysql_client_path: str

    @property
    def is_development(self) -> bool:
        return self.env == "development"


def _require(key: str) -> str:
    """Read a variable that has no sensible default."""
    value = os.environ.get(key, "").strip()
    if not value:
        raise ConfigurationError(
            f"{key} is not set. Copy .env.example to .env and fill it in."
        )
    return value


def _get(key: str, default: str) -> str:
    value = os.environ.get(key, "").strip()
    return value or default


def _get_int(key: str, default: int) -> int:
    raw = _get(key, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be a whole number, got {raw!r}.") from exc


def _get_float(key: str, default: float) -> float:
    raw = _get(key, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be a number, got {raw!r}.") from exc


def _resolve_dir(raw: str) -> Path:
    """Relative paths in .env are relative to the project root, not the cwd."""
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


def load_settings(env_file: Path | None = None) -> Settings:
    """Read .env plus the process environment and build a `Settings`.

    Real environment variables win over the file, so a deployment can override
    a single value without editing .env.
    """
    load_dotenv(env_file or (BASE_DIR / ".env"), override=False)

    log_level = _get("LOG_LEVEL", "INFO").upper()
    if log_level not in _VALID_LOG_LEVELS:
        raise ConfigurationError(
            f"LOG_LEVEL must be one of {', '.join(sorted(_VALID_LOG_LEVELS))}, "
            f"got {log_level!r}."
        )

    pool_size = _get_int("DB_POOL_SIZE", 5)
    if not 1 <= pool_size <= 32:
        # MySQL's own connector caps a pool at 32.
        raise ConfigurationError(f"DB_POOL_SIZE must be between 1 and 32, got {pool_size}.")

    database = DatabaseSettings(
        host=_get("DB_HOST", "127.0.0.1"),
        port=_get_int("DB_PORT", 3306),
        name=_require("DB_NAME"),
        user=_require("DB_USER"),
        password=os.environ.get("DB_PASSWORD", ""),
        pool_size=pool_size,
    )

    return Settings(
        env=_get("APP_ENV", "development"),
        log_level=log_level,
        database=database,
        backup_dir=_resolve_dir(_get("BACKUP_DIR", "./backups")),
        log_dir=_resolve_dir(_get("LOG_DIR", "./logs")),
        currency_symbol=_get("CURRENCY_SYMBOL", "BDT"),
        default_tax_rate=_get_float("DEFAULT_TAX_RATE", 0.0),
        mysqldump_path=_get("MYSQLDUMP_PATH", "mysqldump"),
        mysql_client_path=_get("MYSQL_CLIENT_PATH", "mysql"),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide settings, loading them on first use."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def set_settings(settings: Settings | None) -> None:
    """Replace the cached settings. For tests; not used by the application."""
    global _settings
    _settings = settings
