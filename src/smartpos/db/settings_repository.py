"""Queries against the `settings` key-value table."""

from __future__ import annotations

from typing import Any

from smartpos.db.repository import BaseRepository


class SettingsRepository(BaseRepository):
    table = "settings"

    def all(self, conn: Any = None) -> dict[str, str]:
        rows = self.fetch_all(
            "SELECT setting_key, setting_value FROM settings", (), conn)
        return {r["setting_key"]: (r["setting_value"] or "") for r in rows}

    def get(self, key: str, conn: Any = None) -> str | None:
        value = self.fetch_value(
            "SELECT setting_value FROM settings WHERE setting_key = %s", (key,), conn)
        return value if value is not None else None

    def put(self, key: str, value: str, conn: Any) -> None:
        """Insert or update. One statement, so two screens saving at once
        cannot leave a key missing."""
        self.execute(
            "INSERT INTO settings (setting_key, setting_value) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)",
            (key, value), conn)
