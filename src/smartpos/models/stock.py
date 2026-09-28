"""`stock`, `stock_movements`, `stock_transfers` and `stock_transfer_items`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from smartpos.core.constants import MovementType
from smartpos.models.base import Entity


@dataclass
class StockLevel(Entity):
    """Quantity on hand for one product in one warehouse."""

    product_id: int | None = None
    warehouse_id: int | None = None
    quantity: Decimal = Decimal("0.000")


@dataclass
class StockMovement(Entity):
    """One line of the stock ledger. Quantity is signed: positive in, negative out."""

    product_id: int | None = None
    warehouse_id: int | None = None
    movement_type: MovementType = MovementType.ADJUSTMENT
    quantity: Decimal = Decimal("0.000")
    balance_after: Decimal = Decimal("0.000")
    reference_type: str | None = None
    reference_id: int | None = None
    user_id: int | None = None
    note: str | None = None


@dataclass
class StockTransferItem(Entity):
    transfer_id: int | None = None
    product_id: int | None = None
    quantity: Decimal = Decimal("0.000")


@dataclass
class StockTransfer(Entity):
    reference_no: str = ""
    from_warehouse_id: int | None = None
    to_warehouse_id: int | None = None
    user_id: int | None = None
    transfer_date: datetime | None = None
    note: str | None = None
    items: list[StockTransferItem] = field(default_factory=list)
