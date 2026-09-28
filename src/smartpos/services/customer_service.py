"""Customers, their purchase history and their dues."""

from __future__ import annotations

from decimal import Decimal

from smartpos.models.customer import Customer


class CustomerService:
    def search(self, term: str, limit: int = 50) -> list[Customer]:
        raise NotImplementedError

    def get(self, customer_id: int) -> Customer | None:
        raise NotImplementedError

    def get_walk_in(self) -> Customer:
        """Return the default walk-in customer used by quick counter sales."""
        raise NotImplementedError

    def create(self, customer: Customer) -> int:
        raise NotImplementedError

    def update(self, customer: Customer) -> None:
        raise NotImplementedError

    def due_balance(self, customer_id: int) -> Decimal:
        """Opening balance + unpaid sales - returns - collections."""
        raise NotImplementedError

    def collect_due(self, customer_id: int, amount: Decimal, method: str) -> int:
        raise NotImplementedError
