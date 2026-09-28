"""`payments` -- money received from customers and paid to suppliers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from smartpos.models.base import Entity


@dataclass
class Payment(Entity):
    party_type: str = "customer"   # customer | supplier
    party_id: int | None = None
    direction: str = "in"          # in | out
    amount: Decimal = Decimal("0.00")
    method: str = "cash"
    reference_type: str | None = None
    reference_id: int | None = None
    user_id: int | None = None
    paid_at: datetime | None = None
    note: str | None = None
