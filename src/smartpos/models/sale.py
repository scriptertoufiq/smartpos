"""`sales` and `sale_items`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from smartpos.core.constants import PaymentMethod, PaymentStatus, SaleStatus
from smartpos.models.base import Entity


@dataclass
class SaleItem(Entity):
    sale_id: int | None = None
    product_id: int | None = None
    quantity: Decimal = Decimal("0.000")
    unit_price: Decimal = Decimal("0.00")
    # Copied from the product at the time of sale so later price changes do not
    # rewrite historic profit figures.
    unit_cost: Decimal = Decimal("0.00")
    discount: Decimal = Decimal("0.00")
    tax_amount: Decimal = Decimal("0.00")
    line_total: Decimal = Decimal("0.00")


@dataclass
class Sale(Entity):
    invoice_no: str = ""
    customer_id: int | None = None
    warehouse_id: int | None = None
    user_id: int | None = None
    sale_date: datetime | None = None
    subtotal: Decimal = Decimal("0.00")
    discount: Decimal = Decimal("0.00")
    tax_amount: Decimal = Decimal("0.00")
    grand_total: Decimal = Decimal("0.00")
    paid_amount: Decimal = Decimal("0.00")
    change_amount: Decimal = Decimal("0.00")
    payment_method: PaymentMethod = PaymentMethod.CASH
    payment_status: PaymentStatus = PaymentStatus.PAID
    status: SaleStatus = SaleStatus.COMPLETED
    note: str | None = None
    items: list[SaleItem] = field(default_factory=list)
