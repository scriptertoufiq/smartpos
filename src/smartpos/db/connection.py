"""MySQL connection pool and transaction helpers.

Queries are written by hand with mysql-connector; there is no ORM. Every write
that touches more than one table (a sale and its items and its stock movements,
for example) must run inside `transaction()`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


def init_pool() -> None:
    """Create the connection pool from the configured database settings."""
    raise NotImplementedError


def close_pool() -> None:
    """Close every pooled connection. Called once on application exit."""
    raise NotImplementedError


@contextmanager
def get_connection() -> Iterator[Any]:
    """Borrow a connection from the pool and return it when the block ends."""
    raise NotImplementedError


@contextmanager
def transaction() -> Iterator[Any]:
    """Run a block inside one transaction: commit on success, roll back on error."""
    raise NotImplementedError


def check_connection() -> bool:
    """Return True if the server answers, used by the startup health check."""
    raise NotImplementedError
