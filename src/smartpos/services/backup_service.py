"""Database backup and restore.

The dump is written by this process reading the database through its own
connection, not by shelling out to `mysqldump`. That tool is simply not present
on many machines -- it is absent from the development machine this was built on,
and a shop running MySQL from an installer or a container often has the server
without the client utilities. A backup that only works where someone happened to
install a command-line tool is not a backup.

If `mysqldump` *is* available it is used instead, because it is faster and its
output is the canonical format. The Python path produces a file the same
`mysql` client could replay.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from smartpos.config import get_settings
from smartpos.core.exceptions import DatabaseError, ValidationError
from smartpos.core.logging import get_logger
from smartpos.db.connection import get_connection, transaction
from smartpos.db.migrations import split_statements
from smartpos.db.user_repository import ActivityLogRepository
from smartpos.services.auth_service import AuthService
from smartpos.services.settings_service import SettingsService

log = get_logger(__name__)

MANAGE = "backup.manage"
CONFIRMATION = "CONFIRM"
FILE_PREFIX = "smartpos_"
ROWS_PER_INSERT = 200


def _literal(value) -> str:
    """One value as SQL text. Everything the schema can hold is covered."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "0x" + value.hex() if value else "''"
    if isinstance(value, datetime):
        return "'" + value.strftime("%Y-%m-%d %H:%M:%S") + "'"
    text = str(value)
    # Order matters: the backslash has to be escaped before anything that
    # introduces one.
    for find, replace in (("\\", "\\\\"), ("'", "\\'"), ("\n", "\\n"),
                          ("\r", "\\r"), ("\x00", "\\0"), ("\x1a", "\\Z")):
        text = text.replace(find, replace)
    return f"'{text}'"


class BackupService:
    def __init__(self, auth: AuthService | None = None) -> None:
        self._audit = ActivityLogRepository()
        self._auth = auth

    # ------------------------------------------------------------------ reads

    def backup_dir(self) -> Path:
        return get_settings().backup_dir

    def list_backups(self) -> list[dict]:
        """Newest first, with the size and age a person can judge."""
        directory = self.backup_dir()
        if not directory.is_dir():
            return []
        files = []
        for path in directory.glob(f"{FILE_PREFIX}*.sql"):
            stat = path.stat()
            files.append({
                "path": path,
                "name": path.name,
                "size_kb": round(stat.st_size / 1024, 1),
                "taken_at": datetime.fromtimestamp(stat.st_mtime),
            })
        return sorted(files, key=lambda f: f["taken_at"], reverse=True)

    def mysqldump_available(self) -> bool:
        return self._mysqldump_path() is not None

    # ----------------------------------------------------------------- backup

    def create_backup(self, destination: Path | None = None) -> Path:
        """Write a dump and return the file. Timestamped, never overwritten."""
        actor = self._require()
        settings = get_settings()

        target = destination or (
            self.backup_dir()
            / f"{FILE_PREFIX}{datetime.now():%Y%m%d_%H%M%S}.sql")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValidationError(
                f"Cannot write to {target.parent}: {exc.strerror or exc}. "
                "Choose another folder in Settings.") from exc

        dump = self._mysqldump_path()
        try:
            if dump is not None:
                self._dump_with_mysqldump(dump, target, settings)
            else:
                self._dump_with_python(target, settings)
        except ValidationError:
            raise
        except OSError as exc:
            raise ValidationError(
                f"The backup could not be written: {exc.strerror or exc}") from exc

        if not target.is_file() or target.stat().st_size == 0:
            raise ValidationError(
                "The backup file came out empty; nothing was saved.")

        with transaction() as conn:
            self._audit.record(actor.id, "backup.create", "settings", None,
                               {"file": target.name,
                                "bytes": target.stat().st_size}, conn)
        log.info("Backup written: %s (%d bytes)", target, target.stat().st_size)
        return target

    def _dump_with_python(self, target: Path, settings) -> None:
        """Read the schema and every row through the application's own connection."""
        lines = [
            "-- SmartPOS backup",
            f"-- taken {datetime.now():%Y-%m-%d %H:%M:%S} from {settings.database.name}",
            "-- restore with: python scripts/restore_backup.py <this file>",
            "",
            "SET NAMES utf8mb4;",
            "SET FOREIGN_KEY_CHECKS = 0;",
            "SET SQL_MODE = 'NO_AUTO_VALUE_ON_ZERO';",
            "",
        ]

        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME")
                objects = cursor.fetchall()
                tables = [name for name, kind in objects if kind == "BASE TABLE"]
                views = [name for name, kind in objects if kind == "VIEW"]

                for table in tables:
                    cursor.execute(f"SHOW CREATE TABLE `{table}`")
                    create = cursor.fetchone()[1]
                    lines += [f"DROP TABLE IF EXISTS `{table}`;", create + ";", ""]

                    cursor.execute(f"SELECT * FROM `{table}`")
                    rows = cursor.fetchall()
                    if not rows:
                        continue
                    columns = ", ".join(f"`{c[0]}`" for c in cursor.description)
                    for start in range(0, len(rows), ROWS_PER_INSERT):
                        chunk = rows[start:start + ROWS_PER_INSERT]
                        values = ",\n".join(
                            "(" + ", ".join(_literal(v) for v in row) + ")"
                            for row in chunk)
                        lines.append(
                            f"INSERT INTO `{table}` ({columns}) VALUES\n{values};")
                    lines.append("")

                # Views last: they reference the tables above.
                for view in views:
                    cursor.execute(f"SHOW CREATE VIEW `{view}`")
                    create = cursor.fetchone()[1]
                    lines += [f"DROP VIEW IF EXISTS `{view}`;", create + ";", ""]
            finally:
                cursor.close()

        lines += ["SET FOREIGN_KEY_CHECKS = 1;", ""]
        target.write_text("\n".join(lines), encoding="utf-8")

    def _dump_with_mysqldump(self, dump: str, target: Path, settings) -> None:
        database = settings.database
        command = [
            dump, f"--host={database.host}", f"--port={database.port}",
            f"--user={database.user}", "--single-transaction", "--routines",
            "--default-character-set=utf8mb4", "--add-drop-table", database.name,
        ]
        env_password = {"MYSQL_PWD": database.password} if database.password else {}
        try:
            import os
            result = subprocess.run(
                command, capture_output=True, timeout=600,
                env={**os.environ, **env_password})
        except subprocess.TimeoutExpired as exc:
            raise ValidationError(
                "mysqldump did not finish within ten minutes.") from exc

        if result.returncode != 0:
            message = result.stderr.decode(errors="replace").strip()
            raise ValidationError(f"mysqldump failed: {message or 'no detail given'}")
        target.write_bytes(result.stdout)

    # ---------------------------------------------------------------- restore

    def restore_backup(self, dump_file: Path, confirmation: str = "") -> dict:
        """Replace the current database with a dump. Destructive.

        Refuses unless `confirmation` is the word CONFIRM, and takes a safety
        copy of the current database first, so a restore of the wrong file is
        itself recoverable.
        """
        actor = self._require()

        if confirmation != CONFIRMATION:
            raise ValidationError(
                f"A restore replaces every row in the database. Type "
                f"{CONFIRMATION} to go ahead.")
        if not dump_file.is_file():
            raise ValidationError(f"No such backup file: {dump_file}")
        text = dump_file.read_text(encoding="utf-8", errors="replace")
        if "CREATE TABLE" not in text.upper():
            raise ValidationError(
                f"{dump_file.name} does not look like a SmartPOS backup — it "
                "contains no tables.")

        safety = (self.backup_dir()
                  / f"{FILE_PREFIX}before_restore_{datetime.now():%Y%m%d_%H%M%S}.sql")
        self._dump_with_python(safety, get_settings())
        log.info("Safety copy taken before restore: %s", safety)

        statements = split_statements(text)
        applied = 0
        with transaction() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
                for number, statement in enumerate(statements, start=1):
                    try:
                        cursor.execute(statement)
                        if cursor.with_rows:
                            cursor.fetchall()
                        applied += 1
                    except Exception as exc:                      # noqa: BLE001
                        first = statement.strip().splitlines()[0][:110]
                        raise DatabaseError(
                            f"The restore stopped at statement {number}: {exc}\n"
                            f"  {first}\n"
                            f"The database was left as it was; the copy taken "
                            f"beforehand is at {safety}.") from exc
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            finally:
                cursor.close()

        SettingsService.invalidate()
        log.info("Restored %s (%d statements)", dump_file.name, applied)
        return {"file": dump_file, "statements": applied, "safety_copy": safety}

    def delete_backup(self, path: Path) -> None:
        self._require()
        if path.parent != self.backup_dir():
            raise ValidationError("Only files in the backup folder can be removed.")
        try:
            path.unlink()
        except OSError as exc:
            raise ValidationError(f"Could not delete {path.name}: {exc}") from exc

    # -------------------------------------------------------------- internals

    @staticmethod
    def _mysqldump_path() -> str | None:
        configured = get_settings().mysqldump_path
        if configured and configured != "mysqldump":
            return configured if Path(configured).is_file() else None
        return shutil.which("mysqldump")

    def _require(self):
        if self._auth is None:
            raise ValidationError(
                "This action needs a signed-in user; the service was created "
                "without one.")
        return self._auth.require(MANAGE)
