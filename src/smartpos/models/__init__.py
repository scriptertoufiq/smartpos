"""Dataclasses mirroring the database tables."""

from smartpos.models.base import Entity
from smartpos.models.category import Category, Unit
from smartpos.models.customer import Customer
from smartpos.models.payment import Payment
from smartpos.models.product import Product
from smartpos.models.purchase import Purchase, PurchaseItem
from smartpos.models.sale import Sale, SaleItem
from smartpos.models.sale_return import SaleReturn, SaleReturnItem
from smartpos.models.stock import StockLevel, StockMovement, StockTransfer, StockTransferItem
from smartpos.models.supplier import Supplier
from smartpos.models.user import ActivityLogEntry, User
from smartpos.models.warehouse import Warehouse

__all__ = [
    "ActivityLogEntry", "Category", "Customer", "Entity", "Payment", "Product",
    "Purchase", "PurchaseItem", "Sale", "SaleItem", "SaleReturn", "SaleReturnItem",
    "StockLevel", "StockMovement", "StockTransfer", "StockTransferItem",
    "Supplier", "Unit", "User", "Warehouse",
]
