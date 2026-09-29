"""Shared query helpers that the per-entity repositories build on.

Every statement is parameterised. No method here ever formats a value into SQL,
and `table` is never taken from user input -- it is a class attribute the
developer sets.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import mysql.connector

from smartpos.core.exceptions import DatabaseError
from smartpos.core.logging import get_logger
from smartpos.db.connection import get_connection, transaction

log = get_logger(__name__)

Params = Sequence[Any]
Row = dict[str, Any]


class BaseRepository:
    """Thin helper around the connection pool.

    Subclasses set `table` and add their own query methods; the CRUD helpers
    here cover the repetitive cases.

    Every method accepts an optional open `conn`. Pass one when the call is part
    of a larger transaction -- a sale writing its items and its stock movements
    must not have each repository opening a transaction of its own.
    """

    table: str = ""

    # ------------------------------------------------------------------ reads

    def fetch_one(self, sql: str, params: Params = (), conn: Any = None) -> Row | None:
        """Run a SELECT and return the first row as a dict, or None."""
        rows = self._query(sql, params, conn, limit_one=True)
        return rows[0] if rows else None

    def fetch_all(self, sql: str, params: Params = (), conn: Any = None) -> list[Row]:
        """Run a SELECT and return every row."""
        return self._query(sql, params, conn, limit_one=False)

    def fetch_value(self, sql: str, params: Params = (), conn: Any = None) -> Any:
        """Run a SELECT that yields a single column and return it, or None."""
        row = self.fetch_one(sql, params, conn)
        return next(iter(row.values())) if row else None

    def exists(self, where: str, params: Params = (), conn: Any = None) -> bool:
        self._require_table()
        sql = f"SELECT 1 FROM `{self.table}` WHERE {where} LIMIT 1"
        return self.fetch_one(sql, params, conn) is not None

    # ----------------------------------------------------------------- writes

    def execute(self, sql: str, params: Params = (), conn: Any = None) -> int:
        """Run an INSERT/UPDATE/DELETE and return the affected row count."""
        if conn is not None:
            return self._execute_on(conn, sql, params)[0]
        with transaction() as own:
            return self._execute_on(own, sql, params)[0]

    def insert(self, data: dict[str, Any], conn: Any = None) -> int:
        """Insert one row into `table` and return its new primary key."""
        self._require_table()
        if not data:
            raise DatabaseError("insert() needs at least one column.")
        cols = ", ".join(f"`{c}`" for c in data)
        marks = ", ".join(["%s"] * len(data))
        sql = f"INSERT INTO `{self.table}` ({cols}) VALUES ({marks})"
        params = tuple(data.values())
        if conn is not None:
            return self._execute_on(conn, sql, params)[1]
        with transaction() as own:
            return self._execute_on(own, sql, params)[1]

    def update(self, record_id: int, data: dict[str, Any], conn: Any = None) -> bool:
        """Update one row of `table` by primary key."""
        self._require_table()
        if not data:
            return False
        assignments = ", ".join(f"`{c}` = %s" for c in data)
        sql = f"UPDATE `{self.table}` SET {assignments} WHERE id = %s"
        params = (*data.values(), record_id)
        return self.execute(sql, params, conn) > 0

    def delete(self, record_id: int, conn: Any = None) -> bool:
        """Delete one row of `table` by primary key.

        Master data is deactivated rather than deleted; this exists for the
        tables where a real delete is correct, such as a line item.
        """
        self._require_table()
        sql = f"DELETE FROM `{self.table}` WHERE id = %s"
        return self.execute(sql, (record_id,), conn) > 0

    def insert_many(self, rows: list[dict[str, Any]], conn: Any = None) -> int:
        """Insert several rows of the same shape. Returns the row count."""
        self._require_table()
        if not rows:
            return 0
        first = list(rows[0])
        if any(list(r) != first for r in rows):
            raise DatabaseError("insert_many() needs every row to have the same columns.")
        cols = ", ".join(f"`{c}`" for c in first)
        marks = ", ".join(["%s"] * len(first))
        sql = f"INSERT INTO `{self.table}` ({cols}) VALUES ({marks})"
        values = [tuple(r[c] for c in first) for r in rows]
        if conn is not None:
            return self._execute_many_on(conn, sql, values)
        with transaction() as own:
            return self._execute_many_on(own, sql, values)

    # --------------------------------------------------------------- internals

    def _require_table(self) -> None:
        if not self.table:
            raise DatabaseError(f"{type(self).__name__} has not set `table`.")

    def _query(self, sql: str, params: Params, conn: Any, limit_one: bool) -> list[Row]:
        if conn is not None:
            return self._query_on(conn, sql, params, limit_one)
        with get_connection() as own:
            return self._query_on(own, sql, params, limit_one)

    @staticmethod
    def _query_on(conn: Any, sql: str, params: Params, limit_one: bool) -> list[Row]:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(sql, tuple(params))
            rows = [cursor.fetchone()] if limit_one else cursor.fetchall()
            return [r for r in rows if r]
        except mysql.connector.Error as exc:
            log.error("Query failed: %s | %s", sql, exc)
            raise DatabaseError(f"Query failed: {exc.msg or exc}") from exc
        finally:
            # Unread rows would poison the next statement on this connection.
            try:
                cursor.fetchall()
            except mysql.connector.Error:
                pass
            cursor.close()

    @staticmethod
    def _execute_on(conn: Any, sql: str, params: Params) -> tuple[int, int]:
        """Returns (rowcount, lastrowid)."""
        cursor = conn.cursor()
        try:
            cursor.execute(sql, tuple(params))
            return cursor.rowcount, cursor.lastrowid
        except mysql.connector.Error as exc:
            log.error("Statement failed: %s | %s", sql, exc)
            raise DatabaseError(f"Statement failed: {exc.msg or exc}") from exc
        finally:
            cursor.close()

    @staticmethod
    def _execute_many_on(conn: Any, sql: str, values: list[tuple[Any, ...]]) -> int:
        cursor = conn.cursor()
        try:
            cursor.executemany(sql, values)
            return cursor.rowcount
        except mysql.connector.Error as exc:
            log.error("Batch failed: %s | %s", sql, exc)
            raise DatabaseError(f"Batch failed: {exc.msg or exc}") from exc
        finally:
            cursor.close()
