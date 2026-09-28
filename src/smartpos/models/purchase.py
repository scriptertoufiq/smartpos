"""`purchases` and `purchase_items`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from smartpos.core.constants import PaymentStatus
from smartpos.models.base import Entity


@dataclass
class PurchaseItem(Entity):
    purchase_id: int | None = None
    product_id: int | None = None
    quantity: Decimal = Decimal("0.000")
    unit_cost: Decimal = Decimal("0.00")
    discount: Decimal = Decimal("0.00")
    tax_amount: Decimal = Decimal("0.00")
    line_total: Decimal = Decimal("0.00")


@dataclass
class Purchase(Entity):
    reference_no: str = ""
    supplier_id: int | None = None
    warehouse_id: int | None = None
    user_id: int | None = None
    purchase_date: date | None = None
    subtotal: Decimal = Decimal("0.00")
    discount: Decimal = Decimal("0.00")
    tax_amount: Decimal = Decimal("0.00")
    grand_total: Decimal = Decimal("0.00")
    paid_amount: Decimal = Decimal("0.00")
    payment_status: PaymentStatus = PaymentStatus.DUE
    note: str | None = None
    items: list[PurchaseItem] = field(default_factory=list)
