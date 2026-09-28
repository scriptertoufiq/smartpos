"""Suppliers and amounts payable to them."""

from __future__ import annotations

from decimal import Decimal

from smartpos.models.supplier import Supplier


class SupplierService:
    def search(self, term: str, limit: int = 50) -> list[Supplier]:
        raise NotImplementedError

    def get(self, supplier_id: int) -> Supplier | None:
        raise NotImplementedError

    def create(self, supplier: Supplier) -> int:
        raise NotImplementedError

    def update(self, supplier: Supplier) -> None:
        raise NotImplementedError

    def payable_balance(self, supplier_id: int) -> Decimal:
        raise NotImplementedError

    def pay(self, supplier_id: int, amount: Decimal, method: str) -> int:
        raise NotImplementedError
