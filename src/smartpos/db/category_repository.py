"""Queries against `categories` and `units`."""

from __future__ import annotations

from typing import Any

from smartpos.db.repository import BaseRepository, Row

_COLUMNS = "id, parent_id, name, is_active, created_at, updated_at"


class CategoryRepository(BaseRepository):
    table = "categories"

    # ------------------------------------------------------------------ reads

    def get(self, category_id: int, conn: Any = None) -> Row | None:
        return self.fetch_one(
            f"SELECT {_COLUMNS} FROM categories WHERE id = %s", (category_id,), conn
        )

    def list_all(self, active_only: bool = False, conn: Any = None) -> list[Row]:
        sql = f"SELECT {_COLUMNS} FROM categories"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY name"
        return self.fetch_all(sql, (), conn)

    def children_of(self, parent_id: int | None, conn: Any = None) -> list[Row]:
        if parent_id is None:
            sql = f"SELECT {_COLUMNS} FROM categories WHERE parent_id IS NULL ORDER BY name"
            return self.fetch_all(sql, (), conn)
        sql = f"SELECT {_COLUMNS} FROM categories WHERE parent_id = %s ORDER BY name"
        return self.fetch_all(sql, (parent_id,), conn)

    def name_taken(self, name: str, parent_id: int | None,
                   exclude_id: int | None = None, conn: Any = None) -> bool:
        """Names must be unique among siblings, not globally.

        'Rice' under Grocery and 'Rice' under Wholesale are different things.
        """
        sql = "SELECT 1 FROM categories WHERE name = %s AND "
        params: list[Any] = [name]
        if parent_id is None:
            sql += "parent_id IS NULL"
        else:
            sql += "parent_id = %s"
            params.append(parent_id)
        if exclude_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_id)
        return self.fetch_one(sql + " LIMIT 1", tuple(params), conn) is not None

    def child_count(self, category_id: int, conn: Any = None) -> int:
        return int(self.fetch_value(
            "SELECT COUNT(*) AS n FROM categories WHERE parent_id = %s",
            (category_id,), conn) or 0)

    def product_count(self, category_id: int, conn: Any = None) -> int:
        return int(self.fetch_value(
            "SELECT COUNT(*) AS n FROM products WHERE category_id = %s",
            (category_id,), conn) or 0)

    def sample_products(self, category_id: int, limit: int = 3,
                        conn: Any = None) -> list[str]:
        """A few product names, so a refusal can say what is blocking it."""
        rows = self.fetch_all(
            "SELECT name FROM products WHERE category_id = %s ORDER BY name LIMIT %s",
            (category_id, limit), conn)
        return [r["name"] for r in rows]

    def ancestors_of(self, category_id: int, conn: Any = None) -> list[int]:
        """Ids from the immediate parent upwards.

        Walks rather than recurses in SQL: the tree is two levels deep by
        design, and the loop guard protects against a cycle that a bad write
        could otherwise leave in the table.
        """
        seen: list[int] = []
        current = self.get(category_id, conn)
        while current and current["parent_id"] is not None:
            parent_id = current["parent_id"]
            if parent_id in seen:
                break  # a cycle already in the data; stop rather than spin
            seen.append(parent_id)
            current = self.get(parent_id, conn)
        return seen

    def set_active(self, category_id: int, active: bool, conn: Any = None) -> bool:
        return self.update(category_id, {"is_active": 1 if active else 0}, conn)


class UnitRepository(BaseRepository):
    table = "units"

    def list_all(self, conn: Any = None) -> list[Row]:
        return self.fetch_all(
            "SELECT id, name, short_name FROM units ORDER BY id", (), conn)

    def get(self, unit_id: int, conn: Any = None) -> Row | None:
        return self.fetch_one(
            "SELECT id, name, short_name FROM units WHERE id = %s", (unit_id,), conn)
