"""The sales counter.

Everything else in the system is CRUD. This is the flow that has to be right,
because it is the one that can corrupt stock.

Completing a sale does all of this inside one transaction:

    allocate the invoice number -> lock each stock row -> check what is on hand
    -> write the sale and its lines -> deduct stock and write the ledger rows
    -> record the tender -> commit

The stock check happens *while holding the row lock*, not before the
transaction opens. Checking first and writing after is the classic POS bug: two
counters both read "1 left" and both sell it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from smartpos.core.constants import MovementType, PaymentMethod, PaymentStatus, SaleStatus
from smartpos.core.exceptions import ValidationError
from smartpos.core.logging import get_logger
from smartpos.db.connection import transaction
from smartpos.db.party_repository import CustomerPaymentRepository, CustomerRepository
from smartpos.db.product_repository import ProductRepository
from smartpos.db.sale_repository import SaleItemRepository, SaleRepository
from smartpos.db.sequence_repository import SequenceRepository
from smartpos.db.user_repository import ActivityLogRepository
from smartpos.services.auth_service import AuthService
from smartpos.services.stock_service import StockService

log = get_logger(__name__)

CREATE = "sale.create"
VIEW = "sale.view"

MONEY = Decimal("0.01")
QTY = Decimal("0.001")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100")


@dataclass
class SaleLine:
    """One product in the cart."""

    product_id: int
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal = ZERO
    tax_amount: Decimal = ZERO
    # Filled in by the service at save time from the product's current cost.
    # Copied onto the row so a later price change cannot rewrite past profit.
    unit_cost: Decimal = ZERO

    @property
    def gross(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(MONEY)

    @property
    def line_total(self) -> Decimal:
        return (self.gross - self.discount + self.tax_amount).quantize(MONEY)


@dataclass
class SaleDraft:
    """A cart, before it becomes an invoice."""

    warehouse_id: int
    customer_id: int | None = None          # None means the walk-in customer
    lines: list[SaleLine] = field(default_factory=list)
    discount_type: str = "fixed"            # fixed | percent
    discount_value: Decimal = ZERO
    tax_amount: Decimal = ZERO
    tendered: Decimal = ZERO                # what the customer handed over
    payment_method: str = "cash"
    note: str | None = None

    @property
    def subtotal(self) -> Decimal:
        return sum((line.line_total for line in self.lines), ZERO).quantize(MONEY)

    @property
    def discount(self) -> Decimal:
        """The discount in money, whichever way it was typed."""
        if self.discount_type == "percent":
            return (self.subtotal * self.discount_value / HUNDRED).quantize(MONEY)
        return Decimal(str(self.discount_value)).quantize(MONEY)

    @property
    def grand_total(self) -> Decimal:
        return (self.subtotal - self.discount + self.tax_amount).quantize(MONEY)

    @property
    def applied(self) -> Decimal:
        """How much of the tender settles the bill. The rest is change."""
        return min(self.tendered, self.grand_total).quantize(MONEY)

    @property
    def change(self) -> Decimal:
        return max(ZERO, self.tendered - self.grand_total).quantize(MONEY)


class SaleService:
    def __init__(self, auth: AuthService | None = None) -> None:
        self._repo = SaleRepository()
        self._items = SaleItemRepository()
        self._products = ProductRepository()
        self._customers = CustomerRepository()
        self._payments = CustomerPaymentRepository()
        self._sequences = SequenceRepository()
        self._audit = ActivityLogRepository()
        self._stock = StockService(auth)
        self._auth = auth

    # ------------------------------------------------------------------ reads

    def get(self, sale_id: int) -> dict | None:
        row = self._repo.get(sale_id)
        if row is None:
            return None
        row["items"] = self._items.for_sale(sale_id)
        return row

    def get_by_invoice(self, invoice_no: str) -> dict | None:
        """Used by the return screen, which starts from an invoice number."""
        row = self._repo.get_by_invoice((invoice_no or "").strip())
        if row is None:
            return None
        row["items"] = self._items.for_sale(row["id"])
        return row

    def list(self, **filters) -> list[dict]:
        return self._repo.list(**filters)

    def items_of(self, sale_id: int) -> list[dict]:
        return self._items.for_sale(sale_id)

    def list_held(self, warehouse_id: int | None = None) -> list[dict]:
        return self._repo.held(warehouse_id)

    def next_invoice_no(self) -> str:
        """Display only. `create` allocates the real one in its own transaction."""
        return self._sequences.peek("sale")

    # ------------------------------------------------------------ completing

    def create(self, draft: SaleDraft) -> int:
        """Complete a sale: save it, deduct stock, record the tender."""
        actor = self._require()
        self._validate(draft)

        with transaction() as conn:
            sale_id = self._write_sale(draft, conn, actor, status=SaleStatus.COMPLETED)

        log.info("Sale %s completed: %d line(s), total %s",
                 self._repo.get(sale_id)["invoice_no"], len(draft.lines),
                 draft.grand_total)
        return sale_id

    def hold(self, draft: SaleDraft) -> int:
        """Park an unfinished cart so the counter is not blocked.

        A held bill touches no stock -- the goods are still on the shelf. It
        takes a HLD- reference rather than an invoice number, so the invoice
        series stays gapless and unbroken for the sales that really happened.
        """
        actor = self._require()
        if not draft.lines:
            raise ValidationError("There is nothing to hold.")
        self._validate(draft, require_payment=False)

        with transaction() as conn:
            reference = self._sequences.allocate("hold", conn)
            sale_id = self._insert_header(draft, conn, actor, reference,
                                          SaleStatus.HELD, applied=ZERO)
            for line in self._prepared_lines(draft, conn):
                self._insert_item(sale_id, line, conn)
            self._audit.record(actor.id, "sale.hold", "sales", sale_id,
                               {"reference": reference}, conn)
        log.info("Bill held as %s", reference)
        return sale_id

    def resume(self, sale_id: int) -> dict:
        """Load a held bill back into a cart. It stays held until completed."""
        sale = self.get(sale_id)
        if sale is None:
            raise ValidationError("That held bill no longer exists.")
        if sale["status"] != str(SaleStatus.HELD):
            raise ValidationError(
                f"{sale['invoice_no']} is {sale['status']}, not a held bill.")
        return sale

    def complete_held(self, sale_id: int, tendered: Decimal | str | float = 0,
                      payment_method: str = "cash") -> int:
        """Turn a held bill into a real sale: allocate an invoice, deduct stock."""
        actor = self._require()

        with transaction() as conn:
            held = self._repo.get(sale_id, conn)
            if held is None:
                raise ValidationError("That held bill no longer exists.")
            if held["status"] != str(SaleStatus.HELD):
                raise ValidationError(
                    f"{held['invoice_no']} has already been {held['status']}.")

            items = self._items.for_sale(sale_id, conn)
            grand_total = Decimal(str(held["grand_total"]))
            tendered = self._money(tendered, "Tendered")
            applied = min(tendered, grand_total).quantize(MONEY)
            change = max(ZERO, tendered - grand_total).quantize(MONEY)

            invoice_no = self._sequences.allocate("sale", conn)
            self._repo.update(sale_id, {
                "invoice_no": invoice_no,
                "status": str(SaleStatus.COMPLETED),
                "sale_date": datetime.now(),
                "paid_amount": applied,
                "change_amount": change,
                "payment_method": payment_method,
                "payment_status": str(self._status_for(applied, grand_total)),
            }, conn)

            for item in sorted(items, key=lambda i: i["product_id"]):
                self._stock.take(
                    item["product_id"], held["warehouse_id"],
                    Decimal(str(item["quantity"])), MovementType.SALE, conn,
                    reference_type="sale", reference_id=sale_id, user_id=actor.id)

            if applied > ZERO:
                self._record_tender(held["customer_id"], sale_id, applied,
                                    payment_method, actor, conn)

            self._audit.record(actor.id, "sale.complete_held", "sales", sale_id,
                               {"invoice_no": invoice_no}, conn)
        log.info("Held bill completed as %s", invoice_no)
        return sale_id

    def cancel_held(self, sale_id: int) -> None:
        """Discard a held bill. No stock was ever taken, so nothing reverses."""
        actor = self._require()
        with transaction() as conn:
            held = self._repo.get(sale_id, conn)
            if held is None:
                return
            if held["status"] != str(SaleStatus.HELD):
                raise ValidationError(
                    "Only a held bill can be discarded. A completed sale is "
                    "corrected with a return.")
            self._items.delete_for_sale(sale_id, conn)
            self._repo.delete(sale_id, conn)
            self._audit.record(actor.id, "sale.discard_held", "sales", None,
                               {"reference": held["invoice_no"]}, conn)

    def record_payment(self, sale_id: int, amount: Decimal | str | float,
                       method: str = "cash") -> None:
        """Collect against a sale that was left partly or wholly unpaid."""
        actor = self._require()
        amount = self._money(amount, "Amount")
        if amount <= ZERO:
            raise ValidationError("Enter an amount to collect.")

        with transaction() as conn:
            sale = self._repo.get(sale_id, conn)
            if sale is None:
                raise ValidationError("That sale no longer exists.")
            if sale["status"] != str(SaleStatus.COMPLETED):
                raise ValidationError("Only a completed sale can take a payment.")

            grand_total = Decimal(str(sale["grand_total"]))
            already = self._repo.paid_total(sale_id, conn)
            outstanding = (grand_total - already).quantize(MONEY)
            if amount > outstanding:
                raise ValidationError(
                    f"{sale['invoice_no']} has {outstanding} left to pay. "
                    f"Collecting {amount} would overpay by {amount - outstanding}.")

            self._record_tender(sale["customer_id"], sale_id, amount, method,
                                actor, conn)
            paid = (already + amount).quantize(MONEY)
            self._repo.update(sale_id, {
                "paid_amount": paid,
                "payment_status": str(self._status_for(paid, grand_total)),
            }, conn)
            self._audit.record(actor.id, "sale.payment", "sales", sale_id,
                               {"amount": str(amount)}, conn)

    # -------------------------------------------------------------- internals

    def _write_sale(self, draft: SaleDraft, conn, actor,
                    status: SaleStatus) -> int:
        """The critical section. Must run inside an open transaction."""
        invoice_no = self._sequences.allocate("sale", conn)
        sale_id = self._insert_header(draft, conn, actor, invoice_no, status,
                                      applied=draft.applied)

        # A stable order stops two tills deadlocking on the same two products.
        for line in sorted(self._prepared_lines(draft, conn),
                           key=lambda line: line.product_id):
            self._insert_item(sale_id, line, conn)
            # The lock is taken here, inside the transaction, by apply_movement.
            self._stock.take(
                line.product_id, draft.warehouse_id, line.quantity,
                MovementType.SALE, conn,
                reference_type="sale", reference_id=sale_id, user_id=actor.id)

        if draft.applied > ZERO:
            customer_id = draft.customer_id or self._walk_in_id(conn)
            self._record_tender(customer_id, sale_id, draft.applied,
                                draft.payment_method, actor, conn)

        self._audit.record(actor.id, "sale.create", "sales", sale_id,
                           {"invoice_no": invoice_no,
                            "grand_total": str(draft.grand_total),
                            "lines": len(draft.lines)}, conn)
        return sale_id

    def _insert_header(self, draft: SaleDraft, conn, actor, reference: str,
                       status: SaleStatus, applied: Decimal) -> int:
        customer_id = draft.customer_id or self._walk_in_id(conn)
        grand_total = draft.grand_total
        return self._repo.insert({
            "invoice_no": reference,
            "customer_id": customer_id,
            "warehouse_id": draft.warehouse_id,
            "user_id": actor.id,
            "sale_date": datetime.now(),
            "subtotal": draft.subtotal,
            "discount_type": draft.discount_type,
            "discount_value": Decimal(str(draft.discount_value)).quantize(MONEY),
            "discount": draft.discount,
            "tax_amount": draft.tax_amount,
            "grand_total": grand_total,
            "paid_amount": applied,
            "change_amount": draft.change if status is SaleStatus.COMPLETED else ZERO,
            "payment_method": draft.payment_method,
            "payment_status": str(self._status_for(applied, grand_total)),
            "status": str(status),
            "note": (draft.note or "").strip() or None,
        }, conn)

    def _insert_item(self, sale_id: int, line: SaleLine, conn) -> None:
        self._items.insert({
            "sale_id": sale_id,
            "product_id": line.product_id,
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "unit_cost": line.unit_cost,
            "discount": line.discount,
            "tax_amount": line.tax_amount,
            "line_total": line.line_total,
        }, conn)

    def _prepared_lines(self, draft: SaleDraft, conn) -> list[SaleLine]:
        """Stamp each line with the product's cost as it stands right now."""
        for line in draft.lines:
            product = self._products.get(line.product_id, conn)
            if product is None:
                raise ValidationError(
                    f"A product on the bill no longer exists (#{line.product_id}).")
            line.unit_cost = Decimal(str(product["purchase_price"]))
        return draft.lines

    def _record_tender(self, customer_id: int, sale_id: int, amount: Decimal,
                       method: str, actor, conn) -> None:
        """Only what settles the bill is recorded. Change handed back is not
        money the shop received."""
        self._payments.insert({
            "customer_id": customer_id, "direction": "in", "amount": amount,
            "method": method if method in ("cash", "card", "mobile", "bank") else "cash",
            "reference_type": "sale", "reference_id": sale_id,
            "user_id": actor.id, "paid_at": datetime.now(), "note": None}, conn)

    def _walk_in_id(self, conn) -> int:
        row = self._customers.get_walk_in(conn)
        if row is None:
            raise ValidationError(
                "The walk-in customer row is missing; a counter sale needs it.")
        return row["id"]

    def _validate(self, draft: SaleDraft, require_payment: bool = True) -> None:
        if not draft.lines:
            raise ValidationError("The cart is empty.")
        if draft.discount_type not in ("fixed", "percent"):
            raise ValidationError(f"Unknown discount type {draft.discount_type!r}.")

        seen: set[int] = set()
        for line in draft.lines:
            line.quantity = self._quantity(line.quantity)
            line.unit_price = self._money(line.unit_price, "Price")
            line.discount = self._money(line.discount, "Line discount")
            line.tax_amount = self._money(line.tax_amount, "Line tax")
            if line.quantity <= Decimal("0"):
                raise ValidationError("Each line must be for more than zero.")
            if line.discount > line.gross:
                raise ValidationError(
                    "A line discount cannot exceed the value of the line.")
            if line.product_id in seen:
                raise ValidationError(
                    "The same product appears twice. Change the quantity instead.")
            seen.add(line.product_id)

        draft.discount_value = self._money(draft.discount_value, "Discount")
        draft.tax_amount = self._money(draft.tax_amount, "Tax")
        if draft.discount_type == "percent" and draft.discount_value > HUNDRED:
            raise ValidationError("A percentage discount cannot exceed 100%.")
        if draft.discount > draft.subtotal:
            raise ValidationError(
                f"The discount ({draft.discount}) is more than the bill "
                f"({draft.subtotal}).")

        if require_payment:
            draft.tendered = self._money(draft.tendered, "Tendered")

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
        return self._auth.require(CREATE)

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
