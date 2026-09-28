"""`categories` and `units`."""

from __future__ import annotations

from dataclasses import dataclass

from smartpos.models.base import Entity


@dataclass
class Category(Entity):
    name: str = ""
    parent_id: int | None = None
    is_active: bool = True


@dataclass
class Unit(Entity):
    name: str = ""
    short_name: str = ""
