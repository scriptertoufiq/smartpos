"""The sales counter."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from smartpos.models.sale import Sale


class SaleService:
    def create(self, sale: Sale) -> int:
        """Complete a sale: save it, deduct stock, log the movements.

        All of it runs in one transaction, and the stock check happens inside
        that transaction so two counters cannot sell the same last unit.
        Raises InsufficientStock if the warehouse cannot cover a line.
        """
        raise NotImplementedError

    def hold(self, sale: Sale) -> int:
        """Park an unfinished cart so the counter is not blocked. No stock effect."""
        raise NotImplementedError

    def list_held(self, warehouse_id: int) -> list[Sale]:
        raise NotImplementedError

    def resume(self, sale_id: int) -> Sale:
        raise NotImplementedError

    def get(self, sale_id: int) -> Sale | None:
        raise NotImplementedError

    def get_by_invoice(self, invoice_no: str) -> Sale | None:
        """Used by the return screen, which starts from an invoice number."""
        raise NotImplementedError

    def list(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        customer_id: int | None = None,
        warehouse_id: int | None = None,
    ) -> list[Sale]:
        raise NotImplementedError

    def record_payment(self, sale_id: int, amount: Decimal, method: str) -> None:
        raise NotImplementedError

    def next_invoice_no(self) -> str:
        raise NotImplementedError
