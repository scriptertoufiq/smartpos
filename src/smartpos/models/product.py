"""`products`."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from smartpos.models.base import Entity


@dataclass
class Product(Entity):
    sku: str = ""
    name: str = ""
    barcode: str | None = None
    category_id: int | None = None
    unit_id: int | None = None
    purchase_price: Decimal = Decimal("0.00")
    sale_price: Decimal = Decimal("0.00")
    tax_rate: Decimal = Decimal("0.00")
    reorder_level: Decimal = Decimal("0.000")
    image_path: str | None = None
    is_active: bool = True
