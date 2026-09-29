"""Queries against `warehouses`."""

from __future__ import annotations

from typing import Any

from smartpos.db.repository import BaseRepository, Row

_COLUMNS = "id, code, name, address, phone, is_active, created_at, updated_at"


class WarehouseRepository(BaseRepository):
    table = "warehouses"

    def get(self, warehouse_id: int, conn: Any = None) -> Row | None:
        return self.fetch_one(
            f"SELECT {_COLUMNS} FROM warehouses WHERE id = %s", (warehouse_id,), conn
        )

    def get_by_code(self, code: str, conn: Any = None) -> Row | None:
        return self.fetch_one(
            f"SELECT {_COLUMNS} FROM warehouses WHERE code = %s", (code,), conn
        )

    def list_all(self, active_only: bool = True, conn: Any = None) -> list[Row]:
        sql = f"SELECT {_COLUMNS} FROM warehouses"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY is_active DESC, name"
        return self.fetch_all(sql, (), conn)

    def code_taken(self, code: str, exclude_id: int | None = None,
                   conn: Any = None) -> bool:
        sql = "SELECT 1 FROM warehouses WHERE code = %s"
        params: list[Any] = [code]
        if exclude_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_id)
        return self.fetch_one(sql + " LIMIT 1", tuple(params), conn) is not None

    def stock_on_hand(self, warehouse_id: int, conn: Any = None) -> int:
        """How many products still hold a non-zero quantity here.

        Disabling an outlet that still holds goods would hide that stock from
        every report while the goods are physically still on its shelves.
        """
        return int(self.fetch_value(
            "SELECT COUNT(*) AS n FROM stock WHERE warehouse_id = %s AND quantity <> 0",
            (warehouse_id,), conn) or 0)

    def users_posted(self, warehouse_id: int, conn: Any = None) -> int:
        return int(self.fetch_value(
            "SELECT COUNT(*) AS n FROM users WHERE warehouse_id = %s AND is_active = 1",
            (warehouse_id,), conn) or 0)

    def set_active(self, warehouse_id: int, active: bool, conn: Any = None) -> bool:
        return self.update(warehouse_id, {"is_active": 1 if active else 0}, conn)

    def count_active(self, exclude_id: int | None = None, conn: Any = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM warehouses WHERE is_active = 1"
        params: list[Any] = []
        if exclude_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_id)
        return int(self.fetch_value(sql, tuple(params), conn) or 0)
