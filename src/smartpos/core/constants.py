"""Values that are fixed for the whole application."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """User roles, mirroring the `users.role` column."""

    ADMIN = "admin"
    MANAGER = "manager"
    CASHIER = "cashier"


class PaymentStatus(StrEnum):
    PAID = "paid"
    PARTIAL = "partial"
    DUE = "due"


class PaymentMethod(StrEnum):
    CASH = "cash"
    CARD = "card"
    MOBILE = "mobile"
    MIXED = "mixed"


class SaleStatus(StrEnum):
    COMPLETED = "completed"
    HELD = "held"
    CANCELLED = "cancelled"


class MovementType(StrEnum):
    """Reasons a stock quantity changes, mirroring `stock_movements.movement_type`."""

    PURCHASE = "purchase"
    SALE = "sale"
    SALE_RETURN = "sale_return"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    ADJUSTMENT = "adjustment"
    OPENING = "opening"


# Permissions each role is allowed to exercise. Checked by `auth_service`.
ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.ADMIN: frozenset({"*"}),
    Role.MANAGER: frozenset(
        {
            "sale.create", "sale.view", "sale_return.create", "sale_return.view",
            "purchase.manage", "product.manage", "category.manage",
            "customer.manage", "supplier.manage", "warehouse.view",
            "stock.manage", "report.view",
        }
    ),
    Role.CASHIER: frozenset({"sale.create", "sale.view", "customer.view", "product.view"}),
}

MONEY_PLACES = 2
QUANTITY_PLACES = 3
