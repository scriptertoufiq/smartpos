"""Queries against `customers` and `suppliers`, and their money ledgers.

The two are kept apart rather than merged behind one abstraction: their
balances run in opposite directions, and every query is one or the other.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from smartpos.db.repository import BaseRepository, Row

_CUSTOMER_COLUMNS = (
    "id, name, phone, email, address, opening_balance, is_walk_in, "
    "is_active, created_at, updated_at"
)
_SUPPLIER_COLUMNS = (
    "id, name, phone, email, address, opening_balance, is_active, "
    "created_at, updated_at"
)

ZERO = Decimal("0.00")


class _PartyRepository(BaseRepository):
    """Shared shape: both parties have a name, a phone and an opening balance."""

    columns: str = ""

    def get(self, party_id: int, conn: Any = None) -> Row | None:
        return self.fetch_one(
            f"SELECT {self.columns} FROM {self.table} WHERE id = %s", (party_id,), conn)

    def search(self, term: str, limit: int = 50, active_only: bool = True,
               conn: Any = None) -> list[Row]:
        sql = [f"SELECT {self.columns} FROM {self.table} WHERE 1 = 1"]
        params: list[Any] = []
        if active_only:
            sql.append("AND is_active = 1")
        if term:
            sql.append("AND (name LIKE %s OR phone LIKE %s OR email LIKE %s)")
            like = f"%{term}%"
            params.extend([like, like, like])
        sql.append("ORDER BY name LIMIT %s")
        params.append(limit)
        return self.fetch_all(" ".join(sql), tuple(params), conn)

    def name_taken(self, name: str, exclude_id: int | None = None,
                   conn: Any = None) -> bool:
        sql = f"SELECT 1 FROM {self.table} WHERE name = %s"
        params: list[Any] = [name]
        if exclude_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_id)
        return self.fetch_one(sql + " LIMIT 1", tuple(params), conn) is not None

    def set_active(self, party_id: int, active: bool, conn: Any = None) -> bool:
        return self.update(party_id, {"is_active": 1 if active else 0}, conn)

    @staticmethod
    def _amount(value) -> Decimal:
        return Decimal(str(value)) if value is not None else ZERO


class CustomerRepository(_PartyRepository):
    table = "customers"
    columns = _CUSTOMER_COLUMNS

    def get_walk_in(self, conn: Any = None) -> Row | None:
        return self.fetch_one(
            f"SELECT {self.columns} FROM customers WHERE is_walk_in = 1 LIMIT 1",
            (), conn)

    # ------------------------------------------------------------- balances

    def balance_parts(self, customer_id: int, conn: Any = None) -> dict[str, Decimal]:
        """The four figures a customer's due is made of.

        Returned separately so a disagreement can be traced to the part that
        caused it, rather than only showing a wrong total.
        """
        opening = self._amount(self.fetch_value(
            "SELECT opening_balance FROM customers WHERE id = %s", (customer_id,), conn))
        # Held and cancelled sales are not debts.
        sales = self._amount(self.fetch_value(
            "SELECT COALESCE(SUM(grand_total), 0) AS n FROM sales "
            "WHERE customer_id = %s AND status = 'completed'", (customer_id,), conn))
        returns = self._amount(self.fetch_value(
            "SELECT COALESCE(SUM(r.total_amount), 0) AS n FROM sale_returns r "
            "JOIN sales s ON s.id = r.sale_id WHERE s.customer_id = %s",
            (customer_id,), conn))
        received = self._amount(self.fetch_value(
            "SELECT COALESCE(SUM(amount), 0) AS n FROM customer_payments "
            "WHERE customer_id = %s AND direction = 'in'", (customer_id,), conn))
        refunded = self._amount(self.fetch_value(
            "SELECT COALESCE(SUM(amount), 0) AS n FROM customer_payments "
            "WHERE customer_id = %s AND direction = 'out'", (customer_id,), conn))
        return {"opening": opening, "sales": sales, "returns": returns,
                "received": received, "refunded": refunded}

    def ledger(self, customer_id: int, limit: int = 200,
               conn: Any = None) -> list[Row]:
        """Every document that moved this customer's balance, newest first."""
        return self.fetch_all(
            """
            SELECT * FROM (
                SELECT s.sale_date   AS happened_at, 'Sale'   AS kind,
                       s.invoice_no  AS reference,   s.grand_total AS debit,
                       0             AS credit
                  FROM sales s
                 WHERE s.customer_id = %s AND s.status = 'completed'
                UNION ALL
                SELECT r.return_date, 'Return', r.reference_no, 0, r.total_amount
                  FROM sale_returns r
                  JOIN sales s ON s.id = r.sale_id
                 WHERE s.customer_id = %s
                UNION ALL
                SELECT p.paid_at, 'Payment', COALESCE(p.reference_type, 'collection'),
                       CASE WHEN p.direction = 'out' THEN p.amount ELSE 0 END,
                       CASE WHEN p.direction = 'in'  THEN p.amount ELSE 0 END
                  FROM customer_payments p
                 WHERE p.customer_id = %s
            ) AS entries
            ORDER BY happened_at DESC, kind
            LIMIT %s
            """,
            (customer_id, customer_id, customer_id, limit), conn)

    def has_activity(self, customer_id: int, conn: Any = None) -> bool:
        checks = (
            "SELECT 1 FROM sales WHERE customer_id = %s LIMIT 1",
            "SELECT 1 FROM customer_payments WHERE customer_id = %s LIMIT 1",
        )
        return any(self.fetch_one(sql, (customer_id,), conn) for sql in checks)


class SupplierRepository(_PartyRepository):
    table = "suppliers"
    columns = _SUPPLIER_COLUMNS

    def balance_parts(self, supplier_id: int, conn: Any = None) -> dict[str, Decimal]:
        opening = self._amount(self.fetch_value(
            "SELECT opening_balance FROM suppliers WHERE id = %s", (supplier_id,), conn))
        purchases = self._amount(self.fetch_value(
            "SELECT COALESCE(SUM(grand_total), 0) AS n FROM purchases "
            "WHERE supplier_id = %s", (supplier_id,), conn))
        paid = self._amount(self.fetch_value(
            "SELECT COALESCE(SUM(amount), 0) AS n FROM supplier_payments "
            "WHERE supplier_id = %s AND direction = 'out'", (supplier_id,), conn))
        refunded = self._amount(self.fetch_value(
            "SELECT COALESCE(SUM(amount), 0) AS n FROM supplier_payments "
            "WHERE supplier_id = %s AND direction = 'in'", (supplier_id,), conn))
        return {"opening": opening, "purchases": purchases,
                "paid": paid, "refunded": refunded}

    def ledger(self, supplier_id: int, limit: int = 200,
               conn: Any = None) -> list[Row]:
        return self.fetch_all(
            """
            SELECT * FROM (
                SELECT p.purchase_date AS happened_at, 'Purchase' AS kind,
                       p.reference_no  AS reference,  p.grand_total AS credit,
                       0               AS debit
                  FROM purchases p
                 WHERE p.supplier_id = %s
                UNION ALL
                SELECT sp.paid_at, 'Payment', COALESCE(sp.reference_type, 'payment'),
                       CASE WHEN sp.direction = 'in'  THEN sp.amount ELSE 0 END,
                       CASE WHEN sp.direction = 'out' THEN sp.amount ELSE 0 END
                  FROM supplier_payments sp
                 WHERE sp.supplier_id = %s
            ) AS entries
            ORDER BY happened_at DESC, kind
            LIMIT %s
            """,
            (supplier_id, supplier_id, limit), conn)

    def has_activity(self, supplier_id: int, conn: Any = None) -> bool:
        checks = (
            "SELECT 1 FROM purchases WHERE supplier_id = %s LIMIT 1",
            "SELECT 1 FROM supplier_payments WHERE supplier_id = %s LIMIT 1",
        )
        return any(self.fetch_one(sql, (supplier_id,), conn) for sql in checks)


class CustomerPaymentRepository(BaseRepository):
    table = "customer_payments"


class SupplierPaymentRepository(BaseRepository):
    table = "supplier_payments"
