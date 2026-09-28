"""`suppliers`."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from smartpos.models.base import Entity


@dataclass
class Supplier(Entity):
    name: str = ""
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    opening_balance: Decimal = Decimal("0.00")
    is_active: bool = True
