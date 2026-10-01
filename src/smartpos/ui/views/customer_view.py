"""Customer master, purchase history and due collection."""

from __future__ import annotations

from decimal import Decimal

from smartpos.services.auth_service import AuthService
from smartpos.services.customer_service import CustomerService
from smartpos.ui.views._party_view import PartyView


class CustomerView(PartyView):
    """Customer master, purchase history and due collection."""

    noun = "customer"
    plural = "Customers"
    blurb = ("Who owes what. A positive due is money the shop is waiting on; "
             "a negative one means the shop owes them, usually after a return.")
    balance_heading = "Due"
    payment_verb = "Collect due"
    payment_prompt = "Currently owes"
    manage_permission = "customer.manage"

    def __init__(self, auth: AuthService, parent=None) -> None:
        self._svc = CustomerService(auth)
        super().__init__(auth, parent)

    def _service(self) -> CustomerService:
        return self._svc

    def _balance_of(self, party_id: int) -> Decimal:
        return self._svc.due_balance(party_id)

    def _record_payment(self, party_id: int, values: dict) -> None:
        self._svc.collect_due(party_id, values["amount"],
                              values["method"], values["note"])

    def _is_protected(self, party) -> bool:
        """The walk-in row anchors every counter sale without a named buyer."""
        return bool(getattr(party, "is_walk_in", False))
