"""Queries against `purchases` and `purchase_items`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from smartpos.db.repository import BaseRepository, Row

_JOINED = (
    "SELECT p.*, s.name AS supplier_name, w.name AS warehouse_name, u.username "
    "  FROM purchases p "
    "  JOIN suppliers s ON s.id = p.supplier_id "
    "  JOIN warehouses w ON w.id = p.warehouse_id "
    "  LEFT JOIN users u ON u.id = p.user_id"
)


class PurchaseRepository(BaseRepository):
    table = "purchases"

    def get(self, purchase_id: int, conn: Any = None) -> Row | None:
        return self.fetch_one(f"{_JOINED} WHERE p.id = %s", (purchase_id,), conn)

    def get_by_reference(self, reference_no: str, conn: Any = None) -> Row | None:
        return self.fetch_one(f"{_JOINED} WHERE p.reference_no = %s",
                              (reference_no,), conn)

    def list(self, date_from: date | None = None, date_to: date | None = None,
             supplier_id: int | None = None, warehouse_id: int | None = None,
             payment_status: str | None = None, limit: int = 200,
             conn: Any = None) -> list[Row]:
        sql = [_JOINED, "WHERE 1 = 1"]
        params: list[Any] = []
        if date_from is not None:
            sql.append("AND p.purchase_date >= %s")
            params.append(date_from)
        if date_to is not None:
            sql.append("AND p.purchase_date < %s + INTERVAL 1 DAY")
            params.append(date_to)
        if supplier_id is not None:
            sql.append("AND p.supplier_id = %s")
            params.append(supplier_id)
        if warehouse_id is not None:
            sql.append("AND p.warehouse_id = %s")
            params.append(warehouse_id)
        if payment_status is not None:
            sql.append("AND p.payment_status = %s")
            params.append(payment_status)
        sql.append("ORDER BY p.purchase_date DESC, p.id DESC LIMIT %s")
        params.append(limit)
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def paid_total(self, purchase_id: int, conn: Any = None) -> Decimal:
        """Sum of supplier payments against this purchase.

        `purchases.paid_amount` is a cache of exactly this; recomputing from the
        payment rows is what keeps the two honest.
        """
        value = self.fetch_value(
            "SELECT COALESCE(SUM(CASE WHEN direction = 'out' THEN amount "
            "ELSE -amount END), 0) AS n FROM supplier_payments "
            "WHERE reference_type = 'purchase' AND reference_id = %s",
            (purchase_id,), conn)
        return Decimal(str(value or 0))


class PurchaseItemRepository(BaseRepository):
    table = "purchase_items"

    def for_purchase(self, purchase_id: int, conn: Any = None) -> list[Row]:
        return self.fetch_all(
            "SELECT i.*, p.sku, p.name AS product_name, u.short_name AS unit_name "
            "  FROM purchase_items i "
            "  JOIN products p ON p.id = i.product_id "
            "  LEFT JOIN units u ON u.id = p.unit_id "
            " WHERE i.purchase_id = %s ORDER BY i.id", (purchase_id,), conn)
