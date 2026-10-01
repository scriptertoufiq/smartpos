"""M10 tests: returns against an existing sale.

Acceptance criteria from docs/PLAN.md:
  * returning more than was sold is refused, counting earlier returns
  * the returned quantity reappears in the warehouse the sale left from
  * sales reports show sales net of returns
  * two partial returns against one invoice both work and cannot together
    exceed the sale
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from smartpos.core.constants import Role
from smartpos.core.exceptions import DatabaseError, PermissionDenied, ValidationError
from smartpos.db import connection
from smartpos.services.auth_service import AuthService
from smartpos.services.customer_service import CustomerService
from smartpos.services.product_service import ProductService
from smartpos.services.purchase_service import PurchaseDraft, PurchaseLine, PurchaseService
from smartpos.services.sale_return_service import (
    REFUND_CASH,
    REFUND_DUE,
    ReturnDraft,
    ReturnLine,
    SaleReturnService,
)
from smartpos.services.sale_service import SaleDraft, SaleLine, SaleService
from smartpos.services.stock_service import StockService
from smartpos.services.supplier_service import SupplierService
from smartpos.services.warehouse_service import WarehouseService

MARK = "ZZRET"


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
    mine = ("SELECT * FROM (SELECT DISTINCT i.sale_id FROM sale_items i "
            "JOIN products p ON p.id = i.product_id WHERE p.sku LIKE %s) AS t")
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM sale_return_items WHERE sale_return_id IN "
                    f"(SELECT * FROM (SELECT id FROM sale_returns WHERE sale_id IN "
                    f"({mine})) AS r)", (MARK + "%",))
        cur.execute(f"DELETE FROM customer_payments WHERE reference_type = 'sale_return' "
                    f"AND reference_id NOT IN (SELECT id FROM sale_returns)")
        cur.execute(f"DELETE FROM sale_returns WHERE sale_id IN ({mine})", (MARK + "%",))
        cur.execute(f"DELETE FROM customer_payments WHERE reference_type IN "
                    f"('sale','sale_return') AND reference_id IN ({mine})", (MARK + "%",))
        cur.execute(f"DELETE FROM sales WHERE id IN ({mine})", (MARK + "%",))
        for sql in (
            "DELETE FROM sale_items WHERE product_id IN "
            " (SELECT id FROM products WHERE sku LIKE %s)",
            "DELETE FROM purchase_items WHERE product_id IN "
            " (SELECT id FROM products WHERE sku LIKE %s)",
            "DELETE FROM purchases WHERE supplier_id IN "
            " (SELECT id FROM suppliers WHERE name LIKE %s)",
            "DELETE FROM supplier_payments WHERE supplier_id IN "
            " (SELECT id FROM suppliers WHERE name LIKE %s)",
            "DELETE FROM stock_movements WHERE product_id IN "
            " (SELECT id FROM products WHERE sku LIKE %s)",
            "DELETE FROM stock WHERE product_id IN "
            " (SELECT id FROM products WHERE sku LIKE %s)",
            "DELETE FROM products WHERE sku LIKE %s",
            "DELETE FROM suppliers WHERE name LIKE %s",
            # Refund rows point at a return, not a sale, so they are only
            # reachable through the customer.
            "DELETE FROM customer_payments WHERE customer_id IN "
            " (SELECT id FROM customers WHERE name LIKE %s)",
            "DELETE FROM customers WHERE name LIKE %s AND is_walk_in = 0",
        ):
            cur.execute(sql, (MARK + "%",))
        cur.execute("DELETE FROM users WHERE username LIKE 'zz_%'")
        cur.close()


@pytest.fixture
def ctx(db):
    _purge()
    auth = AuthService()
    auth.login("admin", "admin123")
    products = ProductService(auth)
    sales = SaleService(auth)
    returns = SaleReturnService(auth)
    stock = StockService(auth)
    customers = CustomerService(auth)

    warehouse = next(w for w in WarehouseService(auth).list_all()
                     if w.code == "MAIN").id
    supplier = SupplierService(auth).create(f"{MARK} Supplier")
    rice = products.create(f"{MARK}-RICE", "Rice 5kg", sale_price="420.00")
    oil = products.create(f"{MARK}-OIL", "Oil 1L", sale_price="175.00")
    PurchaseService(auth).create(PurchaseDraft(
        supplier_id=supplier, warehouse_id=warehouse,
        lines=[PurchaseLine(rice, Decimal("100"), Decimal("395.50")),
               PurchaseLine(oil, Decimal("50"), Decimal("160.00"))]))

    yield {"auth": auth, "sales": sales, "returns": returns, "stock": stock,
           "products": products, "customers": customers,
           "warehouse": warehouse, "rice": rice, "oil": oil}
    _purge()


def _sell(ctx, rice_qty="10", oil_qty=None, tendered=None, customer_id=None):
    lines = [SaleLine(ctx["rice"], Decimal(rice_qty), Decimal("420.00"))]
    if oil_qty:
        lines.append(SaleLine(ctx["oil"], Decimal(oil_qty), Decimal("175.00")))
    total = sum(line.quantity * line.unit_price for line in lines)
    draft = SaleDraft(warehouse_id=ctx["warehouse"], customer_id=customer_id,
                      lines=lines,
                      tendered=Decimal(tendered) if tendered else total)
    return ctx["sales"].create(draft)


def _no_drift(ctx) -> None:
    assert ctx["stock"].find_drift() == [], "ledger and levels disagree"


# ------------------------------------------------------- returnable amounts

def test_returnable_shows_the_full_quantity_at_first(ctx):
    sale_id = _sell(ctx, "10")
    line = ctx["returns"].returnable_quantities(sale_id)[0]
    assert line.sold == Decimal("10.000")
    assert line.already_returned == Decimal("0.000")
    assert line.returnable == Decimal("10.000")


def test_returnable_falls_as_returns_are_made(ctx):
    returns = ctx["returns"]
    sale_id = _sell(ctx, "10")
    item_id = returns.returnable_quantities(sale_id)[0].sale_item_id
    returns.create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("4"))]))
    line = returns.returnable_quantities(sale_id)[0]
    assert line.already_returned == Decimal("4.000")
    assert line.returnable == Decimal("6.000")


def test_a_held_sale_cannot_be_returned_against(ctx):
    draft = SaleDraft(warehouse_id=ctx["warehouse"],
                      lines=[SaleLine(ctx["rice"], Decimal("1"), Decimal("420"))])
    held = ctx["sales"].hold(draft)
    with pytest.raises(ValidationError, match="only a completed sale"):
        ctx["returns"].returnable_quantities(held)


# -------------------------------------------------------------- stock effect

def test_the_returned_quantity_goes_back_to_the_selling_warehouse(ctx):
    """Acceptance criterion."""
    stock, rice, w = ctx["stock"], ctx["rice"], ctx["warehouse"]
    sale_id = _sell(ctx, "10")
    assert stock.quantity_on_hand(rice, w) == Decimal("90.000")

    item_id = ctx["returns"].returnable_quantities(sale_id)[0].sale_item_id
    ctx["returns"].create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("3"))]))

    assert stock.quantity_on_hand(rice, w) == Decimal("93.000")
    _no_drift(ctx)


def test_the_movement_names_the_return(ctx):
    sale_id = _sell(ctx, "5")
    item_id = ctx["returns"].returnable_quantities(sale_id)[0].sale_item_id
    return_id = ctx["returns"].create(
        ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("2"))]))
    movements = ctx["stock"].movements_for("sale_return", return_id)
    assert len(movements) == 1
    assert movements[0]["movement_type"] == "sale_return"
    assert movements[0]["quantity"] == Decimal("2.000")


def test_a_return_can_be_taken_into_a_different_outlet(ctx):
    godown = WarehouseService(ctx["auth"]).create(f"{MARK}G", "Return Godown")
    try:
        sale_id = _sell(ctx, "5")
        item_id = ctx["returns"].returnable_quantities(sale_id)[0].sale_item_id
        ctx["returns"].create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("2"))],
                                          warehouse_id=godown))
        assert ctx["stock"].quantity_on_hand(ctx["rice"], godown) == Decimal("2.000")
        _no_drift(ctx)
    finally:
        with connection.transaction() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM stock_movements WHERE warehouse_id = %s", (godown,))
            cur.execute("DELETE FROM stock WHERE warehouse_id = %s", (godown,))
            cur.execute("UPDATE sale_returns SET warehouse_id = %s WHERE warehouse_id = %s",
                        (ctx["warehouse"], godown))
            cur.execute("DELETE FROM warehouses WHERE id = %s", (godown,))
            cur.close()


# ------------------------------------------------------ the quantity guard

def test_returning_more_than_was_sold_is_refused(ctx):
    """Acceptance criterion."""
    sale_id = _sell(ctx, "5")
    item_id = ctx["returns"].returnable_quantities(sale_id)[0].sale_item_id
    with pytest.raises(ValidationError, match="5 left to return"):
        ctx["returns"].create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("6"))]))


def test_earlier_returns_count_against_the_limit(ctx):
    """Acceptance: the refusal counts what has already gone back."""
    returns = ctx["returns"]
    sale_id = _sell(ctx, "10")
    item_id = returns.returnable_quantities(sale_id)[0].sale_item_id
    returns.create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("7"))]))
    with pytest.raises(ValidationError, match="3 left to return"):
        returns.create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("4"))]))


def test_two_partial_returns_both_work_and_cannot_exceed_the_sale(ctx):
    """Acceptance criterion, in full."""
    returns, stock = ctx["returns"], ctx["stock"]
    sale_id = _sell(ctx, "10")
    item_id = returns.returnable_quantities(sale_id)[0].sale_item_id

    returns.create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("6"))]))
    returns.create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("4"))]))
    assert returns.returnable_quantities(sale_id)[0].returnable == Decimal("0.000")

    with pytest.raises(ValidationError, match="0 left to return"):
        returns.create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("1"))]))

    # Everything sold has come back, and nothing more.
    assert stock.quantity_on_hand(ctx["rice"], ctx["warehouse"]) == Decimal("100.000")
    _no_drift(ctx)


def test_a_line_from_another_invoice_is_refused(ctx):
    returns = ctx["returns"]
    first = _sell(ctx, "5")
    second = _sell(ctx, "5")
    foreign_item = returns.returnable_quantities(second)[0].sale_item_id
    with pytest.raises(ValidationError, match="does not belong"):
        returns.create(ReturnDraft(first, [ReturnLine(foreign_item, Decimal("1"))]))


def test_the_same_line_twice_in_one_return_is_refused(ctx):
    sale_id = _sell(ctx, "5")
    item_id = ctx["returns"].returnable_quantities(sale_id)[0].sale_item_id
    with pytest.raises(ValidationError, match="appears twice"):
        ctx["returns"].create(ReturnDraft(sale_id, [
            ReturnLine(item_id, Decimal("1")), ReturnLine(item_id, Decimal("2"))]))


def test_a_return_with_no_lines_is_refused(ctx):
    sale_id = _sell(ctx, "5")
    with pytest.raises(ValidationError, match="at least one line"):
        ctx["returns"].create(ReturnDraft(sale_id, []))


def test_a_refused_return_writes_nothing(ctx):
    returns, stock = ctx["returns"], ctx["stock"]
    sale_id = _sell(ctx, "5")
    item_id = returns.returnable_quantities(sale_id)[0].sale_item_id
    before = stock.quantity_on_hand(ctx["rice"], ctx["warehouse"])

    with pytest.raises(ValidationError):
        returns.create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("99"))]))

    assert stock.quantity_on_hand(ctx["rice"], ctx["warehouse"]) == before
    assert returns.list(sale_id=sale_id) == []
    _no_drift(ctx)


def test_a_partly_failing_multi_line_return_writes_nothing(ctx):
    """The second line is over the limit; the first must not have gone back."""
    returns, stock = ctx["returns"], ctx["stock"]
    sale_id = _sell(ctx, "5", oil_qty="2")
    lines = {l.product_id: l.sale_item_id
             for l in returns.returnable_quantities(sale_id)}
    before_rice = stock.quantity_on_hand(ctx["rice"], ctx["warehouse"])
    before_oil = stock.quantity_on_hand(ctx["oil"], ctx["warehouse"])

    with pytest.raises(ValidationError):
        returns.create(ReturnDraft(sale_id, [
            ReturnLine(lines[ctx["rice"]], Decimal("2")),
            ReturnLine(lines[ctx["oil"]], Decimal("99"))]))

    assert stock.quantity_on_hand(ctx["rice"], ctx["warehouse"]) == before_rice
    assert stock.quantity_on_hand(ctx["oil"], ctx["warehouse"]) == before_oil
    _no_drift(ctx)


# ----------------------------------------------------------------- the money

def test_a_cash_refund_settles_the_customer_to_zero(ctx):
    """Sale paid in full, part returned, cash handed back: nobody owes anybody."""
    customers, returns = ctx["customers"], ctx["returns"]
    cid = customers.create(f"{MARK} Rahim")
    sale_id = _sell(ctx, "10", customer_id=cid)          # 4200, paid in full
    assert customers.due_balance(cid) == Decimal("0.00")

    item_id = returns.returnable_quantities(sale_id)[0].sale_item_id
    returns.create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("2"))],
                               refund_method=REFUND_CASH))
    assert customers.due_balance(cid) == Decimal("0.00"), "cash back settles it"


def test_a_due_adjustment_reduces_what_the_customer_owes(ctx):
    customers, returns = ctx["customers"], ctx["returns"]
    cid = customers.create(f"{MARK} Karim")
    sale_id = _sell(ctx, "10", tendered="0", customer_id=cid)   # 4200 on credit
    assert customers.due_balance(cid) == Decimal("4200.00")

    item_id = returns.returnable_quantities(sale_id)[0].sale_item_id
    returns.create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("2"))],
                               refund_method=REFUND_DUE))
    assert customers.due_balance(cid) == Decimal("3360.00")     # 4200 - 840


def test_the_return_total_is_the_sold_price_not_todays(ctx):
    returns, products = ctx["returns"], ctx["products"]
    sale_id = _sell(ctx, "4")
    products.update(ctx["rice"], sale_price="999.00")
    item_id = returns.returnable_quantities(sale_id)[0].sale_item_id
    return_id = returns.create(
        ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("2"))]))
    assert Decimal(str(returns.get(return_id)["total_amount"])) == Decimal("840.00")


def test_an_unknown_refund_method_is_refused(ctx):
    sale_id = _sell(ctx, "5")
    item_id = ctx["returns"].returnable_quantities(sale_id)[0].sale_item_id
    with pytest.raises(ValidationError, match="refund method"):
        ctx["returns"].create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("1"))],
                                          refund_method="barter"))


# ------------------------------------------------------------- bookkeeping

def test_the_reference_is_sequential_and_gapless(ctx):
    returns = ctx["returns"]
    sale_id = _sell(ctx, "10")
    item_id = returns.returnable_quantities(sale_id)[0].sale_item_id
    first = returns.get(returns.create(
        ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("1"))])))["reference_no"]
    second = returns.get(returns.create(
        ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("1"))])))["reference_no"]
    assert first.startswith("RET-")
    assert int(second.split("-")[-1]) == int(first.split("-")[-1]) + 1


def test_a_failed_return_gives_its_reference_back(ctx):
    returns = ctx["returns"]
    sale_id = _sell(ctx, "5")
    item_id = returns.returnable_quantities(sale_id)[0].sale_item_id
    expected = returns.next_reference_no()
    with pytest.raises(ValidationError):
        returns.create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("99"))]))
    assert returns.next_reference_no() == expected


def test_sales_are_reported_net_of_returns(ctx):
    """Acceptance: the figures a report would use."""
    returns = ctx["returns"]
    sale_id = _sell(ctx, "10")                       # 4200 billed
    item_id = returns.returnable_quantities(sale_id)[0].sale_item_id
    returns.create(ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("3"))]))

    billed = Decimal(str(ctx["sales"].get(sale_id)["grand_total"]))
    returned = returns.total_returned(sale_id)
    assert billed == Decimal("4200.00")
    assert returned == Decimal("1260.00")
    assert billed - returned == Decimal("2940.00")


def test_the_return_lists_its_lines(ctx):
    returns = ctx["returns"]
    sale_id = _sell(ctx, "5", oil_qty="3")
    lines = {l.product_id: l.sale_item_id
             for l in returns.returnable_quantities(sale_id)}
    return_id = returns.create(ReturnDraft(sale_id, [
        ReturnLine(lines[ctx["rice"]], Decimal("1")),
        ReturnLine(lines[ctx["oil"]], Decimal("2"))]))
    items = returns.items_of(return_id)
    assert len(items) == 2
    assert sum(Decimal(str(i["line_total"])) for i in items) == Decimal("770.00")


# ------------------------------------------------------------ authorisation

def test_returns_need_a_signed_in_user(db):
    with pytest.raises(ValidationError, match="signed-in user"):
        SaleReturnService().create(ReturnDraft(1, [ReturnLine(1, Decimal("1"))]))


def test_a_cashier_cannot_process_a_return(ctx):
    """Returns are a manager's job: they move money out of the till."""
    ctx["auth"].create_user("zz_ret_till", "Till", "password1", Role.CASHIER)
    cashier = AuthService()
    cashier.login("zz_ret_till", "password1")
    sale_id = _sell(ctx, "5")
    item_id = ctx["returns"].returnable_quantities(sale_id)[0].sale_item_id
    with pytest.raises(PermissionDenied):
        SaleReturnService(cashier).create(
            ReturnDraft(sale_id, [ReturnLine(item_id, Decimal("1"))]))
