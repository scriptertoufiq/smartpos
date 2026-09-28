"""Stock levels, transfers, adjustments and the stock ledger."""

from __future__ import annotations

from decimal import Decimal

from smartpos.core.constants import MovementType
from smartpos.models.stock import StockLevel, StockMovement, StockTransfer


class StockService:
    def quantity_on_hand(self, product_id: int, warehouse_id: int) -> Decimal:
        raise NotImplementedError

    def levels(self, warehouse_id: int | None = None) -> list[StockLevel]:
        raise NotImplementedError

    def low_stock(self, warehouse_id: int | None = None) -> list[StockLevel]:
        """Products at or below their reorder level."""
        raise NotImplementedError

    def apply_movement(
        self,
        product_id: int,
        warehouse_id: int,
        quantity: Decimal,
        movement_type: MovementType,
        reference_type: str | None = None,
        reference_id: int | None = None,
        note: str | None = None,
    ) -> None:
        """Change a stock level and write the matching ledger row.

        Every other service goes through this -- it is the single place where
        `stock` and `stock_movements` are kept in step. Must be called inside an
        open transaction.
        """
        raise NotImplementedError

    def transfer(self, transfer: StockTransfer) -> int:
        """Move stock between warehouses: one out movement, one in movement."""
        raise NotImplementedError

    def adjust(
        self, product_id: int, warehouse_id: int, new_quantity: Decimal, reason: str
    ) -> None:
        """Correct a level after a physical count, damage or loss."""
        raise NotImplementedError

    def ledger(self, product_id: int, warehouse_id: int | None = None) -> list[StockMovement]:
        raise NotImplementedError
