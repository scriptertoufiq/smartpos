"""Warehouses and outlets."""

from __future__ import annotations

from smartpos.models.warehouse import Warehouse


class WarehouseService:
    def list_all(self, active_only: bool = True) -> list[Warehouse]:
        raise NotImplementedError

    def get(self, warehouse_id: int) -> Warehouse | None:
        raise NotImplementedError

    def create(self, warehouse: Warehouse) -> int:
        raise NotImplementedError

    def update(self, warehouse: Warehouse) -> None:
        raise NotImplementedError

    def deactivate(self, warehouse_id: int) -> None:
        """Warehouses are never deleted -- historic documents still point at them."""
        raise NotImplementedError
