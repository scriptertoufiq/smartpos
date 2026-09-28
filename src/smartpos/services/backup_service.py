"""Database backup and restore."""

from __future__ import annotations

from pathlib import Path


class BackupService:
    def create_backup(self, destination: Path | None = None) -> Path:
        """Dump the database with mysqldump and return the file written."""
        raise NotImplementedError

    def restore_backup(self, dump_file: Path) -> None:
        """Replace the current database with a dump. Destructive -- confirm first."""
        raise NotImplementedError

    def list_backups(self) -> list[Path]:
        raise NotImplementedError
