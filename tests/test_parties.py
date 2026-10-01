"""M6 tests: customers, suppliers and their money ledgers.

Acceptance criteria from docs/PLAN.md:
  * the walk-in customer cannot be deleted or renamed away
  * a due balance recomputed from payments matches the displayed figure to the paisa
  * collecting more than the outstanding due is refused
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from smartpos.core.constants import Role
from smartpos.core.exceptions import DatabaseError, PermissionDenied, ValidationError
from smartpos.db import connection
from smartpos.services.auth_service import AuthService
from smartpos.services.customer_service import CustomerService
from smartpos.services.supplier_service import SupplierService

MARK = "ZZPARTY "


@pytest.fixture(scope="module")
def db():
    try:
        connection.init_pool()
        connection.check_connection()
    except DatabaseError as exc:
        pytest.skip(f"MySQL not reachable: {exc}")
    yield
    connection.close_pool()


def _purge() -> None:
    with connection.transaction() as conn:
        cur = conn.cursor()
        for sql in (
            "DELETE FROM sale_returns WHERE sale_id IN (SELECT id FROM sales WHERE "
            " customer_id IN (SELECT id FROM customers WHERE name LIKE %s))",
            "DELETE FROM customer_payments WHERE customer_id IN "
            " (SELECT id FROM customers WHERE name LIKE %s)",
            "DELETE FROM sales WHERE customer_id IN "
            " (SELECT id FROM customers WHERE name LIKE %s)",
            "DELETE FROM supplier_payments WHERE supplier_id IN "
            " (SELECT id FROM suppliers WHERE name LIKE %s)",
            "DELETE FROM purchases WHERE supplier_id IN "
            " (SELECT id FROM suppliers WHERE name LIKE %s)",
            "DELETE FROM customers WHERE name LIKE %s AND is_walk_in = 0",
            "DELETE FROM suppliers WHERE name LIKE %s",
        ):
            cur.execute(sql, (MARK + "%",))
        cur.execute("DELETE FROM users WHERE username LIKE 'zz_%'")
        cur.close()


@pytest.fixture
def services(db):
    _purge()
    auth = AuthService()
    auth.login("admin", "admin123")
    yield CustomerService(auth), SupplierService(auth)
    _purge()


# --- helpers that stand in for M8/M9, which do not exist yet ---------------

def _record_sale(customer_id: int, total: str, invoice: str) -> int:
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sales (invoice_no, customer_id, warehouse_id, sale_date, "
            "subtotal, grand_total, status) VALUES (%s,%s,1,%s,%s,%s,'completed')",
            (invoice, customer_id, datetime.now(), total, total))
        sale_id = cur.lastrowid
        cur.close()
    return sale_id


def _record_return(sale_id: int, total: str, reference: str) -> None:
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sale_returns (reference_no, sale_id, warehouse_id, "
            "return_date, total_amount) VALUES (%s,%s,1,%s,%s)",
            (reference, sale_id, datetime.now(), total))
        cur.close()


def _record_refund(customer_id: int, amount: str) -> None:
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO customer_payments (customer_id, direction, amount, method, "
            "paid_at) VALUES (%s,'out',%s,'cash',%s)",
            (customer_id, amount, datetime.now()))
        cur.close()


def _record_purchase(supplier_id: int, total: str, reference: str) -> None:
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO purchases (reference_no, supplier_id, warehouse_id, "
            "purchase_date, subtotal, grand_total) VALUES (%s,%s,1,%s,%s,%s)",
            (reference, supplier_id, datetime.now(), total, total))
        cur.close()


# ------------------------------------------------------------- the walk-in

def test_the_walk_in_customer_exists(services):
    customers, _ = services
    walk_in = customers.get_walk_in()
    assert walk_in.is_walk_in and walk_in.is_active


def test_the_walk_in_customer_cannot_be_renamed(services):
    """Acceptance: it cannot be renamed away."""
    customers, _ = services
    walk_in = customers.get_walk_in()
    with pytest.raises(ValidationError, match="cannot be renamed"):
        customers.update(walk_in.id, name=f"{MARK}Renamed")


def test_the_walk_in_customer_cannot_be_deleted(services):
    """Acceptance: it cannot be deleted."""
    customers, _ = services
    walk_in = customers.get_walk_in()
    with pytest.raises(ValidationError, match="cannot be deleted"):
        customers.delete(walk_in.id)


def test_the_walk_in_customer_cannot_be_disabled(services):
    customers, _ = services
    walk_in = customers.get_walk_in()
    with pytest.raises(ValidationError, match="cannot be disabled"):
        customers.deactivate(walk_in.id)


def test_other_fields_of_the_walk_in_may_still_be_edited(services):
    """Only the name is protected; a shop may want a phone on it."""
    customers, _ = services
    walk_in = customers.get_walk_in()
    customers.update(walk_in.id, address="Counter")
    assert customers.get(walk_in.id).address == "Counter"
    customers.update(walk_in.id, address="")


# ----------------------------------------------------------- customer CRUD

def test_create_and_read_back(services):
    customers, _ = services
    cid = customers.create(f"{MARK}Rahim", phone="01711000000",
                           address="Mirpur", opening_balance="500.00")
    c = customers.get(cid)
    assert c.name == f"{MARK}Rahim"
    assert c.opening_balance == Decimal("500.00")
    assert not c.is_walk_in


def test_duplicate_customer_name_is_refused(services):
    customers, _ = services
    customers.create(f"{MARK}Karim")
    with pytest.raises(ValidationError, match="already exists"):
        customers.create(f"{MARK}Karim")


def test_search_matches_name_and_phone(services):
    customers, _ = services
    customers.create(f"{MARK}Searchable", phone="01799887766")
    assert customers.search("Searchable")[0].phone == "01799887766"
    assert customers.search("01799887766")[0].name == f"{MARK}Searchable"


def test_a_customer_with_no_history_can_be_deleted(services):
    customers, _ = services
    cid = customers.create(f"{MARK}Temporary")
    customers.delete(cid)
    assert customers.get(cid) is None


def test_a_customer_with_sales_cannot_be_deleted(services):
    customers, _ = services
    cid = customers.create(f"{MARK}HasSales")
    _record_sale(cid, "100.00", f"{MARK}INV1")
    with pytest.raises(ValidationError, match="cannot be deleted"):
        customers.delete(cid)


# ------------------------------------------------------- customer balances

def test_opening_balance_alone(services):
    customers, _ = services
    cid = customers.create(f"{MARK}Opening", opening_balance="250.50")
    assert customers.due_balance(cid) == Decimal("250.50")


def test_a_credit_sale_increases_the_due(services):
    customers, _ = services
    cid = customers.create(f"{MARK}Credit")
    _record_sale(cid, "1000.00", f"{MARK}INV2")
    assert customers.due_balance(cid) == Decimal("1000.00")


def test_a_collection_reduces_the_due(services):
    customers, _ = services
    cid = customers.create(f"{MARK}Paying")
    _record_sale(cid, "1000.00", f"{MARK}INV3")
    customers.collect_due(cid, "400.00")
    assert customers.due_balance(cid) == Decimal("600.00")


def test_a_return_reduces_the_due(services):
    customers, _ = services
    cid = customers.create(f"{MARK}Returning")
    sale = _record_sale(cid, "1000.00", f"{MARK}INV4")
    _record_return(sale, "300.00", f"{MARK}RET1")
    assert customers.due_balance(cid) == Decimal("700.00")


def test_a_cash_refund_on_a_paid_sale_nets_to_zero(services):
    """The case that makes the `+ refunded` term necessary.

    Sale paid in full, then part returned and the cash handed back: the
    customer owes nothing, and the shop owes nothing either.
    """
    customers, _ = services
    cid = customers.create(f"{MARK}Refunded")
    sale = _record_sale(cid, "1000.00", f"{MARK}INV5")
    customers.collect_due(cid, "1000.00")
    assert customers.due_balance(cid) == Decimal("0.00")

    _record_return(sale, "300.00", f"{MARK}RET2")
    assert customers.due_balance(cid) == Decimal("-300.00"), "shop owes the customer"

    _record_refund(cid, "300.00")
    assert customers.due_balance(cid) == Decimal("0.00"), "settled by handing cash back"


def test_the_balance_reconciles_to_the_paisa(services):
    """Acceptance: a due recomputed from the parts matches the figure shown."""
    customers, _ = services
    cid = customers.create(f"{MARK}Reconcile", opening_balance="123.45")
    s1 = _record_sale(cid, "999.99", f"{MARK}INV6")
    _record_sale(cid, "0.01", f"{MARK}INV7")
    customers.collect_due(cid, "500.55")
    _record_return(s1, "49.49", f"{MARK}RET3")
    _record_refund(cid, "10.10")

    parts = customers.balance_breakdown(cid)
    recomputed = (parts["opening"] + parts["sales"] - parts["returns"]
                  - parts["received"] + parts["refunded"])
    assert parts["due"] == recomputed
    assert parts["due"] == Decimal("123.45") + Decimal("1000.00") \
        - Decimal("49.49") - Decimal("500.55") + Decimal("10.10")
    assert parts["due"] == Decimal("583.51")


def test_held_and_cancelled_sales_are_not_debts(services):
    customers, _ = services
    cid = customers.create(f"{MARK}Held")
    with connection.transaction() as conn:
        cur = conn.cursor()
        for status, invoice in (("held", f"{MARK}INV8"), ("cancelled", f"{MARK}INV9")):
            cur.execute(
                "INSERT INTO sales (invoice_no, customer_id, warehouse_id, sale_date,"
                " subtotal, grand_total, status) VALUES (%s,%s,1,%s,500,500,%s)",
                (invoice, cid, datetime.now(), status))
        cur.close()
    assert customers.due_balance(cid) == Decimal("0.00")


def test_collecting_more_than_the_due_is_refused(services):
    """Acceptance criterion."""
    customers, _ = services
    cid = customers.create(f"{MARK}Overpay")
    _record_sale(cid, "500.00", f"{MARK}INV10")
    with pytest.raises(ValidationError, match="owes 500.00"):
        customers.collect_due(cid, "500.01")


def test_collecting_exactly_the_due_is_allowed(services):
    customers, _ = services
    cid = customers.create(f"{MARK}Exact")
    _record_sale(cid, "500.00", f"{MARK}INV11")
    customers.collect_due(cid, "500.00")
    assert customers.due_balance(cid) == Decimal("0.00")


def test_collecting_zero_or_negative_is_refused(services):
    customers, _ = services
    cid = customers.create(f"{MARK}Zero")
    _record_sale(cid, "100.00", f"{MARK}INV12")
    with pytest.raises(ValidationError, match="amount to collect"):
        customers.collect_due(cid, "0")
    with pytest.raises(ValidationError, match="cannot be negative"):
        customers.collect_due(cid, "-5")


def test_an_unknown_payment_method_is_refused(services):
    customers, _ = services
    cid = customers.create(f"{MARK}Method")
    _record_sale(cid, "100.00", f"{MARK}INV13")
    with pytest.raises(ValidationError, match="payment method"):
        customers.collect_due(cid, "50", method="barter")


def test_a_refused_collection_writes_nothing(services):
    customers, _ = services
    cid = customers.create(f"{MARK}Nothing")
    _record_sale(cid, "100.00", f"{MARK}INV14")
    with pytest.raises(ValidationError):
        customers.collect_due(cid, "999")
    assert customers.due_balance(cid) == Decimal("100.00")


def test_the_ledger_lists_every_movement(services):
    customers, _ = services
    cid = customers.create(f"{MARK}Ledger")
    sale = _record_sale(cid, "800.00", f"{MARK}INV15")
    _record_return(sale, "100.00", f"{MARK}RET4")
    customers.collect_due(cid, "200.00")
    kinds = [row["kind"] for row in customers.ledger(cid)]
    assert sorted(kinds) == ["Payment", "Return", "Sale"]


# --------------------------------------------------------------- suppliers

def test_supplier_create_and_search(services):
    _, suppliers = services
    sid = suppliers.create(f"{MARK}Wholesaler", phone="01811000000")
    assert suppliers.get(sid).name == f"{MARK}Wholesaler"
    assert suppliers.search("Wholesaler")[0].id == sid


def test_a_purchase_increases_the_payable(services):
    _, suppliers = services
    sid = suppliers.create(f"{MARK}Owed")
    _record_purchase(sid, "5000.00", f"{MARK}PUR1")
    assert suppliers.payable_balance(sid) == Decimal("5000.00")


def test_paying_reduces_the_payable(services):
    _, suppliers = services
    sid = suppliers.create(f"{MARK}Paid")
    _record_purchase(sid, "5000.00", f"{MARK}PUR2")
    suppliers.pay(sid, "2000.00", method="bank")
    assert suppliers.payable_balance(sid) == Decimal("3000.00")


def test_overpaying_a_supplier_is_refused(services):
    _, suppliers = services
    sid = suppliers.create(f"{MARK}NoOverpay")
    _record_purchase(sid, "1000.00", f"{MARK}PUR3")
    with pytest.raises(ValidationError, match="overpay by 500.00"):
        suppliers.pay(sid, "1500.00")


def test_the_supplier_balance_reconciles(services):
    _, suppliers = services
    sid = suppliers.create(f"{MARK}SupplierRecon", opening_balance="1000.00")
    _record_purchase(sid, "2500.75", f"{MARK}PUR4")
    suppliers.pay(sid, "1200.25")
    parts = suppliers.balance_breakdown(sid)
    assert parts["payable"] == parts["opening"] + parts["purchases"] \
        - parts["paid"] + parts["refunded"]
    assert parts["payable"] == Decimal("2300.50")


def test_a_supplier_with_purchases_cannot_be_deleted(services):
    _, suppliers = services
    sid = suppliers.create(f"{MARK}Busy")
    _record_purchase(sid, "10.00", f"{MARK}PUR5")
    with pytest.raises(ValidationError, match="cannot be deleted"):
        suppliers.delete(sid)


def test_supplier_deactivate_and_reactivate(services):
    _, suppliers = services
    sid = suppliers.create(f"{MARK}Toggle")
    suppliers.deactivate(sid)
    assert suppliers.get(sid).is_active is False
    suppliers.activate(sid)
    assert suppliers.get(sid).is_active is True


# ------------------------------------------------------------ authorisation

def test_reads_work_without_a_session(db):
    assert CustomerService().search("") is not None
    assert SupplierService().search("") is not None


def test_writes_need_a_signed_in_user(db):
    with pytest.raises(ValidationError, match="signed-in user"):
        CustomerService().create(f"{MARK}NoSession")
    with pytest.raises(ValidationError, match="signed-in user"):
        SupplierService().create(f"{MARK}NoSession")


def test_a_cashier_may_look_but_not_edit(services):
    customers, suppliers = services
    admin = AuthService()
    admin.login("admin", "admin123")
    admin.create_user("zz_party_cashier", "Counter", "password1", Role.CASHIER)
    cashier = AuthService()
    cashier.login("zz_party_cashier", "password1")

    assert CustomerService(cashier).search("") is not None
    with pytest.raises(PermissionDenied):
        CustomerService(cashier).create(f"{MARK}Nope")
    with pytest.raises(PermissionDenied):
        SupplierService(cashier).create(f"{MARK}Nope")
