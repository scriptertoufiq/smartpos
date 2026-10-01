"""Stock levels, transfers, adjustments and the stock ledger.

Stock is stored twice: `stock` holds the quantity on hand, `stock_movements`
holds the ledger that explains it. They must never disagree, which is why
`apply_movement()` is the only thing in the codebase that writes either of them.

The invariant, checkable at any moment with `find_drift()`:

    SUM(stock_movements.quantity) == stock.quantity

for every (product, warehouse) pair. If that ever returns a row, something has
written stock outside this service.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from smartpos.core.constants import MovementType
from smartpos.core.exceptions import InsufficientStock, ValidationError
from smartpos.core.logging import get_logger
from smartpos.db.connection import transaction
from smartpos.db.sequence_repository import SequenceRepository
from smartpos.db.stock_repository import (
    StockMovementRepository,
    StockRepository,
    StockTransferItemRepository,
    StockTransferRepository,
)
from smartpos.db.user_repository import ActivityLogRepository
from smartpos.services.auth_service import AuthService

log = get_logger(__name__)

MANAGE = "stock.manage"
ZERO = Decimal("0.000")

# Movements that may take a level below zero. None, by design: stock going
# negative means the ledger no longer describes anything physical.
ALLOW_NEGATIVE: frozenset[MovementType] = frozenset()


class StockService:
    """Every change to stock goes through `apply_movement`."""

    def __init__(self, auth: AuthService | None = None) -> None:
        self._stock = StockRepository()
        self._movements = StockMovementRepository()
        self._transfers = StockTransferRepository()
        self._transfer_items = StockTransferItemRepository()
        self._sequences = SequenceRepository()
        self._audit = ActivityLogRepository()
        self._auth = auth

    # --------------------------------------------------------- the write path

    def apply_movement(
        self,
        product_id: int,
        warehouse_id: int,
        quantity: Decimal,
        movement_type: MovementType,
        conn: Any,
        *,
        reference_type: str | None = None,
        reference_id: int | None = None,
        note: str | None = None,
        user_id: int | None = None,
    ) -> Decimal:
        """Change a stock level and write the matching ledger row.

        `conn` is mandatory and must be an open transaction. The level is locked
        first, so a concurrent caller waits here rather than reading a figure
        that is about to change underneath it.

        Returns the new quantity on hand. Raises InsufficientStock rather than
        letting a level go negative.
        """
        quantity = self._quantity(quantity)
        if quantity == ZERO:
            raise ValidationError("A stock movement cannot be for zero.")
        if movement_type == MovementType.ADJUSTMENT and not (note or "").strip():
            raise ValidationError("An adjustment must say why.")

        current = self._stock.lock_level(product_id, warehouse_id, conn)
        new_balance = current + quantity

        if new_balance < ZERO and movement_type not in ALLOW_NEGATIVE:
            raise InsufficientStock(
                f"Only {current:g} in stock; this would take it to {new_balance:g}.")

        self._stock.set_quantity(product_id, warehouse_id, new_balance, conn)
        self._movements.record(
            product_id=product_id, warehouse_id=warehouse_id,
            movement_type=str(movement_type), quantity=quantity,
            balance_after=new_balance, reference_type=reference_type,
            reference_id=reference_id,
            user_id=user_id if user_id is not None else self._actor_id(),
            note=note, conn=conn)
        return new_balance

    def take(self, product_id: int, warehouse_id: int, quantity: Decimal,
             movement_type: MovementType, conn: Any, **kwargs) -> Decimal:
        """Remove stock. A convenience so callers never pass a negative by hand."""
        return self.apply_movement(product_id, warehouse_id,
                                   -self._quantity(quantity), movement_type,
                                   conn, **kwargs)

    def put(self, product_id: int, warehouse_id: int, quantity: Decimal,
            movement_type: MovementType, conn: Any, **kwargs) -> Decimal:
        """Add stock."""
        return self.apply_movement(product_id, warehouse_id,
                                   self._quantity(quantity), movement_type,
                                   conn, **kwargs)

    # ------------------------------------------------------------------ reads

    def quantity_on_hand(self, product_id: int, warehouse_id: int) -> Decimal:
        return self._stock.quantity_on_hand(product_id, warehouse_id)

    def levels(self, warehouse_id: int | None = None, search: str = "",
               only_nonzero: bool = False) -> list[dict]:
        return self._stock.levels(warehouse_id, search, only_nonzero)

    def low_stock(self, warehouse_id: int | None = None) -> list[dict]:
        """Products at or below their reorder level."""
        return self._stock.low_stock(warehouse_id)

    def ledger(self, product_id: int, warehouse_id: int | None = None,
               limit: int = 200) -> list[dict]:
        return self._movements.ledger(product_id, warehouse_id, limit)

    def movements_for(self, reference_type: str, reference_id: int) -> list[dict]:
        """Every movement a given document caused."""
        return self._movements.for_document(reference_type, reference_id)

    def find_drift(self) -> list[dict]:
        """Where the ledger and the levels disagree. Must always be empty."""
        return self._movements.drift()

    def transfers(self, limit: int = 100) -> list[dict]:
        return self._transfers.list(limit)

    def transfer_items(self, transfer_id: int) -> list[dict]:
        return self._transfers.items_of(transfer_id)

    # ------------------------------------------------------------ operations

    def set_opening(self, product_id: int, warehouse_id: int,
                    quantity: Decimal, note: str = "Opening stock") -> Decimal:
        """Record what was already on the shelves when the shop started."""
        actor = self._require()
        quantity = self._quantity(quantity)
        if quantity <= ZERO:
            raise ValidationError("Opening stock must be more than zero.")

        with transaction() as conn:
            current = self._stock.lock_level(product_id, warehouse_id, conn)
            if current != ZERO:
                raise ValidationError(
                    f"This product already holds {current:g} here. Use an "
                    "adjustment to correct it, so the change is explained.")
            balance = self.apply_movement(
                product_id, warehouse_id, quantity, MovementType.OPENING, conn,
                reference_type="opening", note=note, user_id=actor.id)
            self._audit.record(actor.id, "stock.opening", "stock", product_id,
                               {"warehouse_id": warehouse_id,
                                "quantity": str(quantity)}, conn)
        return balance

    def adjust(self, product_id: int, warehouse_id: int,
               new_quantity: Decimal, reason: str) -> Decimal:
        """Correct a level after a physical count, damage or loss.

        Takes the quantity that *should* be there and writes the difference, so
        the ledger records the correction rather than a silent overwrite.
        """
        actor = self._require()
        new_quantity = self._quantity(new_quantity)
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("An adjustment must say why.")
        if new_quantity < ZERO:
            raise ValidationError("Stock cannot be adjusted below zero.")

        with transaction() as conn:
            current = self._stock.lock_level(product_id, warehouse_id, conn)
            delta = new_quantity - current
            if delta == ZERO:
                return current
            balance = self.apply_movement(
                product_id, warehouse_id, delta, MovementType.ADJUSTMENT, conn,
                reference_type="adjustment", note=reason, user_id=actor.id)
            self._audit.record(actor.id, "stock.adjust", "stock", product_id,
                               {"warehouse_id": warehouse_id, "from": str(current),
                                "to": str(new_quantity), "reason": reason}, conn)
        log.info("Stock adjusted: product %s at %s from %s to %s (%s)",
                 product_id, warehouse_id, current, new_quantity, reason)
        return balance

    def transfer(self, from_warehouse_id: int, to_warehouse_id: int,
                 lines: list[tuple[int, Decimal]], note: str | None = None) -> int:
        """Move stock between warehouses.

        Two movements per product -- out of one, into the other -- plus the
        transfer document, all in one transaction. Either every line moves in
        both warehouses, or none of it does.
        """
        actor = self._require()
        if from_warehouse_id == to_warehouse_id:
            raise ValidationError("Choose two different outlets.")
        if not lines:
            raise ValidationError("Add at least one product to transfer.")

        cleaned: list[tuple[int, Decimal]] = []
        seen: set[int] = set()
        for product_id, quantity in lines:
            quantity = self._quantity(quantity)
            if quantity <= ZERO:
                raise ValidationError("Each line must be for more than zero.")
            if product_id in seen:
                raise ValidationError(
                    "The same product appears twice. Combine the lines instead.")
            seen.add(product_id)
            cleaned.append((product_id, quantity))

        # A stable order means two transfers running at once cannot deadlock by
        # locking the same rows in opposite order.
        cleaned.sort(key=lambda line: line[0])

        with transaction() as conn:
            reference_no = self._sequences.allocate("transfer", conn)
            transfer_id = self._transfers.insert({
                "reference_no": reference_no,
                "from_warehouse_id": from_warehouse_id,
                "to_warehouse_id": to_warehouse_id,
                "user_id": actor.id,
                "transfer_date": datetime.now(),
                "note": (note or "").strip() or None,
            }, conn)

            for product_id, quantity in cleaned:
                self._transfer_items.insert({
                    "transfer_id": transfer_id, "product_id": product_id,
                    "quantity": quantity}, conn)
                self.apply_movement(
                    product_id, from_warehouse_id, -quantity,
                    MovementType.TRANSFER_OUT, conn,
                    reference_type="transfer", reference_id=transfer_id,
                    user_id=actor.id)
                self.apply_movement(
                    product_id, to_warehouse_id, quantity,
                    MovementType.TRANSFER_IN, conn,
                    reference_type="transfer", reference_id=transfer_id,
                    user_id=actor.id)

            self._audit.record(actor.id, "stock.transfer", "stock_transfers",
                               transfer_id, {"reference_no": reference_no,
                                             "lines": len(cleaned)}, conn)
        log.info("Transfer %s: %d line(s) from %s to %s",
                 reference_no, len(cleaned), from_warehouse_id, to_warehouse_id)
        return transfer_id

    # -------------------------------------------------------------- internals

    def _require(self):
        if self._auth is None:
            raise ValidationError(
                "This action needs a signed-in user; the service was created "
                "without one.")
        return self._auth.require(MANAGE)

    def _actor_id(self) -> int | None:
        if self._auth is None:
            return None
        user = self._auth.current_user()
        return user.id if user else None

    @staticmethod
    def _quantity(value) -> Decimal:
        from decimal import InvalidOperation
        try:
            amount = Decimal(str(value).strip() or "0")
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError(f"Quantity must be a number, got {value!r}.") from exc
        return amount.quantize(Decimal("0.001"))
