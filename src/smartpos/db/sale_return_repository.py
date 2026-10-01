"""Queries against `sale_returns` and `sale_return_items`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from smartpos.db.repository import BaseRepository, Row

_JOINED = (
    "SELECT r.*, s.invoice_no, s.customer_id, c.name AS customer_name, "
    "       w.name AS warehouse_name, u.username "
    "  FROM sale_returns r "
    "  JOIN sales s ON s.id = r.sale_id "
    "  JOIN customers c ON c.id = s.customer_id "
    "  JOIN warehouses w ON w.id = r.warehouse_id "
    "  LEFT JOIN users u ON u.id = r.user_id"
)


class SaleReturnRepository(BaseRepository):
    table = "sale_returns"

    def get(self, return_id: int, conn: Any = None) -> Row | None:
        return self.fetch_one(f"{_JOINED} WHERE r.id = %s", (return_id,), conn)

    def list(self, date_from: date | None = None, date_to: date | None = None,
             sale_id: int | None = None, limit: int = 200,
             conn: Any = None) -> list[Row]:
        sql = [_JOINED, "WHERE 1 = 1"]
        params: list[Any] = []
        if date_from is not None:
            sql.append("AND r.return_date >= %s")
            params.append(date_from)
        if date_to is not None:
            sql.append("AND r.return_date < %s + INTERVAL 1 DAY")
            params.append(date_to)
        if sale_id is not None:
            sql.append("AND r.sale_id = %s")
            params.append(sale_id)
        sql.append("ORDER BY r.return_date DESC, r.id DESC LIMIT %s")
        params.append(limit)
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def returned_per_line(self, sale_id: int, conn: Any = None) -> dict[int, Decimal]:
        """How much of each sold line has already gone back.

        Locked with FOR UPDATE by the caller's own query when it matters; this
        plain read is for showing maxima on screen.
        """
        rows = self.fetch_all(
            "SELECT i.sale_item_id, COALESCE(SUM(i.quantity), 0) AS returned "
            "  FROM sale_return_items i "
            "  JOIN sale_returns r ON r.id = i.sale_return_id "
            " WHERE r.sale_id = %s GROUP BY i.sale_item_id", (sale_id,), conn)
        return {r["sale_item_id"]: Decimal(str(r["returned"])) for r in rows}

    def lock_returned_per_line(self, sale_id: int, conn: Any) -> dict[int, Decimal]:
        """The same figures, but holding a lock on every return row of this sale.

        Two managers processing returns against one invoice at the same moment
        must not together give back more than was sold.
        """
        self.fetch_all(
            "SELECT i.id FROM sale_return_items i "
            "  JOIN sale_returns r ON r.id = i.sale_return_id "
            " WHERE r.sale_id = %s FOR UPDATE", (sale_id,), conn)
        return self.returned_per_line(sale_id, conn)

    def total_returned(self, sale_id: int, conn: Any = None) -> Decimal:
        value = self.fetch_value(
            "SELECT COALESCE(SUM(total_amount), 0) AS n FROM sale_returns "
            "WHERE sale_id = %s", (sale_id,), conn)
        return Decimal(str(value or 0))


class SaleReturnItemRepository(BaseRepository):
    table = "sale_return_items"

    def for_return(self, return_id: int, conn: Any = None) -> list[Row]:
        return self.fetch_all(
            "SELECT i.*, p.sku, p.name AS product_name "
            "  FROM sale_return_items i "
            "  JOIN products p ON p.id = i.product_id "
            " WHERE i.sale_return_id = %s ORDER BY i.id", (return_id,), conn)
