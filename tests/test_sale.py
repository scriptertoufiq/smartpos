"""M9 tests: the POS counter.

Acceptance criteria from docs/PLAN.md:
  * selling more than on hand raises InsufficientStock and writes nothing
  * a held bill blocks no stock and can be resumed on another terminal
  * change_amount = paid - grand_total, never negative on a cash sale
  * the printed invoice totals match the database row exactly
  * killing the app mid-save leaves no partial sale
  * a scan-to-printed-invoice sale completes in under 5 seconds
"""

from __future__ import annotations

import threading
import time
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
from smartpos.services.customer_service import CustomerService
from smartpos.services.product_service import ProductService
from smartpos.services.purchase_service import PurchaseDraft, PurchaseLine, PurchaseService
from smartpos.services.sale_service import SaleDraft, SaleLine, SaleService
from smartpos.services.stock_service import StockService
from smartpos.services.supplier_service import SupplierService
from smartpos.services.warehouse_service import WarehouseService

MARK = "ZZSALE"


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
        # Counter sales belong to the walk-in customer, which is never deleted,
        # so they have to be found through the products they contain.
        mine = ("SELECT * FROM (SELECT DISTINCT i.sale_id FROM sale_items i "
                "JOIN products p ON p.id = i.product_id "
                "WHERE p.sku LIKE %s) AS t")
        for sql in (
            f"DELETE FROM customer_payments WHERE reference_type = 'sale' "
            f"AND reference_id IN ({mine})",
            f"DELETE FROM sales WHERE id IN ({mine})",
            "DELETE FROM sale_items WHERE product_id IN "
            " (SELECT id FROM products WHERE sku LIKE %s)",
            "DELETE FROM sales WHERE customer_id IN "
            " (SELECT id FROM customers WHERE name LIKE %s)",
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
            "DELETE FROM customers WHERE name LIKE %s AND is_walk_in = 0",
        ):
            cur.execute(sql, (MARK + "%",) if "%s" in sql else ())
        cur.execute("DELETE FROM sale_items WHERE sale_id NOT IN (SELECT id FROM sales)")
        cur.execute("DELETE FROM users WHERE username LIKE 'zz_%'")
        cur.close()


@pytest.fixture
def ctx(db):
    _purge()
    auth = AuthService()
    auth.login("admin", "admin123")
    products = ProductService(auth)
    sales = SaleService(auth)
    stock = StockService(auth)

    warehouse = next(w for w in WarehouseService(auth).list_all()
                     if w.code == "MAIN").id
    supplier = SupplierService(auth).create(f"{MARK} Supplier")
    rice = products.create(f"{MARK}-RICE", "Rice 5kg", sale_price="420.00")
    oil = products.create(f"{MARK}-OIL", "Oil 1L", sale_price="175.00")

    # Stock arrives the way it really does: through a purchase.
    PurchaseService(auth).create(PurchaseDraft(
        supplier_id=supplier, warehouse_id=warehouse,
        lines=[PurchaseLine(rice, Decimal("100"), Decimal("395.50")),
               PurchaseLine(oil, Decimal("50"), Decimal("160.00"))]))

    yield {"auth": auth, "sales": sales, "products": products, "stock": stock,
           "warehouse": warehouse, "rice": rice, "oil": oil, "supplier": supplier}
    _purge()


def _draft(ctx, **overrides) -> SaleDraft:
    draft = SaleDraft(
        warehouse_id=ctx["warehouse"],
        lines=[SaleLine(ctx["rice"], Decimal("2"), Decimal("420.00"))],
        tendered=Decimal("840.00"))
    for key, value in overrides.items():
        setattr(draft, key, value)
    return draft


def _no_drift(ctx) -> None:
    assert ctx["stock"].find_drift() == [], "ledger and levels disagree"


# ------------------------------------------------------------- the happy path

def test_a_sale_deducts_stock(ctx):
    stock, rice, w = ctx["stock"], ctx["rice"], ctx["warehouse"]
    assert stock.quantity_on_hand(rice, w) == Decimal("100.000")
    ctx["sales"].create(_draft(ctx))
    assert stock.quantity_on_hand(rice, w) == Decimal("98.000")
    _no_drift(ctx)


def test_the_movement_points_back_at_the_sale(ctx):
    sale_id = ctx["sales"].create(_draft(ctx))
    movements = ctx["stock"].movements_for("sale", sale_id)
    assert len(movements) == 1
    assert movements[0]["movement_type"] == "sale"
    assert movements[0]["quantity"] == Decimal("-2.000")


def test_the_invoice_number_is_sequential_and_gapless(ctx):
    sales = ctx["sales"]
    first = sales.get(sales.create(_draft(ctx)))["invoice_no"]
    second = sales.get(sales.create(_draft(ctx)))["invoice_no"]
    assert first.startswith("INV-")
    assert int(second.split("-")[-1]) == int(first.split("-")[-1]) + 1


def test_a_counter_sale_goes_to_the_walk_in_customer(ctx):
    sale = ctx["sales"].get(ctx["sales"].create(_draft(ctx)))
    assert sale["customer_name"] == "Walk-in Customer"


def test_a_named_customer_can_be_used(ctx):
    customers = CustomerService(ctx["auth"])
    cid = customers.create(f"{MARK} Rahim")
    sale = ctx["sales"].get(ctx["sales"].create(_draft(ctx, customer_id=cid)))
    assert sale["customer_name"] == f"{MARK} Rahim"


# -------------------------------------------------------------------- totals

def test_totals_reconcile(ctx):
    draft = _draft(ctx, tax_amount=Decimal("50.00"),
                   discount_type="fixed", discount_value=Decimal("40.00"))
    draft.lines = [SaleLine(ctx["rice"], Decimal("2"), Decimal("420.00")),
                   SaleLine(ctx["oil"], Decimal("3"), Decimal("175.00"),
                            discount=Decimal("25.00"))]
    draft.tendered = Decimal("1500.00")
    sale = ctx["sales"].get(ctx["sales"].create(draft))

    lines_total = sum(Decimal(str(i["line_total"])) for i in sale["items"])
    assert lines_total == Decimal("840.00") + Decimal("500.00")      # 525 - 25
    assert Decimal(str(sale["subtotal"])) == Decimal("1340.00")
    assert Decimal(str(sale["grand_total"])) == Decimal("1350.00")   # 1340 - 40 + 50


def test_a_percentage_discount_is_stored_as_typed_and_as_money(ctx):
    """7.3: discount_value keeps the intent, discount keeps what it computed to."""
    draft = _draft(ctx, discount_type="percent", discount_value=Decimal("10"),
                   tendered=Decimal("756.00"))
    sale = ctx["sales"].get(ctx["sales"].create(draft))
    assert sale["discount_type"] == "percent"
    assert Decimal(str(sale["discount_value"])) == Decimal("10.00")
    assert Decimal(str(sale["discount"])) == Decimal("84.00")        # 10% of 840
    assert Decimal(str(sale["grand_total"])) == Decimal("756.00")


def test_a_discount_over_100_percent_is_refused(ctx):
    with pytest.raises(ValidationError, match="exceed 100"):
        ctx["sales"].create(_draft(ctx, discount_type="percent",
                                   discount_value=Decimal("150")))


def test_a_discount_larger_than_the_bill_is_refused(ctx):
    with pytest.raises(ValidationError, match="more than the bill"):
        ctx["sales"].create(_draft(ctx, discount_value=Decimal("99999")))


def test_the_same_product_twice_is_refused(ctx):
    draft = _draft(ctx)
    draft.lines = [SaleLine(ctx["rice"], Decimal("1"), Decimal("420")),
                   SaleLine(ctx["rice"], Decimal("1"), Decimal("420"))]
    with pytest.raises(ValidationError, match="appears twice"):
        ctx["sales"].create(draft)


def test_an_empty_cart_is_refused(ctx):
    with pytest.raises(ValidationError, match="cart is empty"):
        ctx["sales"].create(_draft(ctx, lines=[]))


# ------------------------------------------------------------ cost stamping

def test_the_cost_of_the_day_is_copied_onto_the_line(ctx):
    """The column that keeps profit reports stable."""
    sale_id = ctx["sales"].create(_draft(ctx))
    assert Decimal(str(ctx["sales"].items_of(sale_id)[0]["unit_cost"])) == Decimal("395.50")


def test_changing_the_cost_later_does_not_rewrite_the_sale(ctx):
    sale_id = ctx["sales"].create(_draft(ctx))
    ctx["products"].update(ctx["rice"], purchase_price="500.00")
    assert Decimal(str(ctx["sales"].items_of(sale_id)[0]["unit_cost"])) == Decimal("395.50")
    assert ctx["products"].get(ctx["rice"]).purchase_price == Decimal("500.00")


# ------------------------------------------------------------------- payment

def test_change_is_the_tender_less_the_total(ctx):
    """Acceptance: change_amount = paid - grand_total, never negative."""
    sale = ctx["sales"].get(ctx["sales"].create(_draft(ctx, tendered=Decimal("1000.00"))))
    assert Decimal(str(sale["grand_total"])) == Decimal("840.00")
    assert Decimal(str(sale["change_amount"])) == Decimal("160.00")
    assert Decimal(str(sale["paid_amount"])) == Decimal("840.00"), "change is not income"
    assert sale["payment_status"] == "paid"


def test_exact_tender_leaves_no_change(ctx):
    sale = ctx["sales"].get(ctx["sales"].create(_draft(ctx, tendered=Decimal("840.00"))))
    assert Decimal(str(sale["change_amount"])) == Decimal("0.00")


def test_an_underpayment_is_partial_and_change_stays_zero(ctx):
    sale = ctx["sales"].get(ctx["sales"].create(_draft(ctx, tendered=Decimal("500.00"))))
    assert sale["payment_status"] == "partial"
    assert Decimal(str(sale["change_amount"])) == Decimal("0.00")
    assert Decimal(str(sale["paid_amount"])) == Decimal("500.00")


def test_a_credit_sale_is_due(ctx):
    sale = ctx["sales"].get(ctx["sales"].create(_draft(ctx, tendered=Decimal("0"))))
    assert sale["payment_status"] == "due"


def test_the_tender_reaches_the_customer_ledger_without_the_change(ctx):
    customers = CustomerService(ctx["auth"])
    cid = customers.create(f"{MARK} Ledger")
    ctx["sales"].create(_draft(ctx, customer_id=cid, tendered=Decimal("1000.00")))
    # Billed 840, tendered 1000, 160 handed back -> the customer owes nothing.
    assert customers.due_balance(cid) == Decimal("0.00")


def test_a_later_payment_settles_a_due_sale(ctx):
    sales = ctx["sales"]
    sale_id = sales.create(_draft(ctx, tendered=Decimal("0")))
    sales.record_payment(sale_id, "840.00")
    assert sales.get(sale_id)["payment_status"] == "paid"


def test_overpaying_a_due_sale_is_refused(ctx):
    sales = ctx["sales"]
    sale_id = sales.create(_draft(ctx, tendered=Decimal("300.00")))
    with pytest.raises(ValidationError, match="540.00 left to pay"):
        sales.record_payment(sale_id, "600.00")


# ------------------------------------------------------------- insufficient

def test_selling_more_than_is_held_writes_nothing(ctx):
    """Acceptance criterion."""
    sales, stock = ctx["sales"], ctx["stock"]
    before_stock = stock.quantity_on_hand(ctx["rice"], ctx["warehouse"])
    before_sales = len(sales.list())

    draft = _draft(ctx)
    draft.lines = [SaleLine(ctx["rice"], Decimal("500"), Decimal("420.00"))]
    with pytest.raises(InsufficientStock):
        sales.create(draft)

    assert stock.quantity_on_hand(ctx["rice"], ctx["warehouse"]) == before_stock
    assert len(sales.list()) == before_sales
    _no_drift(ctx)


def test_a_failure_on_the_second_line_undoes_the_first(ctx):
    """Acceptance: killing the sale mid-save leaves no partial sale."""
    sales, stock = ctx["sales"], ctx["stock"]
    before_rice = stock.quantity_on_hand(ctx["rice"], ctx["warehouse"])
    before_oil = stock.quantity_on_hand(ctx["oil"], ctx["warehouse"])

    draft = _draft(ctx)
    draft.lines = [SaleLine(ctx["rice"], Decimal("5"), Decimal("420.00")),
                   SaleLine(ctx["oil"], Decimal("999"), Decimal("175.00"))]
    with pytest.raises(InsufficientStock):
        sales.create(draft)

    assert stock.quantity_on_hand(ctx["rice"], ctx["warehouse"]) == before_rice
    assert stock.quantity_on_hand(ctx["oil"], ctx["warehouse"]) == before_oil
    _no_drift(ctx)


def test_a_failed_sale_returns_its_invoice_number(ctx):
    sales = ctx["sales"]
    expected = sales.next_invoice_no()
    draft = _draft(ctx)
    draft.lines = [SaleLine(ctx["rice"], Decimal("9999"), Decimal("420.00"))]
    with pytest.raises(InsufficientStock):
        sales.create(draft)
    assert sales.next_invoice_no() == expected
    assert sales.get(sales.create(_draft(ctx)))["invoice_no"] == expected


# ----------------------------------------------------------------- held bills

def test_a_held_bill_touches_no_stock(ctx):
    """Acceptance criterion."""
    sales, stock = ctx["sales"], ctx["stock"]
    before = stock.quantity_on_hand(ctx["rice"], ctx["warehouse"])
    sale_id = sales.hold(_draft(ctx))
    assert stock.quantity_on_hand(ctx["rice"], ctx["warehouse"]) == before
    assert sales.get(sale_id)["status"] == "held"
    assert sales.get(sale_id)["invoice_no"].startswith("HLD-")
    _no_drift(ctx)


def test_a_held_bill_can_be_resumed_from_another_service(ctx):
    """Acceptance: resumable on another terminal."""
    sale_id = ctx["sales"].hold(_draft(ctx))
    other_terminal = SaleService(ctx["auth"])
    resumed = other_terminal.resume(sale_id)
    assert resumed["status"] == "held"
    assert len(resumed["items"]) == 1
    assert sale_id in {h["id"] for h in other_terminal.list_held()}


def test_completing_a_held_bill_takes_stock_and_a_real_invoice(ctx):
    sales, stock = ctx["sales"], ctx["stock"]
    before = stock.quantity_on_hand(ctx["rice"], ctx["warehouse"])
    sale_id = sales.hold(_draft(ctx))
    sales.complete_held(sale_id, tendered=Decimal("1000.00"))

    sale = sales.get(sale_id)
    assert sale["status"] == "completed"
    assert sale["invoice_no"].startswith("INV-")
    assert Decimal(str(sale["change_amount"])) == Decimal("160.00")
    assert stock.quantity_on_hand(ctx["rice"], ctx["warehouse"]) == before - Decimal("2")
    _no_drift(ctx)


def test_a_held_bill_cannot_be_completed_twice(ctx):
    sales = ctx["sales"]
    sale_id = sales.hold(_draft(ctx))
    sales.complete_held(sale_id, tendered=Decimal("840.00"))
    with pytest.raises(ValidationError, match="already been"):
        sales.complete_held(sale_id, tendered=Decimal("840.00"))


def test_discarding_a_held_bill_removes_it(ctx):
    sales = ctx["sales"]
    sale_id = sales.hold(_draft(ctx))
    sales.cancel_held(sale_id)
    assert sales.get(sale_id) is None


def test_a_completed_sale_cannot_be_discarded(ctx):
    sales = ctx["sales"]
    sale_id = sales.create(_draft(ctx))
    with pytest.raises(ValidationError, match="corrected with a return"):
        sales.cancel_held(sale_id)


def test_held_bills_do_not_appear_in_the_sales_list(ctx):
    sales = ctx["sales"]
    sales.hold(_draft(ctx))
    completed = sales.create(_draft(ctx))
    listed = {s["id"] for s in sales.list()}
    assert completed in listed and len(listed) == 1


# ----------------------------------------------------------------- lookup

def test_a_sale_can_be_found_by_invoice_number(ctx):
    sales = ctx["sales"]
    sale_id = sales.create(_draft(ctx))
    invoice_no = sales.get(sale_id)["invoice_no"]
    found = sales.get_by_invoice(invoice_no)
    assert found["id"] == sale_id and len(found["items"]) == 1
    assert sales.get_by_invoice("INV-1900-000001") is None


# ------------------------------------------------------------- concurrency

def test_two_counters_cannot_both_sell_the_last_unit(ctx):
    """The acceptance criterion that the whole design turns on."""
    sales, stock = ctx["sales"], ctx["stock"]
    products = ctx["products"]
    last = products.create(f"{MARK}-LAST", "Final Packet", sale_price="99.00")
    with connection.transaction() as conn:
        stock.put(last, ctx["warehouse"], "1",
                  __import__("smartpos.core.constants", fromlist=["MovementType"])
                  .MovementType.PURCHASE, conn)

    ready = threading.Barrier(2, timeout=15)
    outcomes: list[str] = []
    lock = threading.Lock()

    def sell(tag: str) -> None:
        auth = AuthService()
        auth.login("admin", "admin123")
        terminal = SaleService(auth)
        draft = SaleDraft(warehouse_id=ctx["warehouse"],
                          lines=[SaleLine(last, Decimal("1"), Decimal("99.00"))],
                          tendered=Decimal("99.00"))
        try:
            ready.wait()
            terminal.create(draft)
            with lock:
                outcomes.append(f"{tag}:sold")
        except InsufficientStock:
            with lock:
                outcomes.append(f"{tag}:refused")
        except Exception as exc:                        # noqa: BLE001
            with lock:
                outcomes.append(f"{tag}:error:{type(exc).__name__}")

    threads = [threading.Thread(target=sell, args=(f"till{i}",)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    print(f"\n    outcomes: {outcomes}")
    assert sorted(o.split(":")[1] for o in outcomes) == ["refused", "sold"], outcomes
    assert stock.quantity_on_hand(last, ctx["warehouse"]) == Decimal("0.000")
    _no_drift(ctx)


@pytest.mark.slow
def test_a_sale_completes_well_under_five_seconds(ctx):
    """Acceptance: scan to saved invoice inside the counter's budget."""
    sales = ctx["sales"]
    timings = []
    for _ in range(10):
        start = time.perf_counter()
        sales.create(_draft(ctx))
        timings.append(time.perf_counter() - start)
    median = sorted(timings)[len(timings) // 2]
    print(f"\n    sale saved in {median * 1000:.1f} ms (median), "
          f"{max(timings) * 1000:.1f} ms worst")
    assert max(timings) < 5.0


# ------------------------------------------------------------ authorisation

def test_selling_needs_a_signed_in_user(db):
    with pytest.raises(ValidationError, match="signed-in user"):
        SaleService().create(SaleDraft(warehouse_id=1))


def test_a_cashier_may_sell(ctx):
    """The one thing a cashier must be able to do."""
    ctx["auth"].create_user("zz_till", "Till Operator", "password1", Role.CASHIER)
    cashier = AuthService()
    cashier.login("zz_till", "password1")
    sale_id = SaleService(cashier).create(_draft(ctx))
    assert ctx["sales"].get(sale_id)["username"] == "zz_till"


def test_a_cashier_cannot_touch_stock_directly(ctx):
    ctx["auth"].create_user("zz_till2", "Till Two", "password1", Role.CASHIER)
    cashier = AuthService()
    cashier.login("zz_till2", "password1")
    with pytest.raises(PermissionDenied):
        StockService(cashier).adjust(ctx["rice"], ctx["warehouse"], "5", reason="no")


# ---------------------------------------------------------------- the invoice

def test_the_printed_invoice_matches_the_database_row(ctx, tmp_path):
    """Acceptance: printed totals match the saved sale exactly.

    The PDF is read back and its figures compared with the row, so a receipt
    cannot quietly disagree with the books.
    """
    from pypdf import PdfReader

    from smartpos.reports.invoice import render_a4, render_thermal

    draft = _draft(ctx, tax_amount=Decimal("50.00"),
                   discount_type="percent", discount_value=Decimal("10"),
                   tendered=Decimal("1000.00"))
    sale = ctx["sales"].get(ctx["sales"].create(draft))

    for renderer, name in ((render_thermal, "receipt"), (render_a4, "invoice")):
        path = renderer(sale, tmp_path / f"{name}.pdf")
        assert path.is_file() and path.stat().st_size > 0
        text = " ".join(page.extract_text() for page in PdfReader(str(path)).pages)
        text = text.replace("\n", " ")

        assert sale["invoice_no"] in text, f"{name} is missing the invoice number"
        for field in ("subtotal", "grand_total", "paid_amount",
                      "discount", "tax_amount", "change_amount"):
            value = Decimal(str(sale[field]))
            if value > Decimal("0.00"):
                assert f"{value:,.2f}" in text, (
                    f"{name} does not show {field} = {value:,.2f}")
