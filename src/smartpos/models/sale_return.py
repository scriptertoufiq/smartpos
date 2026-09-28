"""`sale_returns` and `sale_return_items`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from smartpos.models.base import Entity


@dataclass
class SaleReturnItem(Entity):
    sale_return_id: int | None = None
    sale_item_id: int | None = None
    product_id: int | None = None
    quantity: Decimal = Decimal("0.000")
    unit_price: Decimal = Decimal("0.00")
    line_total: Decimal = Decimal("0.00")


@dataclass
class SaleReturn(Entity):
    reference_no: str = ""
    sale_id: int | None = None
    warehouse_id: int | None = None
    user_id: int | None = None
    return_date: datetime | None = None
    total_amount: Decimal = Decimal("0.00")
    refund_method: str = "cash"
    reason: str | None = None
    items: list[SaleReturnItem] = field(default_factory=list)
