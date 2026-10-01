"""Supplier master, purchase history and payments made."""

from __future__ import annotations

from decimal import Decimal

from smartpos.services.auth_service import AuthService
from smartpos.services.supplier_service import SupplierService
from smartpos.ui.views._party_view import PartyView


class SupplierView(PartyView):
    """Supplier master, purchase history and payments made."""

    noun = "supplier"
    plural = "Suppliers"
    blurb = ("What the shop owes. A positive payable is money due out; it "
             "grows with every purchase and falls with every payment.")
    balance_heading = "Payable"
    payment_verb = "Pay supplier"
    payment_prompt = "Shop owes"
    manage_permission = "supplier.manage"

    def __init__(self, auth: AuthService, parent=None) -> None:
        self._svc = SupplierService(auth)
        super().__init__(auth, parent)

    def _service(self) -> SupplierService:
        return self._svc

    def _balance_of(self, party_id: int) -> Decimal:
        return self._svc.payable_balance(party_id)

    def _record_payment(self, party_id: int, values: dict) -> None:
        self._svc.pay(party_id, values["amount"], values["method"], values["note"])
