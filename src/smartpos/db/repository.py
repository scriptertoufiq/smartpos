"""Shared query helpers that the per-entity repositories build on."""

from __future__ import annotations

from typing import Any


class BaseRepository:
    """Thin helper around the connection pool.

    Subclasses set `table` and add their own query methods; the CRUD helpers
    here cover the repetitive cases.
    """

    table: str = ""

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        """Run a SELECT and return the first row as a dict, or None."""
        raise NotImplementedError

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Run a SELECT and return every row."""
        raise NotImplementedError

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Run an INSERT/UPDATE/DELETE and return the affected row count."""
        raise NotImplementedError

    def insert(self, data: dict[str, Any]) -> int:
        """Insert one row into `table` and return its new primary key."""
        raise NotImplementedError

    def update(self, record_id: int, data: dict[str, Any]) -> bool:
        """Update one row of `table` by primary key."""
        raise NotImplementedError

    def delete(self, record_id: int) -> bool:
        """Delete one row of `table` by primary key."""
        raise NotImplementedError
