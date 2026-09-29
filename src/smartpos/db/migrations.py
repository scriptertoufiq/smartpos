"""Applies the .sql files in docker/mysql/init to a database.

The Docker container runs those files automatically the first time it starts.
This module exists for the case where the database is not created by Docker --
an existing MySQL server on the shop's machine, or a test database.

Like the container, it only initialises an empty schema. It is not a migration
framework: there is no version table and no down-migration, because the schema
is created once and then evolved by editing the files before the next install.
"""

from __future__ import annotations

from pathlib import Path

import mysql.connector

from smartpos.core.exceptions import DatabaseError
from smartpos.core.logging import get_logger
from smartpos.db.connection import get_connection, transaction

log = get_logger(__name__)

# src/smartpos/db/migrations.py -> src/smartpos/db -> src/smartpos -> src -> root
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "docker" / "mysql" / "init"


def list_migrations() -> list[Path]:
    """Return the .sql files in filename order -- the order MySQL applies them."""
    if not MIGRATIONS_DIR.is_dir():
        raise DatabaseError(f"No migrations directory at {MIGRATIONS_DIR}")
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def split_statements(sql: str) -> list[str]:
    """Split a script into statements.

    Semicolons inside quoted strings and inside `--` comments are not
    separators; the schema contains both, so a naive split on ';' corrupts it.
    """
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    in_line_comment = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                current.append(ch)
            i += 1
            continue

        if quote:
            current.append(ch)
            if ch == "\\":  # escaped character inside a string
                if nxt:
                    current.append(nxt)
                    i += 1
            elif ch == quote:
                quote = None
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line_comment = True
            i += 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            current.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def schema_is_empty(conn=None) -> bool:
    """True if the connected database holds no tables."""

    def _check(c) -> bool:
        cursor = c.cursor()
        try:
            cursor.execute("SHOW TABLES")
            return not cursor.fetchall()
        finally:
            cursor.close()

    if conn is not None:
        return _check(conn)
    with get_connection() as own:
        return _check(own)


def apply_migrations(force: bool = False) -> list[str]:
    """Create the schema. Returns the names of the files applied.

    Refuses to run against a database that already has tables, the way the
    Docker entrypoint only initialises an empty volume. `force` skips that
    check; it does not drop anything, so it will fail on objects that exist.
    """
    files = list_migrations()
    if not files:
        raise DatabaseError(f"No .sql files found in {MIGRATIONS_DIR}")

    with transaction() as conn:
        if not force and not schema_is_empty(conn):
            raise DatabaseError(
                "The database already contains tables. Applying the schema over "
                "an existing one is refused; drop it first, or pass force=True."
            )

    applied: list[str] = []
    for path in files:
        statements = split_statements(path.read_text(encoding="utf-8"))
        log.info("Applying %s (%d statements)", path.name, len(statements))
        # One transaction per file. MySQL commits DDL implicitly, so this does
        # not make a file atomic -- it keeps the failure message pointing at the
        # file and statement that actually broke.
        with transaction() as conn:
            cursor = conn.cursor()
            try:
                for n, stmt in enumerate(statements, start=1):
                    try:
                        cursor.execute(stmt)
                        cursor.fetchall() if cursor.with_rows else None
                    except mysql.connector.Error as exc:
                        first_line = stmt.strip().splitlines()[0][:100]
                        raise DatabaseError(
                            f"{path.name}, statement {n} failed: {exc.msg or exc}\n"
                            f"  {first_line}"
                        ) from exc
            finally:
                cursor.close()
        applied.append(path.name)

    log.info("Schema created from %d files", len(applied))
    return applied


def describe_schema() -> dict[str, int]:
    """Table name -> column count, for verifying what was created."""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT TABLE_NAME, COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() GROUP BY TABLE_NAME ORDER BY TABLE_NAME"
            )
            return {name: count for name, count in cursor.fetchall()}
        finally:
            cursor.close()
