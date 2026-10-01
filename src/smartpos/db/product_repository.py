"""Queries against `products`.

`get_by_barcode` is the counter's hot path: a scanner fires it on every item,
and it must stay a unique-index hit.
"""

from __future__ import annotations

from typing import Any

from smartpos.db.repository import BaseRepository, Row

_COLUMNS = (
    "p.id, p.sku, p.barcode, p.name, p.category_id, p.unit_id, "
    "p.purchase_price, p.sale_price, p.tax_rate, p.reorder_level, "
    "p.image_path, p.is_active, p.created_at, p.updated_at"
)
_JOINED = (
    f"SELECT {_COLUMNS}, c.name AS category_name, u.short_name AS unit_name "
    "FROM products p "
    "LEFT JOIN categories c ON c.id = p.category_id "
    "LEFT JOIN units u ON u.id = p.unit_id"
)


class ProductRepository(BaseRepository):
    table = "products"

    # ------------------------------------------------------------------ reads

    def get(self, product_id: int, conn: Any = None) -> Row | None:
        return self.fetch_one(f"{_JOINED} WHERE p.id = %s", (product_id,), conn)

    def get_by_barcode(self, barcode: str, conn: Any = None) -> Row | None:
        """Exact match on the unique index. No LIKE, no function on the column."""
        return self.fetch_one(f"{_JOINED} WHERE p.barcode = %s", (barcode,), conn)

    def get_by_sku(self, sku: str, conn: Any = None) -> Row | None:
        return self.fetch_one(f"{_JOINED} WHERE p.sku = %s", (sku,), conn)

    def search(self, term: str, limit: int = 50, active_only: bool = True,
               category_id: int | None = None, conn: Any = None) -> list[Row]:
        """Match on name, SKU or barcode.

        An exact barcode or SKU sorts first, so scanning something that also
        appears inside another product's name still puts the scanned item on top.
        """
        sql = [_JOINED, "WHERE 1 = 1"]
        params: list[Any] = []
        if active_only:
            sql.append("AND p.is_active = 1")
        if category_id is not None:
            sql.append("AND p.category_id = %s")
            params.append(category_id)
        if term:
            sql.append("AND (p.name LIKE %s OR p.sku LIKE %s OR p.barcode = %s)")
            like = f"%{term}%"
            params.extend([like, like, term])
            sql.append("ORDER BY (p.barcode = %s) DESC, (p.sku = %s) DESC, p.name")
            params.extend([term, term])
        else:
            sql.append("ORDER BY p.name")
        sql.append("LIMIT %s")
        params.append(limit)
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def list_all(self, active_only: bool = False, conn: Any = None) -> list[Row]:
        sql = _JOINED
        if active_only:
            sql += " WHERE p.is_active = 1"
        return self.fetch_all(sql + " ORDER BY p.name", (), conn)

    def sku_taken(self, sku: str, exclude_id: int | None = None,
                  conn: Any = None) -> bool:
        sql = "SELECT 1 FROM products WHERE sku = %s"
        params: list[Any] = [sku]
        if exclude_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_id)
        return self.fetch_one(sql + " LIMIT 1", tuple(params), conn) is not None

    def barcode_taken(self, barcode: str, exclude_id: int | None = None,
                      conn: Any = None) -> bool:
        sql = "SELECT 1 FROM products WHERE barcode = %s"
        params: list[Any] = [barcode]
        if exclude_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_id)
        return self.fetch_one(sql + " LIMIT 1", tuple(params), conn) is not None

    def has_activity(self, product_id: int, conn: Any = None) -> bool:
        """True if any document references this product."""
        checks = (
            "SELECT 1 FROM sale_items WHERE product_id = %s LIMIT 1",
            "SELECT 1 FROM purchase_items WHERE product_id = %s LIMIT 1",
            "SELECT 1 FROM stock_movements WHERE product_id = %s LIMIT 1",
        )
        return any(self.fetch_one(sql, (product_id,), conn) for sql in checks)

    def set_active(self, product_id: int, active: bool, conn: Any = None) -> bool:
        return self.update(product_id, {"is_active": 1 if active else 0}, conn)

    def count(self, conn: Any = None) -> int:
        return int(self.fetch_value("SELECT COUNT(*) AS n FROM products", (), conn) or 0)
