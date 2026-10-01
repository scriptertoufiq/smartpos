"""M8 tests: recording purchases.

Acceptance criteria from docs/PLAN.md:
  * a confirmed purchase raises stock by exactly the quantity received
  * totals reconcile: sum(line_total) - discount + tax == grand_total
  * payment_status follows paid_amount: paid / partial / due
  * a failure mid-save leaves no purchase row and no stock change
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from smartpos.core.constants import Role
from smartpos.core.exceptions import (
    DatabaseError,
    InsufficientStock,
    PermissionDenied,
    ValidationError,
)
from smartpos.db import connection
from smartpos.services.auth_service import AuthService
from smartpos.services.product_service import ProductService
from smartpos.services.purchase_service import PurchaseDraft, PurchaseLine, PurchaseService
from smartpos.services.stock_service import StockService
from smartpos.services.supplier_service import SupplierService
from smartpos.services.warehouse_service import WarehouseService

MARK = "ZZPUR"


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
        cur.execute("DELETE FROM supplier_payments WHERE reference_type = 'purchase' "
                    "AND reference_id IN (SELECT id FROM purchases WHERE reference_no "
                    "LIKE 'PUR-%')")
        cur.execute("DELETE FROM supplier_payments WHERE supplier_id IN "
                    "(SELECT id FROM suppliers WHERE name LIKE %s)", (MARK + "%",))
        cur.execute("DELETE FROM purchase_items WHERE product_id IN "
                    "(SELECT id FROM products WHERE sku LIKE %s)", (MARK + "%",))
        cur.execute("DELETE FROM purchases WHERE supplier_id IN "
                    "(SELECT id FROM suppliers WHERE name LIKE %s)", (MARK + "%",))
        cur.execute("DELETE FROM stock_movements WHERE product_id IN "
                    "(SELECT id FROM products WHERE sku LIKE %s)", (MARK + "%",))
        cur.execute("DELETE FROM stock WHERE product_id IN "
                    "(SELECT id FROM products WHERE sku LIKE %s)", (MARK + "%",))
        cur.execute("DELETE FROM products WHERE sku LIKE %s", (MARK + "%",))
        cur.execute("DELETE FROM suppliers WHERE name LIKE %s", (MARK + "%",))
        cur.execute("DELETE FROM users WHERE username LIKE 'zz_%'")
        cur.close()


@pytest.fixture
def ctx(db):
    _purge()
    auth = AuthService()
    auth.login("admin", "admin123")
    products = ProductService(auth)
    purchases = PurchaseService(auth)
    stock = StockService(auth)

    supplier = SupplierService(auth).create(f"{MARK} Wholesaler")
    warehouse = next(w for w in WarehouseService(auth).list_all()
                     if w.code == "MAIN").id
    rice = products.create(f"{MARK}-RICE", "Rice 5kg", purchase_price="0")
    oil = products.create(f"{MARK}-OIL", "Oil 1L", purchase_price="0")

    yield {"auth": auth, "purchases": purchases, "products": products,
           "stock": stock, "supplier": supplier, "warehouse": warehouse,
           "rice": rice, "oil": oil}
    _purge()


def _draft(ctx, **overrides) -> PurchaseDraft:
    draft = PurchaseDraft(
        supplier_id=ctx["supplier"], warehouse_id=ctx["warehouse"],
        lines=[PurchaseLine(ctx["rice"], Decimal("10"), Decimal("395.50"))])
    for key, value in overrides.items():
        setattr(draft, key, value)
    return draft


# ------------------------------------------------------------- stock effect

def test_a_purchase_raises_stock_by_exactly_what_was_received(ctx):
    """Acceptance criterion."""
    stock, rice, w = ctx["stock"], ctx["rice"], ctx["warehouse"]
    assert stock.quantity_on_hand(rice, w) == Decimal("0.000")

    ctx["purchases"].create(_draft(ctx))

    assert stock.quantity_on_hand(rice, w) == Decimal("10.000")
    assert stock.find_drift() == []


def test_the_movement_points_back_at_the_purchase(ctx):
    purchase_id = ctx["purchases"].create(_draft(ctx))
    movements = ctx["stock"].movements_for("purchase", purchase_id)
    assert len(movements) == 1
    assert movements[0]["movement_type"] == "purchase"
    assert movements[0]["quantity"] == Decimal("10.000")


def test_a_second_purchase_adds_to_the_level(ctx):
    ctx["purchases"].create(_draft(ctx))
    ctx["purchases"].create(_draft(ctx))
    assert ctx["stock"].quantity_on_hand(ctx["rice"], ctx["warehouse"]) == Decimal("20.000")
    assert ctx["stock"].find_drift() == []


def test_stock_lands_in_the_chosen_warehouse_only(ctx):
    godown = WarehouseService(ctx["auth"]).create(f"{MARK}G", "Purchase Godown")
    try:
        ctx["purchases"].create(_draft(ctx, warehouse_id=godown))
        assert ctx["stock"].quantity_on_hand(ctx["rice"], godown) == Decimal("10.000")
        assert ctx["stock"].quantity_on_hand(ctx["rice"], ctx["warehouse"]) == Decimal("0.000")
    finally:
        with connection.transaction() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM stock_movements WHERE warehouse_id = %s", (godown,))
            cur.execute("DELETE FROM stock WHERE warehouse_id = %s", (godown,))
            cur.execute("DELETE FROM purchases WHERE warehouse_id = %s", (godown,))
            cur.execute("DELETE FROM warehouses WHERE id = %s", (godown,))
            cur.close()


# ----------------------------------------------------------------- totals

def test_totals_reconcile(ctx):
    """Acceptance: sum(line_total) - discount + tax == grand_total."""
    draft = _draft(ctx, discount=Decimal("100.00"), tax_amount=Decimal("250.75"))
    draft.lines = [
        PurchaseLine(ctx["rice"], Decimal("10"), Decimal("395.50")),
        PurchaseLine(ctx["oil"], Decimal("6"), Decimal("160.25"),
                     discount=Decimal("10.50"), tax_amount=Decimal("5.25")),
    ]
    purchase_id = ctx["purchases"].create(draft)

    saved = ctx["purchases"].get(purchase_id)
    lines_total = sum(Decimal(str(i["line_total"])) for i in saved["items"])
    assert Decimal(str(saved["subtotal"])) == lines_total
    assert Decimal(str(saved["grand_total"])) == (
        lines_total - Decimal(str(saved["discount"])) + Decimal(str(saved["tax_amount"])))

    # And the arithmetic is what a person would do by hand:
    #   rice  10 x 395.50                        = 3955.00
    #   oil    6 x 160.25 - 10.50 + 5.25         =  956.25
    #   subtotal                                  = 4911.25
    #   less discount 100.00, plus tax 250.75     = 5062.00
    assert lines_total == Decimal("4911.25")
    assert Decimal(str(saved["grand_total"])) == Decimal("5062.00")


def test_a_line_total_includes_its_own_discount_and_tax(ctx):
    draft = _draft(ctx)
    draft.lines = [PurchaseLine(ctx["oil"], Decimal("4"), Decimal("100.00"),
                                discount=Decimal("25.00"), tax_amount=Decimal("15.00"))]
    purchase_id = ctx["purchases"].create(draft)
    item = ctx["purchases"].items_of(purchase_id)[0]
    assert Decimal(str(item["line_total"])) == Decimal("390.00")   # 400 - 25 + 15


def test_fractional_quantities_are_kept(ctx):
    draft = _draft(ctx)
    draft.lines = [PurchaseLine(ctx["rice"], Decimal("2.750"), Decimal("100.00"))]
    purchase_id = ctx["purchases"].create(draft)
    assert ctx["stock"].quantity_on_hand(ctx["rice"], ctx["warehouse"]) == Decimal("2.750")
    assert Decimal(str(ctx["purchases"].get(purchase_id)["grand_total"])) == Decimal("275.00")


def test_a_discount_larger_than_the_purchase_is_refused(ctx):
    with pytest.raises(ValidationError, match="more than the purchase"):
        ctx["purchases"].create(_draft(ctx, discount=Decimal("99999")))


def test_a_line_discount_larger_than_the_line_is_refused(ctx):
    draft = _draft(ctx)
    draft.lines = [PurchaseLine(ctx["rice"], Decimal("1"), Decimal("10.00"),
                                discount=Decimal("50.00"))]
    with pytest.raises(ValidationError, match="line discount"):
        ctx["purchases"].create(draft)


def test_a_purchase_with_no_lines_is_refused(ctx):
    with pytest.raises(ValidationError, match="at least one product"):
        ctx["purchases"].create(_draft(ctx, lines=[]))


def test_a_zero_quantity_line_is_refused(ctx):
    draft = _draft(ctx)
    draft.lines = [PurchaseLine(ctx["rice"], Decimal("0"), Decimal("10"))]
    with pytest.raises(ValidationError, match="more than zero"):
        ctx["purchases"].create(draft)


# ------------------------------------------------------------ payment status

def test_unpaid_is_due(ctx):
    purchase_id = ctx["purchases"].create(_draft(ctx))
    assert ctx["purchases"].get(purchase_id)["payment_status"] == "due"


def test_part_paid_is_partial(ctx):
    purchase_id = ctx["purchases"].create(_draft(ctx, paid_amount=Decimal("1000.00")))
    saved = ctx["purchases"].get(purchase_id)
    assert saved["payment_status"] == "partial"
    assert Decimal(str(saved["paid_amount"])) == Decimal("1000.00")


def test_fully_paid_is_paid(ctx):
    draft = _draft(ctx)
    draft.paid_amount = draft.grand_total
    purchase_id = ctx["purchases"].create(draft)
    assert ctx["purchases"].get(purchase_id)["payment_status"] == "paid"


def test_paying_more_than_the_total_is_refused(ctx):
    with pytest.raises(ValidationError, match="overpay"):
        ctx["purchases"].create(_draft(ctx, paid_amount=Decimal("999999")))


def test_a_later_payment_moves_the_status_along(ctx):
    purchases = ctx["purchases"]
    purchase_id = purchases.create(_draft(ctx))              # 3955.00, due
    purchases.record_payment(purchase_id, "1955.00")
    assert purchases.get(purchase_id)["payment_status"] == "partial"
    purchases.record_payment(purchase_id, "2000.00")
    saved = purchases.get(purchase_id)
    assert saved["payment_status"] == "paid"
    assert Decimal(str(saved["paid_amount"])) == Decimal("3955.00")


def test_overpaying_a_later_payment_is_refused(ctx):
    purchases = ctx["purchases"]
    purchase_id = purchases.create(_draft(ctx, paid_amount=Decimal("3000.00")))
    with pytest.raises(ValidationError, match="955.00 left to pay"):
        purchases.record_payment(purchase_id, "1000.00")


def test_the_cached_paid_amount_matches_the_payment_rows(ctx):
    """`purchases.paid_amount` is a cache; this proves it against its source."""
    purchases = ctx["purchases"]
    purchase_id = purchases.create(_draft(ctx, paid_amount=Decimal("500.00")))
    purchases.record_payment(purchase_id, "1200.50")
    purchases.record_payment(purchase_id, "254.50")

    cached = Decimal(str(purchases.get(purchase_id)["paid_amount"]))
    recomputed = purchases.recompute_paid(purchase_id)
    assert cached == recomputed == Decimal("1955.00")


def test_a_payment_reaches_the_supplier_ledger(ctx):
    purchases, auth = ctx["purchases"], ctx["auth"]
    suppliers = SupplierService(auth)
    purchase_id = purchases.create(_draft(ctx, paid_amount=Decimal("955.00")))
    # 3955 bought, 955 paid -> 3000 still payable
    assert suppliers.payable_balance(ctx["supplier"]) == Decimal("3000.00")
    purchases.record_payment(purchase_id, "3000.00")
    assert suppliers.payable_balance(ctx["supplier"]) == Decimal("0.00")


# ------------------------------------------------------------- atomicity

def test_a_failure_mid_save_leaves_nothing_behind(ctx):
    """Acceptance: no purchase row and no stock change.

    The second line names a product that does not exist, so the foreign key
    rejects it after the first line has already been written and its stock
    raised.
    """
    purchases, stock = ctx["purchases"], ctx["stock"]
    before_purchases = len(purchases.list())
    before_stock = stock.quantity_on_hand(ctx["rice"], ctx["warehouse"])

    draft = _draft(ctx)
    draft.lines = [
        PurchaseLine(ctx["rice"], Decimal("10"), Decimal("395.50")),
        PurchaseLine(999_999, Decimal("5"), Decimal("100.00")),     # no such product
    ]
    with pytest.raises(Exception):
        purchases.create(draft)

    assert len(purchases.list()) == before_purchases, "a purchase row survived"
    assert stock.quantity_on_hand(ctx["rice"], ctx["warehouse"]) == before_stock
    assert stock.find_drift() == []


def test_a_rolled_back_purchase_returns_its_reference_number(ctx):
    """Gapless numbering: the failed attempt must not burn a reference."""
    purchases = ctx["purchases"]
    expected = purchases.next_reference_no()

    draft = _draft(ctx)
    draft.lines = [PurchaseLine(999_999, Decimal("1"), Decimal("1"))]
    with pytest.raises(Exception):
        purchases.create(draft)

    assert purchases.next_reference_no() == expected
    purchase_id = purchases.create(_draft(ctx))
    assert purchases.get(purchase_id)["reference_no"] == expected


def test_references_are_sequential(ctx):
    purchases = ctx["purchases"]
    first = purchases.get(purchases.create(_draft(ctx)))["reference_no"]
    second = purchases.get(purchases.create(_draft(ctx)))["reference_no"]
    assert first.startswith("PUR-") and str(date.today().year) in first
    assert int(second.split("-")[-1]) == int(first.split("-")[-1]) + 1


# ------------------------------------------------------------- side effects

def test_the_product_cost_is_updated_on_receipt(ctx):
    assert ctx["products"].get(ctx["rice"]).purchase_price == Decimal("0.00")
    ctx["purchases"].create(_draft(ctx))
    assert ctx["products"].get(ctx["rice"]).purchase_price == Decimal("395.50")


# ------------------------------------------------------------------ listing

def test_list_filters_by_supplier_and_date(ctx):
    purchases = ctx["purchases"]
    purchases.create(_draft(ctx))
    assert len(purchases.list(supplier_id=ctx["supplier"])) == 1
    assert purchases.list(supplier_id=999999) == []
    assert len(purchases.list(date_from=date.today(), date_to=date.today())) == 1
    assert purchases.list(date_to=date.today() - timedelta(days=1)) == []


def test_list_filters_by_payment_status(ctx):
    purchases = ctx["purchases"]
    purchases.create(_draft(ctx))                                   # due
    draft = _draft(ctx)
    draft.paid_amount = draft.grand_total
    purchases.create(draft)                                         # paid
    assert len(purchases.list(payment_status="due")) == 1
    assert len(purchases.list(payment_status="paid")) == 1


# ------------------------------------------------------------ authorisation

def test_writes_need_a_signed_in_user(db):
    with pytest.raises(ValidationError, match="signed-in user"):
        PurchaseService().create(PurchaseDraft(supplier_id=1, warehouse_id=1))


def test_a_cashier_cannot_record_a_purchase(ctx):
    ctx["auth"].create_user("zz_pur_cashier", "Counter", "password1", Role.CASHIER)
    cashier = AuthService()
    cashier.login("zz_pur_cashier", "password1")
    with pytest.raises(PermissionDenied):
        PurchaseService(cashier).create(_draft(ctx))
