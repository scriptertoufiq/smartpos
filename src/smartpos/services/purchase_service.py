"""Recording purchases from suppliers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from smartpos.models.purchase import Purchase


class PurchaseService:
    def create(self, purchase: Purchase) -> int:
        """Save a purchase and raise stock in the destination warehouse.

        The purchase header, its items, the stock updates and the stock
        movements are written in one transaction.
        """
        raise NotImplementedError

    def get(self, purchase_id: int) -> Purchase | None:
        raise NotImplementedError

    def list(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        supplier_id: int | None = None,
        warehouse_id: int | None = None,
    ) -> list[Purchase]:
        raise NotImplementedError

    def record_payment(self, purchase_id: int, amount: Decimal, method: str) -> None:
        """Pay against a purchase and recalculate its payment status."""
        raise NotImplementedError

    def next_reference_no(self) -> str:
        raise NotImplementedError
