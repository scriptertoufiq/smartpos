"""Recording purchases from suppliers.

A purchase is the first thing that puts goods on a shelf. Saving one writes the
header, its lines, the stock increases and the ledger movements in a single
transaction -- so a failure half way through leaves no purchase and no stock
change, rather than goods that exist on paper only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from smartpos.core.constants import MovementType, PaymentStatus
from smartpos.core.exceptions import ValidationError
from smartpos.core.logging import get_logger
from smartpos.db.connection import transaction
from smartpos.db.party_repository import SupplierPaymentRepository, SupplierRepository
from smartpos.db.product_repository import ProductRepository
from smartpos.db.purchase_repository import PurchaseItemRepository, PurchaseRepository
from smartpos.db.sequence_repository import SequenceRepository
from smartpos.db.user_repository import ActivityLogRepository
from smartpos.services.auth_service import AuthService
from smartpos.services.stock_service import StockService

log = get_logger(__name__)

MANAGE = "purchase.manage"
MONEY = Decimal("0.01")
QTY = Decimal("0.001")
ZERO = Decimal("0.00")
PAYMENT_METHODS = ("cash", "card", "mobile", "bank")


@dataclass
class PurchaseLine:
    """One product on a purchase, before it is saved."""

    product_id: int
    quantity: Decimal
    unit_cost: Decimal
    discount: Decimal = ZERO
    tax_amount: Decimal = ZERO

    @property
    def line_total(self) -> Decimal:
        gross = (self.quantity * self.unit_cost).quantize(MONEY)
        return (gross - self.discount + self.tax_amount).quantize(MONEY)


@dataclass
class PurchaseDraft:
    """Everything needed to record a purchase."""

    supplier_id: int
    warehouse_id: int
    lines: list[PurchaseLine] = field(default_factory=list)
    purchase_date: datetime | None = None
    discount: Decimal = ZERO
    tax_amount: Decimal = ZERO
    paid_amount: Decimal = ZERO
    payment_method: str = "cash"
    note: str | None = None

    @property
    def subtotal(self) -> Decimal:
        return sum((line.line_total for line in self.lines), ZERO).quantize(MONEY)

    @property
    def grand_total(self) -> Decimal:
        return (self.subtotal - self.discount + self.tax_amount).quantize(MONEY)


class PurchaseService:
    def __init__(self, auth: AuthService | None = None) -> None:
        self._repo = PurchaseRepository()
        self._items = PurchaseItemRepository()
        self._products = ProductRepository()
        self._suppliers = SupplierRepository()
        self._payments = SupplierPaymentRepository()
        self._sequences = SequenceRepository()
        self._audit = ActivityLogRepository()
        self._stock = StockService(auth)
        self._auth = auth

    # ------------------------------------------------------------------ reads

    def get(self, purchase_id: int) -> dict | None:
        row = self._repo.get(purchase_id)
        if row is None:
            return None
        row["items"] = self._items.for_purchase(purchase_id)
        return row

    def list(self, date_from: date | None = None, date_to: date | None = None,
             supplier_id: int | None = None, warehouse_id: int | None = None,
             payment_status: str | None = None, limit: int = 200) -> list[dict]:
        return self._repo.list(date_from, date_to, supplier_id, warehouse_id,
                               payment_status, limit)

    def items_of(self, purchase_id: int) -> list[dict]:
        return self._items.for_purchase(purchase_id)

    def next_reference_no(self) -> str:
        """What the next reference will look like. Display only -- `create`
        allocates the real one inside its own transaction."""
        return self._sequences.peek("purchase")

    # ----------------------------------------------------------------- writes

    def create(self, draft: PurchaseDraft) -> int:
        """Save a purchase and raise stock in the destination warehouse."""
        actor = self._require()
        self._validate(draft)

        subtotal = draft.subtotal
        grand_total = draft.grand_total
        paid = self._money(draft.paid_amount, "Paid amount")
        if paid > grand_total:
            raise ValidationError(
                f"Paying {paid} against a total of {grand_total} would overpay "
                f"by {paid - grand_total}.")

        # Stable order: two purchases landing at once cannot deadlock by
        # locking the same stock rows in opposite order.
        lines = sorted(draft.lines, key=lambda line: line.product_id)

        with transaction() as conn:
            supplier = self._suppliers.get(draft.supplier_id, conn)
            if supplier is None:
                raise ValidationError("That supplier no longer exists.")

            reference_no = self._sequences.allocate("purchase", conn)
            purchase_id = self._repo.insert({
                "reference_no": reference_no,
                "supplier_id": draft.supplier_id,
                "warehouse_id": draft.warehouse_id,
                "user_id": actor.id,
                "purchase_date": draft.purchase_date or datetime.now(),
                "subtotal": subtotal,
                "discount": draft.discount,
                "tax_amount": draft.tax_amount,
                "grand_total": grand_total,
                "paid_amount": paid,
                "payment_status": str(self._status_for(paid, grand_total)),
                "note": (draft.note or "").strip() or None,
            }, conn)

            for line in lines:
                self._items.insert({
                    "purchase_id": purchase_id,
                    "product_id": line.product_id,
                    "quantity": line.quantity,
                    "unit_cost": line.unit_cost,
                    "discount": line.discount,
                    "tax_amount": line.tax_amount,
                    "line_total": line.line_total,
                }, conn)

                self._stock.put(
                    line.product_id, draft.warehouse_id, line.quantity,
                    MovementType.PURCHASE, conn,
                    reference_type="purchase", reference_id=purchase_id,
                    user_id=actor.id)

                # The latest cost, for margin display and the next reorder.
                # Historic profit is unaffected: a sale copies the cost of its
                # own day onto its line.
                self._products.update(line.product_id,
                                      {"purchase_price": line.unit_cost}, conn)

            if paid > ZERO:
                self._payments.insert({
                    "supplier_id": draft.supplier_id, "direction": "out",
                    "amount": paid, "method": draft.payment_method,
                    "reference_type": "purchase", "reference_id": purchase_id,
                    "user_id": actor.id, "paid_at": datetime.now(),
                    "note": None}, conn)

            self._audit.record(actor.id, "purchase.create", "purchases",
                               purchase_id, {"reference_no": reference_no,
                                             "grand_total": str(grand_total),
                                             "lines": len(lines)}, conn)

        log.info("Purchase %s: %d line(s), total %s", reference_no,
                 len(lines), grand_total)
        return purchase_id

    def record_payment(self, purchase_id: int, amount: Decimal | str | float,
                       method: str = "cash", note: str | None = None) -> None:
        """Pay against a purchase and recalculate its payment status."""
        actor = self._require()
        amount = self._money(amount, "Amount")
        if amount <= ZERO:
            raise ValidationError("Enter an amount to pay.")
        if method not in PAYMENT_METHODS:
            raise ValidationError(f"Unknown payment method {method!r}.")

        with transaction() as conn:
            purchase = self._repo.get(purchase_id, conn)
            if purchase is None:
                raise ValidationError("That purchase no longer exists.")

            already = self._repo.paid_total(purchase_id, conn)
            grand_total = Decimal(str(purchase["grand_total"]))
            outstanding = (grand_total - already).quantize(MONEY)
            if amount > outstanding:
                raise ValidationError(
                    f"{purchase['reference_no']} has {outstanding} left to pay. "
                    f"Paying {amount} would overpay by {amount - outstanding}.")

            self._payments.insert({
                "supplier_id": purchase["supplier_id"], "direction": "out",
                "amount": amount, "method": method,
                "reference_type": "purchase", "reference_id": purchase_id,
                "user_id": actor.id, "paid_at": datetime.now(),
                "note": (note or "").strip() or None}, conn)

            paid = (already + amount).quantize(MONEY)
            self._repo.update(purchase_id, {
                "paid_amount": paid,
                "payment_status": str(self._status_for(paid, grand_total)),
            }, conn)

            self._audit.record(actor.id, "purchase.payment", "purchases",
                               purchase_id, {"amount": str(amount)}, conn)
        log.info("Paid %s against purchase %s", amount, purchase["reference_no"])

    def recompute_paid(self, purchase_id: int) -> Decimal:
        """Rebuild the cached paid_amount from the payment rows.

        The cache exists so the purchase list needs no subquery per row; this is
        how it is proved right.
        """
        with transaction() as conn:
            purchase = self._repo.get(purchase_id, conn)
            if purchase is None:
                raise ValidationError("That purchase no longer exists.")
            paid = self._repo.paid_total(purchase_id, conn)
            self._repo.update(purchase_id, {
                "paid_amount": paid,
                "payment_status": str(
                    self._status_for(paid, Decimal(str(purchase["grand_total"])))),
            }, conn)
        return paid

    # -------------------------------------------------------------- internals

    def _validate(self, draft: PurchaseDraft) -> None:
        if not draft.lines:
            raise ValidationError("A purchase needs at least one product.")

        seen: set[int] = set()
        for line in draft.lines:
            line.quantity = self._quantity(line.quantity)
            line.unit_cost = self._money(line.unit_cost, "Unit cost")
            line.discount = self._money(line.discount, "Line discount")
            line.tax_amount = self._money(line.tax_amount, "Line tax")
            if line.quantity <= Decimal("0"):
                raise ValidationError("Each line must be for more than zero.")
            if line.discount > (line.quantity * line.unit_cost):
                raise ValidationError(
                    "A line discount cannot exceed the value of the line.")
            seen.add(line.product_id)

        draft.discount = self._money(draft.discount, "Discount")
        draft.tax_amount = self._money(draft.tax_amount, "Tax")
        if draft.discount > draft.subtotal:
            raise ValidationError(
                f"The discount ({draft.discount}) is more than the purchase "
                f"itself ({draft.subtotal}).")
        if draft.payment_method not in PAYMENT_METHODS:
            raise ValidationError(f"Unknown payment method {draft.payment_method!r}.")

    @staticmethod
    def _status_for(paid: Decimal, grand_total: Decimal) -> PaymentStatus:
        if paid <= ZERO:
            return PaymentStatus.DUE
        if paid >= grand_total:
            return PaymentStatus.PAID
        return PaymentStatus.PARTIAL

    def _require(self):
        if self._auth is None:
            raise ValidationError(
                "This action needs a signed-in user; the service was created "
                "without one.")
        return self._auth.require(MANAGE)

    @staticmethod
    def _money(value, label: str) -> Decimal:
        try:
            amount = Decimal(str(value).strip() or "0")
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError(f"{label} must be a number, got {value!r}.") from exc
        if amount < 0:
            raise ValidationError(f"{label} cannot be negative.")
        return amount.quantize(MONEY)

    @staticmethod
    def _quantity(value) -> Decimal:
        try:
            amount = Decimal(str(value).strip() or "0")
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError(f"Quantity must be a number, got {value!r}.") from exc
        return amount.quantize(QTY)
