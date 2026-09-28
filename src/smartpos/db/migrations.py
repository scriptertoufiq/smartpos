"""Applies the .sql files in docker/mysql/init to a database.

The Docker container runs those files automatically the first time it starts.
This module exists for the case where the database is not created by Docker --
an existing MySQL server on the shop's machine, or a test database.
"""

from __future__ import annotations

from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "docker" / "mysql" / "init"


def list_migrations() -> list[Path]:
    """Return the .sql files in filename order."""
    raise NotImplementedError


def apply_migrations(target_database: str | None = None) -> None:
    """Run every migration that has not been applied yet."""
    raise NotImplementedError
