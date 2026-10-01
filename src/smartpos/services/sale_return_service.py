"""Returns against an existing sale.

A return is the only way to correct a completed sale, because a completed sale
is immutable. It puts the goods back on the shelf and settles the money, either
by handing cash over or by reducing what the customer owes.

The quantity check runs twice: once to show the cashier what may be returned,
and again inside the transaction holding a lock. Two managers working the same
invoice at once must not together give back more than was sold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from smartpos.core.constants import MovementType
from smartpos.core.exceptions import ValidationError
from smartpos.core.logging import get_logger
from smartpos.db.connection import transaction
from smartpos.db.party_repository import CustomerPaymentRepository
from smartpos.db.sale_repository import SaleItemRepository, SaleRepository
from smartpos.db.sale_return_repository import (
    SaleReturnItemRepository,
    SaleReturnRepository,
)
from smartpos.db.sequence_repository import SequenceRepository
from smartpos.db.user_repository import ActivityLogRepository
from smartpos.services.auth_service import AuthService
from smartpos.services.stock_service import StockService

log = get_logger(__name__)

CREATE = "sale_return.create"
VIEW = "sale_return.view"

MONEY = Decimal("0.01")
QTY = Decimal("0.001")
ZERO = Decimal("0.00")

REFUND_CASH = "cash"
REFUND_DUE = "due_adjustment"


@dataclass
class ReturnableLine:
    """One sold line, and how much of it may still come back."""

    sale_item_id: int
    product_id: int
    sku: str
    product_name: str
    sold: Decimal
    already_returned: Decimal
    unit_price: Decimal

    @property
    def returnable(self) -> Decimal:
        return (self.sold - self.already_returned).quantize(QTY)


@dataclass
class ReturnLine:
    """One line the manager has chosen to take back."""

    sale_item_id: int
    quantity: Decimal


@dataclass
class ReturnDraft:
    sale_id: int
    lines: list[ReturnLine] = field(default_factory=list)
    refund_method: str = REFUND_CASH
    reason: str | None = None
    warehouse_id: int | None = None      # defaults to the one the sale left from


class SaleReturnService:
    def __init__(self, auth: AuthService | None = None) -> None:
        self._repo = SaleReturnRepository()
        self._items = SaleReturnItemRepository()
        self._sales = SaleRepository()
        self._sale_items = SaleItemRepository()
        self._payments = CustomerPaymentRepository()
        self._sequences = SequenceRepository()
        self._audit = ActivityLogRepository()
        self._stock = StockService(auth)
        self._auth = auth

    # ------------------------------------------------------------------ reads

    def get(self, return_id: int) -> dict | None:
        row = self._repo.get(return_id)
        if row is None:
            return None
        row["items"] = self._items.for_return(return_id)
        return row

    def list(self, date_from: date | None = None, date_to: date | None = None,
             sale_id: int | None = None, limit: int = 200) -> list[dict]:
        return self._repo.list(date_from, date_to, sale_id, limit)

    def items_of(self, return_id: int) -> list[dict]:
        return self._items.for_return(return_id)

    def returnable_quantities(self, sale_id: int) -> list[ReturnableLine]:
        """Per sold line, how much may still be returned."""
        sale = self._sales.get(sale_id)
        if sale is None:
            raise ValidationError("That sale no longer exists.")
        if sale["status"] != "completed":
            raise ValidationError(
                f"{sale['invoice_no']} is {sale['status']}; only a completed "
                "sale can be returned against.")

        already = self._repo.returned_per_line(sale_id)
        lines = []
        for item in self._sale_items.for_sale(sale_id):
            lines.append(ReturnableLine(
                sale_item_id=item["id"], product_id=item["product_id"],
                sku=item["sku"], product_name=item["product_name"],
                sold=Decimal(str(item["quantity"])),
                already_returned=already.get(item["id"], ZERO),
                unit_price=Decimal(str(item["unit_price"]))))
        return lines

    def total_returned(self, sale_id: int) -> Decimal:
        return self._repo.total_returned(sale_id)

    def next_reference_no(self) -> str:
        return self._sequences.peek("sale_return")

    # ----------------------------------------------------------------- writes

    def create(self, draft: ReturnDraft) -> int:
        """Save the return, put the quantity back into stock, settle the refund."""
        actor = self._require()
        if not draft.lines:
            raise ValidationError("Choose at least one line to return.")
        if draft.refund_method not in (REFUND_CASH, REFUND_DUE):
            raise ValidationError(f"Unknown refund method {draft.refund_method!r}.")

        wanted: dict[int, Decimal] = {}
        for line in draft.lines:
            quantity = self._quantity(line.quantity)
            if quantity <= ZERO:
                continue                      # a zero line is simply not returned
            if line.sale_item_id in wanted:
                raise ValidationError(
                    "The same line appears twice. Combine the quantities instead.")
            wanted[line.sale_item_id] = quantity
        if not wanted:
            raise ValidationError("Choose at least one line to return.")

        with transaction() as conn:
            sale = self._sales.get(draft.sale_id, conn)
            if sale is None:
                raise ValidationError("That sale no longer exists.")
            if sale["status"] != "completed":
                raise ValidationError(
                    f"{sale['invoice_no']} is {sale['status']}; only a completed "
                    "sale can be returned against.")

            # Lock every existing return row for this sale, then re-read the
            # totals. A second manager working the same invoice waits here.
            already = self._repo.lock_returned_per_line(draft.sale_id, conn)
            sold = {item["id"]: item for item in
                    self._sale_items.for_sale(draft.sale_id, conn)}

            total = ZERO
            prepared = []
            for sale_item_id, quantity in wanted.items():
                item = sold.get(sale_item_id)
                if item is None:
                    raise ValidationError(
                        "A line being returned does not belong to this invoice.")
                remaining = (Decimal(str(item["quantity"]))
                             - already.get(sale_item_id, ZERO))
                if quantity > remaining:
                    # Decimal keeps trailing zeros even under :g, so 3.000 has
                    # to be normalised before it reads like a quantity.
                    def plain(value: Decimal) -> str:
                        return f"{value.normalize():f}"

                    raise ValidationError(
                        f"{item['product_name']}: {plain(remaining)} left to return "
                        f"of {plain(Decimal(str(item['quantity'])))} sold"
                        + (f" ({plain(already[sale_item_id])} already back)"
                           if already.get(sale_item_id) else "")
                        + f". Cannot return {plain(quantity)}.")

                unit_price = Decimal(str(item["unit_price"]))
                line_total = (quantity * unit_price).quantize(MONEY)
                total += line_total
                prepared.append((item, quantity, unit_price, line_total))

            warehouse_id = draft.warehouse_id or sale["warehouse_id"]
            reference_no = self._sequences.allocate("sale_return", conn)
            return_id = self._repo.insert({
                "reference_no": reference_no,
                "sale_id": draft.sale_id,
                "warehouse_id": warehouse_id,
                "user_id": actor.id,
                "return_date": datetime.now(),
                "total_amount": total.quantize(MONEY),
                "refund_method": draft.refund_method,
                "reason": (draft.reason or "").strip() or None,
            }, conn)

            # Stable order: two returns at once cannot deadlock on stock rows.
            for item, quantity, unit_price, line_total in sorted(
                    prepared, key=lambda row: row[0]["product_id"]):
                self._items.insert({
                    "sale_return_id": return_id,
                    "sale_item_id": item["id"],
                    "product_id": item["product_id"],
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "line_total": line_total,
                }, conn)
                self._stock.put(
                    item["product_id"], warehouse_id, quantity,
                    MovementType.SALE_RETURN, conn,
                    reference_type="sale_return", reference_id=return_id,
                    user_id=actor.id)

            if draft.refund_method == REFUND_CASH and total > ZERO:
                # Money out of the till. Recorded against the customer so the
                # ledger nets correctly: the return credits them, this debits
                # them again, and they end up square.
                self._payments.insert({
                    "customer_id": sale["customer_id"], "direction": "out",
                    "amount": total.quantize(MONEY), "method": "cash",
                    "reference_type": "sale_return", "reference_id": return_id,
                    "user_id": actor.id, "paid_at": datetime.now(),
                    "note": None}, conn)

            self._audit.record(actor.id, "sale_return.create", "sale_returns",
                               return_id, {"reference_no": reference_no,
                                           "invoice_no": sale["invoice_no"],
                                           "total": str(total)}, conn)

        log.info("Return %s against %s: %s", reference_no, sale["invoice_no"], total)
        return return_id

    # -------------------------------------------------------------- internals

    def _require(self):
        if self._auth is None:
            raise ValidationError(
                "This action needs a signed-in user; the service was created "
                "without one.")
        return self._auth.require(CREATE)

    @staticmethod
    def _quantity(value) -> Decimal:
        try:
            amount = Decimal(str(value).strip() or "0")
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError(f"Quantity must be a number, got {value!r}.") from exc
        if amount < 0:
            raise ValidationError("A returned quantity cannot be negative.")
        return amount.quantize(QTY)
