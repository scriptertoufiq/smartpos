"""MySQL connection pool and transaction helpers.

Queries are written by hand with mysql-connector; there is no ORM. Every write
that touches more than one table (a sale and its items and its stock movements,
for example) must run inside `transaction()`.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import mysql.connector
from mysql.connector import errorcode
from mysql.connector.pooling import MySQLConnectionPool

from smartpos.config import get_settings
from smartpos.core.exceptions import DatabaseError
from smartpos.core.logging import get_logger

log = get_logger(__name__)

_pool: MySQLConnectionPool | None = None
_lock = threading.Lock()

POOL_NAME = "smartpos"

# MySQL errors that mean "the credentials or the database are wrong", as opposed
# to "the server is unreachable". Worth telling apart in the message.
_ACCESS_ERRORS = {errorcode.ER_ACCESS_DENIED_ERROR, errorcode.ER_DBACCESS_DENIED_ERROR}
_UNKNOWN_DB = errorcode.ER_BAD_DB_ERROR

# Deadlock and lock-wait timeout. A caller may retry the whole transaction.
DEADLOCK_ERRORS = {1213, 1205}


def _describe(exc: mysql.connector.Error) -> str:
    """Turn a connector error into something a shopkeeper can act on."""
    settings = get_settings().database
    code = getattr(exc, "errno", None)
    if code in _ACCESS_ERRORS:
        return (
            f"MySQL refused the credentials for {settings.dsn}. "
            "Check DB_USER and DB_PASSWORD in .env."
        )
    if code == _UNKNOWN_DB:
        return (
            f"MySQL has no database named {settings.name!r}. "
            "Start it with `docker compose up -d`, or run scripts/init_db.py."
        )
    if code == errorcode.CR_CONN_HOST_ERROR or code == 2003:
        return (
            f"Cannot reach MySQL at {settings.host}:{settings.port}. "
            "Is the server running? `docker compose up -d` starts it."
        )
    return f"MySQL error {code}: {exc.msg or exc}"


def init_pool() -> None:
    """Create the connection pool from the configured database settings."""
    global _pool
    with _lock:
        if _pool is not None:
            return
        settings = get_settings().database
        try:
            _pool = MySQLConnectionPool(
                pool_name=POOL_NAME,
                pool_size=settings.pool_size,
                pool_reset_session=True,
                host=settings.host,
                port=settings.port,
                database=settings.name,
                user=settings.user,
                password=settings.password,
                charset="utf8mb4",
                collation="utf8mb4_unicode_ci",
                autocommit=False,
                time_zone="+00:00",
                use_pure=True,
            )
        except mysql.connector.Error as exc:
            raise DatabaseError(_describe(exc)) from exc
        log.info("Connection pool ready (%s, size %d)", settings.dsn, settings.pool_size)


def close_pool() -> None:
    """Close every pooled connection. Called once on application exit."""
    global _pool
    with _lock:
        if _pool is None:
            return
        try:
            # The connector exposes no public close-all; this is the documented
            # internal queue of pooled connections.
            while True:
                conn = _pool._cnx_queue.get_nowait()
                try:
                    conn.close()
                except mysql.connector.Error:
                    pass
        except Exception:
            pass
        _pool = None
        log.info("Connection pool closed")


def is_initialised() -> bool:
    return _pool is not None


@contextmanager
def get_connection() -> Iterator[Any]:
    """Borrow a connection from the pool and return it when the block ends.

    The connection is returned to the pool whether or not the block raised.
    """
    if _pool is None:
        raise DatabaseError("Connection pool is not initialised. Call init_pool() first.")
    try:
        conn = _pool.get_connection()
    except mysql.connector.Error as exc:
        raise DatabaseError(_describe(exc)) from exc
    try:
        yield conn
    finally:
        try:
            conn.close()  # returns it to the pool rather than closing the socket
        except mysql.connector.Error:
            pass


@contextmanager
def transaction() -> Iterator[Any]:
    """Run a block inside one transaction: commit on success, roll back on error.

    This is the only place a transaction is opened. Repositories and views must
    never start their own -- a repository that did would silently break the
    atomicity of whatever called it.
    """
    with get_connection() as conn:
        try:
            conn.start_transaction()
        except mysql.connector.Error as exc:
            raise DatabaseError(_describe(exc)) from exc
        try:
            yield conn
        except Exception:
            try:
                conn.rollback()
            except mysql.connector.Error as rollback_exc:
                log.error("Rollback failed: %s", rollback_exc)
            raise
        else:
            try:
                conn.commit()
            except mysql.connector.Error as exc:
                try:
                    conn.rollback()
                except mysql.connector.Error:
                    pass
                raise DatabaseError(_describe(exc)) from exc


def check_connection() -> bool:
    """Return True if the server answers, used by the startup health check."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT 1")
                cursor.fetchall()
            finally:
                cursor.close()
        return True
    except DatabaseError:
        raise
    except mysql.connector.Error as exc:
        raise DatabaseError(_describe(exc)) from exc


def server_version() -> str:
    """The MySQL version string, for the startup log line."""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT VERSION()")
            row = cursor.fetchone()
            return row[0] if row else "unknown"
        finally:
            cursor.close()
