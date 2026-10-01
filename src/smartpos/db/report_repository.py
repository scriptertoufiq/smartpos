"""Reporting queries.

Three rules run through every query here:

  * only `status = 'completed'` sales count -- a held bill is goods still on the
    shelf, and a cancelled one never happened;
  * profit uses `sale_items.unit_cost`, the cost stamped on the line the day it
    sold, never the product's current purchase price;
  * returns are netted off both revenue and cost, so a returned item leaves no
    trace of profit behind it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from smartpos.db.repository import BaseRepository, Row

# Every report filters on this, which is what idx_sales_status_date is for.
_COMPLETED = "s.status = 'completed'"


def _range(sql: list[str], params: list[Any], date_from: date | None,
           date_to: date | None, column: str = "s.sale_date") -> None:
    if date_from is not None:
        sql.append(f"AND {column} >= %s")
        params.append(date_from)
    if date_to is not None:
        sql.append(f"AND {column} < %s + INTERVAL 1 DAY")
        params.append(date_to)


class ReportRepository(BaseRepository):
    table = "sales"

    # --------------------------------------------------------------- summary

    def sales_totals(self, date_from: date | None = None, date_to: date | None = None,
                     warehouse_id: int | None = None, conn: Any = None) -> Row:
        sql = [
            "SELECT COUNT(*) AS invoices,",
            "       COALESCE(SUM(s.subtotal), 0)    AS line_revenue,",
            "       COALESCE(SUM(s.discount), 0)    AS discount,",
            "       COALESCE(SUM(s.tax_amount), 0)  AS tax,",
            "       COALESCE(SUM(s.grand_total), 0) AS billed,",
            "       COALESCE(SUM(s.paid_amount), 0) AS collected",
            f"  FROM sales s WHERE {_COMPLETED}",
        ]
        params: list[Any] = []
        _range(sql, params, date_from, date_to)
        if warehouse_id is not None:
            sql.append("AND s.warehouse_id = %s")
            params.append(warehouse_id)
        return self.fetch_one(" ".join(sql), tuple(params), conn) or {}

    def cost_of_goods_sold(self, date_from: date | None = None,
                           date_to: date | None = None,
                           warehouse_id: int | None = None,
                           conn: Any = None) -> Row:
        sql = [
            "SELECT COALESCE(SUM(i.quantity * i.unit_cost), 0) AS cogs,",
            "       COALESCE(SUM(i.line_total), 0)             AS line_revenue",
            "  FROM sale_items i JOIN sales s ON s.id = i.sale_id",
            f" WHERE {_COMPLETED}",
        ]
        params: list[Any] = []
        _range(sql, params, date_from, date_to)
        if warehouse_id is not None:
            sql.append("AND s.warehouse_id = %s")
            params.append(warehouse_id)
        return self.fetch_one(" ".join(sql), tuple(params), conn) or {}

    def returns_totals(self, date_from: date | None = None,
                       date_to: date | None = None,
                       warehouse_id: int | None = None, conn: Any = None) -> Row:
        """Returned value, and the cost that came back with it."""
        sql = [
            "SELECT COUNT(DISTINCT r.id) AS returns,",
            "       COALESCE(SUM(ri.line_total), 0)              AS amount,",
            "       COALESCE(SUM(ri.quantity * si.unit_cost), 0) AS cogs",
            "  FROM sale_return_items ri",
            "  JOIN sale_returns r ON r.id = ri.sale_return_id",
            "  JOIN sale_items si ON si.id = ri.sale_item_id",
            "  JOIN sales s ON s.id = r.sale_id",
            " WHERE 1 = 1",
        ]
        params: list[Any] = []
        _range(sql, params, date_from, date_to, "r.return_date")
        if warehouse_id is not None:
            sql.append("AND r.warehouse_id = %s")
            params.append(warehouse_id)
        return self.fetch_one(" ".join(sql), tuple(params), conn) or {}

    # ----------------------------------------------------------------- lists

    def sales_by_day(self, date_from: date, date_to: date,
                     warehouse_id: int | None = None, conn: Any = None) -> list[Row]:
        sql = [
            "SELECT DATE(s.sale_date) AS day, COUNT(*) AS invoices,",
            "       COALESCE(SUM(s.grand_total), 0) AS billed,",
            "       COALESCE(SUM(s.paid_amount), 0) AS collected",
            f"  FROM sales s WHERE {_COMPLETED}",
        ]
        params: list[Any] = []
        _range(sql, params, date_from, date_to)
        if warehouse_id is not None:
            sql.append("AND s.warehouse_id = %s")
            params.append(warehouse_id)
        sql.append("GROUP BY DATE(s.sale_date) ORDER BY day")
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def sales_detail(self, date_from: date | None, date_to: date | None,
                     warehouse_id: int | None = None, conn: Any = None) -> list[Row]:
        sql = [
            "SELECT s.invoice_no, s.sale_date, c.name AS customer, w.name AS outlet,",
            "       s.subtotal, s.discount, s.tax_amount, s.grand_total,",
            "       s.paid_amount, s.payment_status, u.username AS served_by",
            "  FROM sales s",
            "  JOIN customers c ON c.id = s.customer_id",
            "  JOIN warehouses w ON w.id = s.warehouse_id",
            "  LEFT JOIN users u ON u.id = s.user_id",
            f" WHERE {_COMPLETED}",
        ]
        params: list[Any] = []
        _range(sql, params, date_from, date_to)
        if warehouse_id is not None:
            sql.append("AND s.warehouse_id = %s")
            params.append(warehouse_id)
        sql.append("ORDER BY s.sale_date DESC, s.id DESC")
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def purchases_detail(self, date_from: date | None, date_to: date | None,
                         conn: Any = None) -> list[Row]:
        sql = [
            "SELECT p.reference_no, p.purchase_date, sup.name AS supplier,",
            "       w.name AS outlet, p.subtotal, p.discount, p.tax_amount,",
            "       p.grand_total, p.paid_amount, p.payment_status",
            "  FROM purchases p",
            "  JOIN suppliers sup ON sup.id = p.supplier_id",
            "  JOIN warehouses w ON w.id = p.warehouse_id",
            " WHERE 1 = 1",
        ]
        params: list[Any] = []
        _range(sql, params, date_from, date_to, "p.purchase_date")
        sql.append("ORDER BY p.purchase_date DESC, p.id DESC")
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def returns_detail(self, date_from: date | None, date_to: date | None,
                       conn: Any = None) -> list[Row]:
        sql = [
            "SELECT r.reference_no, r.return_date, s.invoice_no, c.name AS customer,",
            "       w.name AS outlet, r.total_amount, r.refund_method, r.reason",
            "  FROM sale_returns r",
            "  JOIN sales s ON s.id = r.sale_id",
            "  JOIN customers c ON c.id = s.customer_id",
            "  JOIN warehouses w ON w.id = r.warehouse_id",
            " WHERE 1 = 1",
        ]
        params: list[Any] = []
        _range(sql, params, date_from, date_to, "r.return_date")
        sql.append("ORDER BY r.return_date DESC, r.id DESC")
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def stock_detail(self, warehouse_id: int | None = None,
                     conn: Any = None) -> list[Row]:
        sql = [
            "SELECT p.sku, p.name AS product, w.name AS outlet, st.quantity,",
            "       p.reorder_level, p.purchase_price, p.sale_price,",
            "       (st.quantity * p.purchase_price) AS stock_value",
            "  FROM stock st",
            "  JOIN products p ON p.id = st.product_id",
            "  JOIN warehouses w ON w.id = st.warehouse_id",
            " WHERE p.is_active = 1",
        ]
        params: list[Any] = []
        if warehouse_id is not None:
            sql.append("AND st.warehouse_id = %s")
            params.append(warehouse_id)
        sql.append("ORDER BY p.name, w.name")
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def top_products(self, date_from: date | None, date_to: date | None,
                     limit: int = 10, conn: Any = None) -> list[Row]:
        """Best sellers, net of what came back."""
        sql = [
            "SELECT p.sku, p.name AS product,",
            "       SUM(i.quantity) AS sold,",
            "       COALESCE(SUM(ret.returned), 0) AS returned,",
            "       SUM(i.line_total) AS revenue,",
            "       SUM(i.quantity * i.unit_cost) AS cost",
            "  FROM sale_items i",
            "  JOIN sales s ON s.id = i.sale_id",
            "  JOIN products p ON p.id = i.product_id",
            "  LEFT JOIN (SELECT sale_item_id, SUM(quantity) AS returned",
            "               FROM sale_return_items GROUP BY sale_item_id) ret",
            "         ON ret.sale_item_id = i.id",
            f" WHERE {_COMPLETED}",
        ]
        params: list[Any] = []
        _range(sql, params, date_from, date_to)
        sql.append("GROUP BY p.id, p.sku, p.name ORDER BY sold DESC LIMIT %s")
        params.append(limit)
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def slow_moving(self, since: date, limit: int = 50,
                    conn: Any = None) -> list[Row]:
        """Products holding stock that have not sold since `since`."""
        return self.fetch_all(
            "SELECT p.sku, p.name AS product,"
            "       COALESCE(SUM(st.quantity), 0) AS on_hand,"
            "       (SELECT MAX(s2.sale_date) FROM sale_items i2"
            "          JOIN sales s2 ON s2.id = i2.sale_id"
            "         WHERE i2.product_id = p.id AND s2.status = 'completed'"
            "       ) AS last_sold"
            "  FROM products p"
            "  LEFT JOIN stock st ON st.product_id = p.id"
            " WHERE p.is_active = 1"
            " GROUP BY p.id, p.sku, p.name"
            " HAVING on_hand > 0 AND (last_sold IS NULL OR last_sold < %s)"
            " ORDER BY last_sold IS NOT NULL, last_sold, on_hand DESC"
            " LIMIT %s", (since, limit), conn)

    def customer_dues(self, conn: Any = None) -> list[Row]:
        return self.fetch_all(
            """
            SELECT c.name AS customer, c.phone,
                   c.opening_balance
                 + COALESCE(sa.billed, 0)
                 - COALESCE(re.returned, 0)
                 - COALESCE(pa.received, 0)
                 + COALESCE(pa.refunded, 0) AS due
              FROM customers c
              LEFT JOIN (SELECT customer_id, SUM(grand_total) AS billed
                           FROM sales WHERE status = 'completed'
                          GROUP BY customer_id) sa ON sa.customer_id = c.id
              LEFT JOIN (SELECT s.customer_id, SUM(r.total_amount) AS returned
                           FROM sale_returns r JOIN sales s ON s.id = r.sale_id
                          GROUP BY s.customer_id) re ON re.customer_id = c.id
              LEFT JOIN (SELECT customer_id,
                                SUM(CASE WHEN direction = 'in'  THEN amount ELSE 0 END) AS received,
                                SUM(CASE WHEN direction = 'out' THEN amount ELSE 0 END) AS refunded
                           FROM customer_payments GROUP BY customer_id) pa
                     ON pa.customer_id = c.id
             HAVING due <> 0
             ORDER BY due DESC
            """, (), conn)

    def supplier_payables(self, conn: Any = None) -> list[Row]:
        return self.fetch_all(
            """
            SELECT s.name AS supplier, s.phone,
                   s.opening_balance
                 + COALESCE(pu.purchased, 0)
                 - COALESCE(pa.paid, 0)
                 + COALESCE(pa.refunded, 0) AS payable
              FROM suppliers s
              LEFT JOIN (SELECT supplier_id, SUM(grand_total) AS purchased
                           FROM purchases GROUP BY supplier_id) pu
                     ON pu.supplier_id = s.id
              LEFT JOIN (SELECT supplier_id,
                                SUM(CASE WHEN direction = 'out' THEN amount ELSE 0 END) AS paid,
                                SUM(CASE WHEN direction = 'in'  THEN amount ELSE 0 END) AS refunded
                           FROM supplier_payments GROUP BY supplier_id) pa
                     ON pa.supplier_id = s.id
             HAVING payable <> 0
             ORDER BY payable DESC
            """, (), conn)

    # ------------------------------------------------------------- dashboard

    def collected_on(self, on: date, conn: Any = None) -> Row:
        """Cash actually taken that day, whatever it was against."""
        return self.fetch_one(
            "SELECT COALESCE(SUM(CASE WHEN direction = 'in' THEN amount "
            "ELSE -amount END), 0) AS collected "
            "  FROM customer_payments WHERE DATE(paid_at) = %s", (on,), conn) or {}

    def low_stock_count(self, warehouse_id: int | None = None,
                        conn: Any = None) -> int:
        sql = ["SELECT COUNT(*) AS n FROM stock st JOIN products p ON p.id = st.product_id",
               "WHERE p.is_active = 1 AND p.reorder_level > 0",
               "  AND st.quantity <= p.reorder_level"]
        params: list[Any] = []
        if warehouse_id is not None:
            sql.append("AND st.warehouse_id = %s")
            params.append(warehouse_id)
        return int(self.fetch_value(" ".join(sql), tuple(params), conn) or 0)

    def stock_value(self, warehouse_id: int | None = None, conn: Any = None):
        sql = ["SELECT COALESCE(SUM(st.quantity * p.purchase_price), 0) AS n",
               "  FROM stock st JOIN products p ON p.id = st.product_id",
               " WHERE p.is_active = 1"]
        params: list[Any] = []
        if warehouse_id is not None:
            sql.append("AND st.warehouse_id = %s")
            params.append(warehouse_id)
        return self.fetch_value(" ".join(sql), tuple(params), conn) or 0
