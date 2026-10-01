"""Suppliers and amounts payable to them.

The mirror of the customer ledger, running the other way:

    payable = opening + purchases - money paid + money received back

Positive means the shop owes the supplier.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from smartpos.core.exceptions import ValidationError
from smartpos.core.logging import get_logger
from smartpos.db.connection import transaction
from smartpos.db.party_repository import SupplierPaymentRepository, SupplierRepository
from smartpos.db.user_repository import ActivityLogRepository
from smartpos.models.supplier import Supplier
from smartpos.services.auth_service import AuthService

log = get_logger(__name__)

MANAGE = "supplier.manage"

ZERO = Decimal("0.00")
MAX_NAME = 120
PAYMENT_METHODS = ("cash", "card", "mobile", "bank")


def _to_model(row: dict) -> Supplier:
    return Supplier(
        id=row["id"], name=row["name"], phone=row.get("phone"),
        email=row.get("email"), address=row.get("address"),
        opening_balance=row["opening_balance"], is_active=bool(row["is_active"]),
        created_at=row.get("created_at"), updated_at=row.get("updated_at"),
    )


class SupplierService:
    def __init__(self, auth: AuthService | None = None) -> None:
        self._repo = SupplierRepository()
        self._payments = SupplierPaymentRepository()
        self._audit = ActivityLogRepository()
        self._auth = auth

    # ------------------------------------------------------------------ reads

    def search(self, term: str = "", limit: int = 50,
               active_only: bool = True) -> list[Supplier]:
        return [_to_model(r) for r in
                self._repo.search((term or "").strip(), limit, active_only)]

    def get(self, supplier_id: int) -> Supplier | None:
        row = self._repo.get(supplier_id)
        return _to_model(row) if row else None

    def payable_balance(self, supplier_id: int) -> Decimal:
        """What the shop owes this supplier. Positive means money is due out."""
        p = self._repo.balance_parts(supplier_id)
        return (p["opening"] + p["purchases"] - p["paid"] + p["refunded"]).quantize(ZERO)

    def balance_breakdown(self, supplier_id: int) -> dict[str, Decimal]:
        parts = self._repo.balance_parts(supplier_id)
        parts["payable"] = (parts["opening"] + parts["purchases"]
                            - parts["paid"] + parts["refunded"]).quantize(ZERO)
        return parts

    def ledger(self, supplier_id: int, limit: int = 200) -> list[dict]:
        return self._repo.ledger(supplier_id, limit)

    # ----------------------------------------------------------------- writes

    def create(self, name: str, phone: str | None = None, email: str | None = None,
               address: str | None = None,
               opening_balance: Decimal | str | float = 0) -> int:
        actor = self._require()
        name = self._clean_name(name)
        opening = self._amount(opening_balance, "Opening balance", allow_negative=True)

        with transaction() as conn:
            if self._repo.name_taken(name, conn=conn):
                raise ValidationError(f"A supplier called {name!r} already exists.")
            supplier_id = self._repo.insert(
                {"name": name, "phone": (phone or "").strip() or None,
                 "email": (email or "").strip() or None,
                 "address": (address or "").strip() or None,
                 "opening_balance": opening}, conn)
            self._audit.record(actor.id, "supplier.create", "suppliers",
                               supplier_id, {"name": name}, conn)
        log.info("Supplier created: %s", name)
        return supplier_id

    def update(self, supplier_id: int, name: str | None = None,
               phone: str | None = None, email: str | None = None,
               address: str | None = None,
               opening_balance: Decimal | str | float | None = None) -> None:
        actor = self._require()

        with transaction() as conn:
            existing = self._repo.get(supplier_id, conn)
            if existing is None:
                raise ValidationError("That supplier no longer exists.")

            changes: dict = {}
            if name is not None:
                cleaned = self._clean_name(name)
                if cleaned != existing["name"]:
                    if self._repo.name_taken(cleaned, supplier_id, conn):
                        raise ValidationError(
                            f"A supplier called {cleaned!r} already exists.")
                    changes["name"] = cleaned
            for key, value in (("phone", phone), ("email", email), ("address", address)):
                if value is not None:
                    changes[key] = value.strip() or None
            if opening_balance is not None:
                changes["opening_balance"] = self._amount(
                    opening_balance, "Opening balance", allow_negative=True)

            if not changes:
                return
            self._repo.update(supplier_id, changes, conn)
            self._audit.record(actor.id, "supplier.update", "suppliers",
                               supplier_id, {k: str(v) for k, v in changes.items()}, conn)

    def pay(self, supplier_id: int, amount: Decimal | str | float,
            method: str = "cash", note: str | None = None) -> int:
        """Record money paid to a supplier."""
        actor = self._require()
        amount = self._amount(amount, "Amount")
        if amount == ZERO:
            raise ValidationError("Enter an amount to pay.")
        if method not in PAYMENT_METHODS:
            raise ValidationError(f"Unknown payment method {method!r}.")

        with transaction() as conn:
            supplier = self._repo.get(supplier_id, conn)
            if supplier is None:
                raise ValidationError("That supplier no longer exists.")

            outstanding = self.payable_balance(supplier_id)
            if amount > outstanding:
                raise ValidationError(
                    f"The shop owes {supplier['name']} {outstanding}. Paying "
                    f"{amount} would overpay by {amount - outstanding}; record "
                    "the purchase it covers first.")

            payment_id = self._payments.insert(
                {"supplier_id": supplier_id, "direction": "out", "amount": amount,
                 "method": method, "reference_type": None, "reference_id": None,
                 "user_id": actor.id, "paid_at": datetime.now(),
                 "note": (note or "").strip() or None}, conn)
            self._audit.record(actor.id, "supplier.pay", "suppliers", supplier_id,
                               {"amount": str(amount), "method": method}, conn)
        log.info("Paid %s to %s", amount, supplier["name"])
        return payment_id

    def deactivate(self, supplier_id: int) -> None:
        actor = self._require()
        with transaction() as conn:
            if self._repo.get(supplier_id, conn) is None:
                raise ValidationError("That supplier no longer exists.")
            self._repo.set_active(supplier_id, False, conn)
            self._audit.record(actor.id, "supplier.deactivate", "suppliers",
                               supplier_id, conn=conn)

    def activate(self, supplier_id: int) -> None:
        actor = self._require()
        with transaction() as conn:
            if self._repo.get(supplier_id, conn) is None:
                raise ValidationError("That supplier no longer exists.")
            self._repo.set_active(supplier_id, True, conn)
            self._audit.record(actor.id, "supplier.activate", "suppliers",
                               supplier_id, conn=conn)

    def delete(self, supplier_id: int) -> None:
        actor = self._require()
        with transaction() as conn:
            existing = self._repo.get(supplier_id, conn)
            if existing is None:
                raise ValidationError("That supplier no longer exists.")
            if self._repo.has_activity(supplier_id, conn):
                raise ValidationError(
                    f"{existing['name']} has purchases or payments on record and "
                    "cannot be deleted. Disable the account instead.")
            self._audit.record(actor.id, "supplier.delete", "suppliers", None,
                               {"name": existing["name"]}, conn)
            self._repo.delete(supplier_id, conn)

    # -------------------------------------------------------------- internals

    def _require(self):
        if self._auth is None:
            raise ValidationError(
                "This action needs a signed-in user; the service was created "
                "without one.")
        return self._auth.require(MANAGE)

    @staticmethod
    def _clean_name(name: str) -> str:
        name = " ".join((name or "").split())
        if not name:
            raise ValidationError("Name is required.")
        if len(name) > MAX_NAME:
            raise ValidationError(f"Name cannot exceed {MAX_NAME} characters.")
        return name

    @staticmethod
    def _amount(value, label: str, allow_negative: bool = False) -> Decimal:
        try:
            amount = Decimal(str(value).strip() or "0")
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError(f"{label} must be a number, got {value!r}.") from exc
        if amount < 0 and not allow_negative:
            raise ValidationError(f"{label} cannot be negative.")
        return amount.quantize(ZERO)
