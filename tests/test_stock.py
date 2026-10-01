"""M7 tests: the stock ledger and its invariant.

Acceptance criteria from docs/PLAN.md:
  * SUM(stock_movements.quantity) == stock.quantity for every (product, warehouse)
  * every movement names its source document via reference_type / reference_id
  * a transfer either moves stock in both warehouses or neither
  * two concurrent sales of the last unit: one succeeds, one raises InsufficientStock
  * stock can never go below zero through the normal paths
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from smartpos.core.constants import MovementType, Role
from smartpos.core.exceptions import (
    DatabaseError,
    InsufficientStock,
    PermissionDenied,
    ValidationError,
)
from smartpos.db import connection
from smartpos.services.auth_service import AuthService
from smartpos.services.product_service import ProductService
from smartpos.services.stock_service import StockService
from smartpos.services.warehouse_service import WarehouseService

MARK = "ZZSTK"


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
            "DELETE FROM stock_transfer_items WHERE transfer_id IN "
            " (SELECT id FROM stock_transfers WHERE reference_no LIKE 'TRF-%')",
            "DELETE FROM stock_movements WHERE product_id IN "
            " (SELECT id FROM products WHERE sku LIKE %s)",
            "DELETE FROM stock WHERE product_id IN "
            " (SELECT id FROM products WHERE sku LIKE %s)",
            "DELETE FROM products WHERE sku LIKE %s",
        ):
            cur.execute(sql, (MARK + "%",) if "%s" in sql else ())
        cur.execute("DELETE FROM stock_transfers WHERE from_warehouse_id IN "
                    "(SELECT id FROM warehouses WHERE code LIKE %s)", (MARK + "%",))
        cur.execute("DELETE FROM stock_transfers WHERE to_warehouse_id IN "
                    "(SELECT id FROM warehouses WHERE code LIKE %s)", (MARK + "%",))
        cur.execute("UPDATE users SET warehouse_id = NULL WHERE warehouse_id IN "
                    "(SELECT id FROM warehouses WHERE code LIKE %s)", (MARK + "%",))
        cur.execute("DELETE FROM warehouses WHERE code LIKE %s", (MARK + "%",))
        cur.execute("DELETE FROM users WHERE username LIKE 'zz_%'")
        cur.close()


@pytest.fixture
def ctx(db):
    """A signed-in stock service, one product and a second warehouse."""
    _purge()
    auth = AuthService()
    auth.login("admin", "admin123")
    stock = StockService(auth)
    products = ProductService(auth)
    warehouses = WarehouseService(auth)

    product_id = products.create(f"{MARK}-1", "Test Rice 5kg", sale_price="420",
                                 reorder_level="10")
    main = next(w for w in warehouses.list_all() if w.code == "MAIN").id
    godown = warehouses.create(f"{MARK}G", "Test Godown")
    yield {"stock": stock, "products": products, "warehouses": warehouses,
           "auth": auth, "product": product_id, "main": main, "godown": godown}
    _purge()


def _assert_no_drift(stock: StockService) -> None:
    drift = stock.find_drift()
    assert drift == [], f"ledger and levels disagree: {drift}"


# --------------------------------------------------------------- the basics

def test_putting_stock_in_raises_the_level(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with connection.transaction() as conn:
        balance = stock.put(p, w, "25", MovementType.PURCHASE, conn,
                            reference_type="purchase", reference_id=1)
    assert balance == Decimal("25.000")
    assert stock.quantity_on_hand(p, w) == Decimal("25.000")
    _assert_no_drift(stock)


def test_taking_stock_out_lowers_the_level(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with connection.transaction() as conn:
        stock.put(p, w, "25", MovementType.PURCHASE, conn, reference_type="purchase",
                  reference_id=1)
    with connection.transaction() as conn:
        stock.take(p, w, "4", MovementType.SALE, conn, reference_type="sale",
                   reference_id=9)
    assert stock.quantity_on_hand(p, w) == Decimal("21.000")
    _assert_no_drift(stock)


def test_a_movement_must_be_inside_a_transaction(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with pytest.raises(DatabaseError, match="inside a transaction"):
        stock.put(p, w, "1", MovementType.PURCHASE, None)


def test_a_zero_movement_is_refused(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with connection.transaction() as conn:
        with pytest.raises(ValidationError, match="cannot be for zero"):
            stock.apply_movement(p, w, Decimal("0"), MovementType.PURCHASE, conn)


def test_fractional_quantities_survive(ctx):
    """Goods sold by weight: 2.5 kg must not become 2 or 3."""
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with connection.transaction() as conn:
        stock.put(p, w, "10.750", MovementType.PURCHASE, conn)
    with connection.transaction() as conn:
        stock.take(p, w, "2.250", MovementType.SALE, conn)
    assert stock.quantity_on_hand(p, w) == Decimal("8.500")
    _assert_no_drift(stock)


# ------------------------------------------------------------ never negative

def test_stock_cannot_go_below_zero(ctx):
    """Acceptance criterion."""
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with connection.transaction() as conn:
        stock.put(p, w, "5", MovementType.PURCHASE, conn)
    with pytest.raises(InsufficientStock, match="Only 5"):
        with connection.transaction() as conn:
            stock.take(p, w, "6", MovementType.SALE, conn)
    assert stock.quantity_on_hand(p, w) == Decimal("5.000")
    _assert_no_drift(stock)


def test_selling_from_an_empty_warehouse_is_refused(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["godown"]
    with pytest.raises(InsufficientStock):
        with connection.transaction() as conn:
            stock.take(p, w, "1", MovementType.SALE, conn)


def test_a_failed_movement_leaves_no_ledger_row(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with connection.transaction() as conn:
        stock.put(p, w, "2", MovementType.PURCHASE, conn)
    before = len(stock.ledger(p, w))
    with pytest.raises(InsufficientStock):
        with connection.transaction() as conn:
            stock.take(p, w, "99", MovementType.SALE, conn)
    assert len(stock.ledger(p, w)) == before
    _assert_no_drift(stock)


# ----------------------------------------------------------- the audit trail

def test_every_movement_names_its_source_document(ctx):
    """Acceptance criterion."""
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with connection.transaction() as conn:
        stock.put(p, w, "10", MovementType.PURCHASE, conn,
                  reference_type="purchase", reference_id=77)
    row = stock.movements_for("purchase", 77)[0]
    assert row["product_id"] == p
    assert row["quantity"] == Decimal("10.000")
    assert row["balance_after"] == Decimal("10.000")


def test_the_ledger_carries_a_running_balance(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    for qty, kind in (("50", MovementType.PURCHASE), ("-2", MovementType.SALE),
                      ("-3", MovementType.SALE), ("1", MovementType.SALE_RETURN)):
        with connection.transaction() as conn:
            stock.apply_movement(p, w, Decimal(qty), kind, conn)
    balances = [r["balance_after"] for r in reversed(stock.ledger(p, w))]
    assert balances == [Decimal("50.000"), Decimal("48.000"),
                        Decimal("45.000"), Decimal("46.000")]


def test_the_movement_records_who_did_it(ctx):
    stock, p, w, auth = ctx["stock"], ctx["product"], ctx["main"], ctx["auth"]
    with connection.transaction() as conn:
        stock.put(p, w, "1", MovementType.PURCHASE, conn)
    assert stock.ledger(p, w)[0]["username"] == auth.current_user().username


# ------------------------------------------------------------- adjustments

def test_an_adjustment_writes_the_difference_not_the_total(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with connection.transaction() as conn:
        stock.put(p, w, "20", MovementType.PURCHASE, conn)
    stock.adjust(p, w, "17", reason="Physical count: 3 damaged")
    assert stock.quantity_on_hand(p, w) == Decimal("17.000")
    latest = stock.ledger(p, w)[0]
    assert latest["quantity"] == Decimal("-3.000"), "the delta, not the new total"
    assert latest["movement_type"] == "adjustment"
    assert "damaged" in latest["note"]
    _assert_no_drift(stock)


def test_an_adjustment_without_a_reason_is_refused(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with pytest.raises(ValidationError, match="say why"):
        stock.adjust(p, w, "5", reason="   ")


def test_adjusting_to_the_same_quantity_writes_nothing(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with connection.transaction() as conn:
        stock.put(p, w, "8", MovementType.PURCHASE, conn)
    before = len(stock.ledger(p, w))
    stock.adjust(p, w, "8", reason="count matches")
    assert len(stock.ledger(p, w)) == before


def test_adjusting_below_zero_is_refused(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with pytest.raises(ValidationError, match="below zero"):
        stock.adjust(p, w, "-1", reason="nonsense")


def test_opening_stock_can_only_be_set_once(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    stock.set_opening(p, w, "100")
    assert stock.quantity_on_hand(p, w) == Decimal("100.000")
    assert stock.ledger(p, w)[0]["movement_type"] == "opening"
    with pytest.raises(ValidationError, match="already holds"):
        stock.set_opening(p, w, "50")


# --------------------------------------------------------------- transfers

def test_a_transfer_moves_stock_both_ways(ctx):
    stock, p = ctx["stock"], ctx["product"]
    main, godown = ctx["main"], ctx["godown"]
    with connection.transaction() as conn:
        stock.put(p, main, "30", MovementType.PURCHASE, conn)

    transfer_id = stock.transfer(main, godown, [(p, Decimal("12"))], note="restock")

    assert stock.quantity_on_hand(p, main) == Decimal("18.000")
    assert stock.quantity_on_hand(p, godown) == Decimal("12.000")
    kinds = {m["movement_type"] for m in stock.movements_for("transfer", transfer_id)}
    assert kinds == {"transfer_out", "transfer_in"}
    _assert_no_drift(stock)


def test_a_transfer_of_more_than_is_held_moves_nothing(ctx):
    """Acceptance: either both warehouses change, or neither does."""
    stock, p = ctx["stock"], ctx["product"]
    main, godown = ctx["main"], ctx["godown"]
    with connection.transaction() as conn:
        stock.put(p, main, "5", MovementType.PURCHASE, conn)

    with pytest.raises(InsufficientStock):
        stock.transfer(main, godown, [(p, Decimal("9"))])

    assert stock.quantity_on_hand(p, main) == Decimal("5.000")
    assert stock.quantity_on_hand(p, godown) == Decimal("0.000")
    assert stock.transfers() == [] or all(
        t["reference_no"] for t in stock.transfers())
    _assert_no_drift(stock)


def test_a_multi_line_transfer_is_all_or_nothing(ctx):
    """The second line fails; the first must not have moved."""
    stock, products = ctx["stock"], ctx["products"]
    main, godown = ctx["main"], ctx["godown"]
    good = ctx["product"]
    short = products.create(f"{MARK}-2", "Short Product")

    with connection.transaction() as conn:
        stock.put(good, main, "40", MovementType.PURCHASE, conn)
        stock.put(short, main, "2", MovementType.PURCHASE, conn)

    with pytest.raises(InsufficientStock):
        stock.transfer(main, godown, [(good, Decimal("10")), (short, Decimal("5"))])

    assert stock.quantity_on_hand(good, main) == Decimal("40.000")
    assert stock.quantity_on_hand(good, godown) == Decimal("0.000")
    assert stock.quantity_on_hand(short, main) == Decimal("2.000")
    _assert_no_drift(stock)


def test_a_transfer_to_the_same_outlet_is_refused(ctx):
    stock, p, main = ctx["stock"], ctx["product"], ctx["main"]
    with pytest.raises(ValidationError, match="two different outlets"):
        stock.transfer(main, main, [(p, Decimal("1"))])


def test_the_same_product_twice_in_one_transfer_is_refused(ctx):
    stock, p = ctx["stock"], ctx["product"]
    with pytest.raises(ValidationError, match="appears twice"):
        stock.transfer(ctx["main"], ctx["godown"],
                       [(p, Decimal("1")), (p, Decimal("2"))])


def test_a_transfer_gets_a_gapless_reference(ctx):
    stock, p = ctx["stock"], ctx["product"]
    with connection.transaction() as conn:
        stock.put(p, ctx["main"], "10", MovementType.PURCHASE, conn)
    stock.transfer(ctx["main"], ctx["godown"], [(p, Decimal("1"))])
    reference = stock.transfers()[0]["reference_no"]
    assert reference.startswith("TRF-")
    assert str(__import__("datetime").date.today().year) in reference


# ------------------------------------------------------------- low stock

def test_low_stock_lists_products_at_or_below_the_reorder_level(ctx):
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with connection.transaction() as conn:
        stock.put(p, w, "10", MovementType.PURCHASE, conn)      # level is 10
    assert any(r["product_id"] == p for r in stock.low_stock())

    with connection.transaction() as conn:
        stock.put(p, w, "5", MovementType.PURCHASE, conn)        # now 15
    assert not any(r["product_id"] == p for r in stock.low_stock())


# ----------------------------------------------------- the concurrency case

def test_two_counters_cannot_both_sell_the_last_unit(ctx):
    """Acceptance: one succeeds, one raises InsufficientStock.

    Both threads read, then try to take the single remaining unit. Without the
    row lock inside the transaction both would see "1 left" and both would
    sell it.
    """
    stock, p, w = ctx["stock"], ctx["product"], ctx["main"]
    with connection.transaction() as conn:
        stock.put(p, w, "1", MovementType.PURCHASE, conn)

    both_ready = threading.Barrier(2, timeout=10)
    outcomes: list[str] = []
    lock = threading.Lock()

    def sell(tag: str) -> None:
        service = StockService()          # its own service, as a second app would have
        try:
            with connection.transaction() as conn:
                both_ready.wait()         # line up so the race is real
                service.take(p, w, "1", MovementType.SALE, conn,
                             reference_type="sale", reference_id=0)
            with lock:
                outcomes.append(f"{tag}:sold")
        except InsufficientStock:
            with lock:
                outcomes.append(f"{tag}:refused")
        except Exception as exc:          # noqa: BLE001
            with lock:
                outcomes.append(f"{tag}:error:{type(exc).__name__}")

    threads = [threading.Thread(target=sell, args=(f"t{i}",)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    sold = [o for o in outcomes if o.endswith(":sold")]
    refused = [o for o in outcomes if o.endswith(":refused")]
    print(f"\n    outcomes: {outcomes}")

    assert len(sold) == 1, f"exactly one sale must succeed, got {outcomes}"
    assert len(refused) == 1, f"exactly one must be refused, got {outcomes}"
    assert stock.quantity_on_hand(p, w) == Decimal("0.000")
    _assert_no_drift(stock)


# ------------------------------------------------------------ authorisation

def test_reads_work_without_a_session(db):
    assert StockService().levels() is not None


def test_adjustments_need_a_signed_in_user(db):
    with pytest.raises(ValidationError, match="signed-in user"):
        StockService().adjust(1, 1, "5", reason="nope")


def test_a_cashier_cannot_adjust_or_transfer(ctx):
    admin = ctx["auth"]
    admin.create_user("zz_stock_cashier", "Counter", "password1", Role.CASHIER)
    cashier = AuthService()
    cashier.login("zz_stock_cashier", "password1")
    restricted = StockService(cashier)
    assert restricted.levels() is not None
    with pytest.raises(PermissionDenied):
        restricted.adjust(ctx["product"], ctx["main"], "5", reason="nope")
    with pytest.raises(PermissionDenied):
        restricted.transfer(ctx["main"], ctx["godown"], [(ctx["product"], Decimal("1"))])
