"""Customers, their purchase history and their dues.

A customer's due is a ledger, not a stored number:

    due = opening + completed sales - returns - money received + cash refunded

The last term looks odd until you follow a refund through. A paid sale nets to
zero; returning part of it puts the shop in debt; handing the cash back settles
that. Without the `+ refunded` term the shop would appear to owe money it has
already paid out.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from smartpos.core.exceptions import ValidationError
from smartpos.core.logging import get_logger
from smartpos.db.connection import transaction
from smartpos.db.party_repository import CustomerPaymentRepository, CustomerRepository
from smartpos.db.user_repository import ActivityLogRepository
from smartpos.models.customer import Customer
from smartpos.services.auth_service import AuthService

log = get_logger(__name__)

MANAGE = "customer.manage"
VIEW = "customer.view"

ZERO = Decimal("0.00")
MAX_NAME = 120
PAYMENT_METHODS = ("cash", "card", "mobile", "bank")


def _to_model(row: dict) -> Customer:
    return Customer(
        id=row["id"], name=row["name"], phone=row.get("phone"),
        email=row.get("email"), address=row.get("address"),
        opening_balance=row["opening_balance"],
        is_walk_in=bool(row["is_walk_in"]), is_active=bool(row["is_active"]),
        created_at=row.get("created_at"), updated_at=row.get("updated_at"),
    )


class CustomerService:
    def __init__(self, auth: AuthService | None = None) -> None:
        self._repo = CustomerRepository()
        self._payments = CustomerPaymentRepository()
        self._audit = ActivityLogRepository()
        self._auth = auth

    # ------------------------------------------------------------------ reads

    def search(self, term: str = "", limit: int = 50,
               active_only: bool = True) -> list[Customer]:
        return [_to_model(r) for r in
                self._repo.search((term or "").strip(), limit, active_only)]

    def get(self, customer_id: int) -> Customer | None:
        row = self._repo.get(customer_id)
        return _to_model(row) if row else None

    def get_walk_in(self) -> Customer:
        """The default customer used by quick counter sales."""
        row = self._repo.get_walk_in()
        if row is None:
            raise ValidationError(
                "The walk-in customer row is missing. Sales at the counter need "
                "it; restore it from the seed data.")
        return _to_model(row)

    def due_balance(self, customer_id: int) -> Decimal:
        """What this customer owes. Positive means they owe the shop."""
        p = self._repo.balance_parts(customer_id)
        return (p["opening"] + p["sales"] - p["returns"]
                - p["received"] + p["refunded"]).quantize(ZERO)

    def balance_breakdown(self, customer_id: int) -> dict[str, Decimal]:
        """The parts of the due, plus the total, so a figure can be explained."""
        parts = self._repo.balance_parts(customer_id)
        parts["due"] = (parts["opening"] + parts["sales"] - parts["returns"]
                        - parts["received"] + parts["refunded"]).quantize(ZERO)
        return parts

    def ledger(self, customer_id: int, limit: int = 200) -> list[dict]:
        return self._repo.ledger(customer_id, limit)

    # ----------------------------------------------------------------- writes

    def create(self, name: str, phone: str | None = None, email: str | None = None,
               address: str | None = None,
               opening_balance: Decimal | str | float = 0) -> int:
        actor = self._require()
        name = self._clean_name(name)
        opening = self._amount(opening_balance, "Opening balance", allow_negative=True)

        with transaction() as conn:
            if self._repo.name_taken(name, conn=conn):
                raise ValidationError(f"A customer called {name!r} already exists.")
            customer_id = self._repo.insert(
                {"name": name, "phone": (phone or "").strip() or None,
                 "email": (email or "").strip() or None,
                 "address": (address or "").strip() or None,
                 "opening_balance": opening}, conn)
            self._audit.record(actor.id, "customer.create", "customers",
                               customer_id, {"name": name}, conn)
        log.info("Customer created: %s", name)
        return customer_id

    def update(self, customer_id: int, name: str | None = None,
               phone: str | None = None, email: str | None = None,
               address: str | None = None,
               opening_balance: Decimal | str | float | None = None) -> None:
        actor = self._require()

        with transaction() as conn:
            existing = self._repo.get(customer_id, conn)
            if existing is None:
                raise ValidationError("That customer no longer exists.")

            changes: dict = {}
            if name is not None:
                cleaned = self._clean_name(name)
                if cleaned != existing["name"]:
                    if existing["is_walk_in"]:
                        raise ValidationError(
                            "The walk-in customer cannot be renamed. Every "
                            "counter sale without a named buyer uses it.")
                    if self._repo.name_taken(cleaned, customer_id, conn):
                        raise ValidationError(
                            f"A customer called {cleaned!r} already exists.")
                    changes["name"] = cleaned
            for key, value in (("phone", phone), ("email", email), ("address", address)):
                if value is not None:
                    changes[key] = value.strip() or None
            if opening_balance is not None:
                changes["opening_balance"] = self._amount(
                    opening_balance, "Opening balance", allow_negative=True)

            if not changes:
                return
            self._repo.update(customer_id, changes, conn)
            self._audit.record(actor.id, "customer.update", "customers",
                               customer_id, {k: str(v) for k, v in changes.items()}, conn)

    def collect_due(self, customer_id: int, amount: Decimal | str | float,
                    method: str = "cash", note: str | None = None) -> int:
        """Record money received against what a customer owes."""
        actor = self._require()
        amount = self._amount(amount, "Amount")
        if amount == ZERO:
            raise ValidationError("Enter an amount to collect.")
        if method not in PAYMENT_METHODS:
            raise ValidationError(f"Unknown payment method {method!r}.")

        with transaction() as conn:
            customer = self._repo.get(customer_id, conn)
            if customer is None:
                raise ValidationError("That customer no longer exists.")

            outstanding = self.due_balance(customer_id)
            if amount > outstanding:
                raise ValidationError(
                    f"{customer['name']} owes {outstanding}. Collecting "
                    f"{amount} would leave the shop holding money it cannot "
                    "account for — record the extra as a sale instead.")

            payment_id = self._payments.insert(
                {"customer_id": customer_id, "direction": "in", "amount": amount,
                 "method": method, "reference_type": None, "reference_id": None,
                 "user_id": actor.id, "paid_at": datetime.now(),
                 "note": (note or "").strip() or None}, conn)
            self._audit.record(actor.id, "customer.collect_due", "customers",
                               customer_id, {"amount": str(amount), "method": method}, conn)
        log.info("Collected %s from %s", amount, customer["name"])
        return payment_id

    def deactivate(self, customer_id: int) -> None:
        actor = self._require()
        with transaction() as conn:
            existing = self._repo.get(customer_id, conn)
            if existing is None:
                raise ValidationError("That customer no longer exists.")
            if existing["is_walk_in"]:
                raise ValidationError(
                    "The walk-in customer cannot be disabled. Counter sales "
                    "without a named buyer have nowhere else to go.")
            self._repo.set_active(customer_id, False, conn)
            self._audit.record(actor.id, "customer.deactivate", "customers",
                               customer_id, conn=conn)

    def activate(self, customer_id: int) -> None:
        actor = self._require()
        with transaction() as conn:
            if self._repo.get(customer_id, conn) is None:
                raise ValidationError("That customer no longer exists.")
            self._repo.set_active(customer_id, True, conn)
            self._audit.record(actor.id, "customer.activate", "customers",
                               customer_id, conn=conn)

    def delete(self, customer_id: int) -> None:
        actor = self._require()
        with transaction() as conn:
            existing = self._repo.get(customer_id, conn)
            if existing is None:
                raise ValidationError("That customer no longer exists.")
            if existing["is_walk_in"]:
                raise ValidationError("The walk-in customer cannot be deleted.")
            if self._repo.has_activity(customer_id, conn):
                raise ValidationError(
                    f"{existing['name']} has sales or payments on record and "
                    "cannot be deleted. Disable the account instead.")
            self._audit.record(actor.id, "customer.delete", "customers", None,
                               {"name": existing["name"]}, conn)
            self._repo.delete(customer_id, conn)

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
        from decimal import InvalidOperation
        try:
            amount = Decimal(str(value).strip() or "0")
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError(f"{label} must be a number, got {value!r}.") from exc
        if amount < 0 and not allow_negative:
            raise ValidationError(f"{label} cannot be negative.")
        return amount.quantize(ZERO)
