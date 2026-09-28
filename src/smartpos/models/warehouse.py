"""`warehouses`."""

from __future__ import annotations

from dataclasses import dataclass

from smartpos.models.base import Entity


@dataclass
class Warehouse(Entity):
    code: str = ""
    name: str = ""
    address: str | None = None
    phone: str | None = None
    is_active: bool = True
