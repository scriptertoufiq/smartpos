"""Queries against `sales` and `sale_items`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from smartpos.db.repository import BaseRepository, Row

_JOINED = (
    "SELECT s.*, c.name AS customer_name, c.phone AS customer_phone, "
    "       w.name AS warehouse_name, u.username "
    "  FROM sales s "
    "  JOIN customers c ON c.id = s.customer_id "
    "  JOIN warehouses w ON w.id = s.warehouse_id "
    "  LEFT JOIN users u ON u.id = s.user_id"
)


class SaleRepository(BaseRepository):
    table = "sales"

    def get(self, sale_id: int, conn: Any = None) -> Row | None:
        return self.fetch_one(f"{_JOINED} WHERE s.id = %s", (sale_id,), conn)

    def get_by_invoice(self, invoice_no: str, conn: Any = None) -> Row | None:
        return self.fetch_one(f"{_JOINED} WHERE s.invoice_no = %s", (invoice_no,), conn)

    def list(self, date_from: date | None = None, date_to: date | None = None,
             customer_id: int | None = None, warehouse_id: int | None = None,
             status: str | None = "completed", user_id: int | None = None,
             limit: int = 200, conn: Any = None) -> list[Row]:
        sql = [_JOINED, "WHERE 1 = 1"]
        params: list[Any] = []
        if status is not None:
            sql.append("AND s.status = %s")
            params.append(status)
        if date_from is not None:
            sql.append("AND s.sale_date >= %s")
            params.append(date_from)
        if date_to is not None:
            sql.append("AND s.sale_date < %s + INTERVAL 1 DAY")
            params.append(date_to)
        if customer_id is not None:
            sql.append("AND s.customer_id = %s")
            params.append(customer_id)
        if warehouse_id is not None:
            sql.append("AND s.warehouse_id = %s")
            params.append(warehouse_id)
        if user_id is not None:
            sql.append("AND s.user_id = %s")
            params.append(user_id)
        sql.append("ORDER BY s.sale_date DESC, s.id DESC LIMIT %s")
        params.append(limit)
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def held(self, warehouse_id: int | None = None, conn: Any = None) -> list[Row]:
        sql = [_JOINED, "WHERE s.status = 'held'"]
        params: list[Any] = []
        if warehouse_id is not None:
            sql.append("AND s.warehouse_id = %s")
            params.append(warehouse_id)
        sql.append("ORDER BY s.sale_date DESC")
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def paid_total(self, sale_id: int, conn: Any = None) -> Decimal:
        value = self.fetch_value(
            "SELECT COALESCE(SUM(CASE WHEN direction = 'in' THEN amount "
            "ELSE -amount END), 0) AS n FROM customer_payments "
            "WHERE reference_type = 'sale' AND reference_id = %s", (sale_id,), conn)
        return Decimal(str(value or 0))


class SaleItemRepository(BaseRepository):
    table = "sale_items"

    def for_sale(self, sale_id: int, conn: Any = None) -> list[Row]:
        return self.fetch_all(
            "SELECT i.*, p.sku, p.name AS product_name, p.barcode, "
            "       u.short_name AS unit_name "
            "  FROM sale_items i "
            "  JOIN products p ON p.id = i.product_id "
            "  LEFT JOIN units u ON u.id = p.unit_id "
            " WHERE i.sale_id = %s ORDER BY i.id", (sale_id,), conn)

    def delete_for_sale(self, sale_id: int, conn: Any) -> int:
        return self.execute("DELETE FROM sale_items WHERE sale_id = %s",
                            (sale_id,), conn)
