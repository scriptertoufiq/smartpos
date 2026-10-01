"""Queries against `stock`, `stock_movements` and the transfer tables.

`lock_level` is the heart of it: a sale takes that row lock inside its own
transaction, which is what stops two counters selling the same last unit.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from smartpos.core.exceptions import DatabaseError
from smartpos.db.repository import BaseRepository, Row

ZERO = Decimal("0.000")


class StockRepository(BaseRepository):
    table = "stock"

    # ------------------------------------------------------------- the lock

    def lock_level(self, product_id: int, warehouse_id: int, conn: Any) -> Decimal:
        """Lock this product's row in this warehouse and return what is on hand.

        Creates the row if it does not exist, so the lock always has something
        to hold. Requires an open transaction -- a lock taken outside one is
        released immediately and protects nothing.
        """
        if conn is None:
            raise DatabaseError(
                "Stock must be locked inside a transaction; a lock outside one "
                "is released at once and protects nothing.")

        row = self.fetch_one(
            "SELECT quantity FROM stock WHERE product_id = %s AND warehouse_id = %s "
            "FOR UPDATE", (product_id, warehouse_id), conn)
        if row is not None:
            return Decimal(str(row["quantity"]))

        # No row yet. Create it at zero, then lock it.
        self.execute(
            "INSERT INTO stock (product_id, warehouse_id, quantity) VALUES (%s, %s, 0) "
            "ON DUPLICATE KEY UPDATE quantity = quantity",
            (product_id, warehouse_id), conn)
        row = self.fetch_one(
            "SELECT quantity FROM stock WHERE product_id = %s AND warehouse_id = %s "
            "FOR UPDATE", (product_id, warehouse_id), conn)
        return Decimal(str(row["quantity"])) if row else ZERO

    def set_quantity(self, product_id: int, warehouse_id: int,
                     quantity: Decimal, conn: Any) -> None:
        self.execute(
            "UPDATE stock SET quantity = %s WHERE product_id = %s AND warehouse_id = %s",
            (quantity, product_id, warehouse_id), conn)

    # ------------------------------------------------------------------ reads

    def quantity_on_hand(self, product_id: int, warehouse_id: int,
                         conn: Any = None) -> Decimal:
        value = self.fetch_value(
            "SELECT quantity FROM stock WHERE product_id = %s AND warehouse_id = %s",
            (product_id, warehouse_id), conn)
        return Decimal(str(value)) if value is not None else ZERO

    def levels(self, warehouse_id: int | None = None, search: str = "",
               only_nonzero: bool = False, conn: Any = None) -> list[Row]:
        sql = [
            "SELECT s.id, s.product_id, s.warehouse_id, s.quantity, s.updated_at,",
            "       p.sku, p.name AS product_name, p.reorder_level, p.sale_price,",
            "       w.name AS warehouse_name, u.short_name AS unit_name",
            "  FROM stock s",
            "  JOIN products p ON p.id = s.product_id",
            "  JOIN warehouses w ON w.id = s.warehouse_id",
            "  LEFT JOIN units u ON u.id = p.unit_id",
            " WHERE 1 = 1",
        ]
        params: list[Any] = []
        if warehouse_id is not None:
            sql.append("AND s.warehouse_id = %s")
            params.append(warehouse_id)
        if only_nonzero:
            sql.append("AND s.quantity <> 0")
        if search:
            sql.append("AND (p.name LIKE %s OR p.sku LIKE %s OR p.barcode = %s)")
            like = f"%{search}%"
            params.extend([like, like, search])
        sql.append("ORDER BY p.name, w.name")
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def low_stock(self, warehouse_id: int | None = None,
                  conn: Any = None) -> list[Row]:
        """At or below the product's reorder level, ignoring products with no level set."""
        sql = [
            "SELECT s.product_id, s.warehouse_id, s.quantity, p.sku,",
            "       p.name AS product_name, p.reorder_level,",
            "       w.name AS warehouse_name",
            "  FROM stock s",
            "  JOIN products p ON p.id = s.product_id",
            "  JOIN warehouses w ON w.id = s.warehouse_id",
            " WHERE p.reorder_level > 0 AND s.quantity <= p.reorder_level",
            "   AND p.is_active = 1",
        ]
        params: list[Any] = []
        if warehouse_id is not None:
            sql.append("AND s.warehouse_id = %s")
            params.append(warehouse_id)
        sql.append("ORDER BY (s.quantity / NULLIF(p.reorder_level, 0)), p.name")
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def total_for_product(self, product_id: int, conn: Any = None) -> Decimal:
        value = self.fetch_value(
            "SELECT COALESCE(SUM(quantity), 0) AS n FROM stock WHERE product_id = %s",
            (product_id,), conn)
        return Decimal(str(value or 0))


class StockMovementRepository(BaseRepository):
    table = "stock_movements"

    def record(self, *, product_id: int, warehouse_id: int, movement_type: str,
               quantity: Decimal, balance_after: Decimal,
               reference_type: str | None, reference_id: int | None,
               user_id: int | None, note: str | None, conn: Any) -> int:
        if conn is None:
            raise DatabaseError("A stock movement must be written inside a transaction.")
        return self.insert({
            "product_id": product_id, "warehouse_id": warehouse_id,
            "movement_type": movement_type, "quantity": quantity,
            "balance_after": balance_after, "reference_type": reference_type,
            "reference_id": reference_id, "user_id": user_id, "note": note,
        }, conn)

    def ledger(self, product_id: int, warehouse_id: int | None = None,
               limit: int = 200, conn: Any = None) -> list[Row]:
        sql = [
            "SELECT m.id, m.product_id, m.warehouse_id, m.movement_type, m.quantity,",
            "       m.balance_after, m.reference_type, m.reference_id, m.note,",
            "       m.created_at, w.name AS warehouse_name, u.username",
            "  FROM stock_movements m",
            "  JOIN warehouses w ON w.id = m.warehouse_id",
            "  LEFT JOIN users u ON u.id = m.user_id",
            " WHERE m.product_id = %s",
        ]
        params: list[Any] = [product_id]
        if warehouse_id is not None:
            sql.append("AND m.warehouse_id = %s")
            params.append(warehouse_id)
        sql.append("ORDER BY m.created_at DESC, m.id DESC LIMIT %s")
        params.append(limit)
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def for_document(self, reference_type: str, reference_id: int,
                     conn: Any = None) -> list[Row]:
        return self.fetch_all(
            "SELECT * FROM stock_movements WHERE reference_type = %s "
            "AND reference_id = %s ORDER BY id", (reference_type, reference_id), conn)

    def drift(self, conn: Any = None) -> list[Row]:
        """Rows where the ledger and the level disagree. Must always be empty."""
        return self.fetch_all(
            """
            SELECT m.product_id, m.warehouse_id,
                   SUM(m.quantity) AS ledger_total,
                   s.quantity      AS level
              FROM stock_movements m
              JOIN stock s ON s.product_id = m.product_id
                          AND s.warehouse_id = m.warehouse_id
             GROUP BY m.product_id, m.warehouse_id, s.quantity
            HAVING SUM(m.quantity) <> s.quantity
            """, (), conn)


class StockTransferRepository(BaseRepository):
    table = "stock_transfers"

    def list(self, limit: int = 100, conn: Any = None) -> list[Row]:
        return self.fetch_all(
            "SELECT t.*, f.name AS from_name, d.name AS to_name, u.username "
            "  FROM stock_transfers t "
            "  JOIN warehouses f ON f.id = t.from_warehouse_id "
            "  JOIN warehouses d ON d.id = t.to_warehouse_id "
            "  LEFT JOIN users u ON u.id = t.user_id "
            " ORDER BY t.transfer_date DESC, t.id DESC LIMIT %s", (limit,), conn)

    def items_of(self, transfer_id: int, conn: Any = None) -> list[Row]:
        return self.fetch_all(
            "SELECT i.*, p.sku, p.name AS product_name "
            "  FROM stock_transfer_items i "
            "  JOIN products p ON p.id = i.product_id "
            " WHERE i.transfer_id = %s ORDER BY p.name", (transfer_id,), conn)


class StockTransferItemRepository(BaseRepository):
    table = "stock_transfer_items"
