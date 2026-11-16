#!/usr/bin/env python3
"""Fill the database with a small, believable shop so the screens have something
in them.

Everything it creates is prefixed DEMO- or "Demo ", so `--clear` can take it all
back out without touching anything real.

    python scripts/demo_data.py          # create it
    python scripts/demo_data.py --clear  # remove it again
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartpos.config import get_settings                                  # noqa: E402
from smartpos.core.exceptions import SmartPOSError                        # noqa: E402
from smartpos.core.logging import configure_logging                       # noqa: E402
from smartpos.db import connection                                        # noqa: E402
from smartpos.services.auth_service import AuthService                    # noqa: E402
from smartpos.services.category_service import CategoryService            # noqa: E402
from smartpos.services.customer_service import CustomerService            # noqa: E402
from smartpos.services.product_service import ProductService              # noqa: E402
from smartpos.services.purchase_service import (                          # noqa: E402
    PurchaseDraft, PurchaseLine, PurchaseService,
)
from smartpos.services.sale_service import SaleDraft, SaleLine, SaleService  # noqa: E402
from smartpos.services.supplier_service import SupplierService            # noqa: E402
from smartpos.services.warehouse_service import WarehouseService          # noqa: E402

SKU = "DEMO-"
NAME = "Demo "

# (sku, name, category, unit short name, cost, price, reorder level)
CATALOGUE = [
    ("RICE5", "Rice Miniket 5kg",      "Grocery",   "pcs", "395.50", "420.00", "15"),
    ("RICE1", "Rice Chinigura 1kg",    "Grocery",   "kg",  "118.00", "135.00", "20"),
    ("OIL1",  "Soyabean Oil 1L",       "Grocery",   "ltr", "160.00", "175.00", "24"),
    ("SALT1", "Salt ACI 1kg",          "Grocery",   "kg",  "30.00",  "38.00",  "30"),
    ("SUGR1", "Sugar 1kg",             "Grocery",   "kg",  "118.00", "130.00", "25"),
    ("LENT1", "Lentil Mosur 1kg",      "Grocery",   "kg",  "135.00", "155.00", "20"),
    ("TEA400","Tea Ispahani 400g",     "Beverages", "pkt", "190.00", "215.00", "10"),
    ("COLA",  "Cola 1.5L",             "Beverages", "pcs", "95.00",  "110.00", "18"),
    ("WATER", "Drinking Water 2L",     "Beverages", "pcs", "28.00",  "35.00",  "40"),
    ("SOAP",  "Bath Soap 100g",        "Household", "pcs", "42.00",  "55.00",  "25"),
    ("DETER", "Detergent 500g",        "Household", "pkt", "88.00",  "105.00", "15"),
    ("BRUSH", "Toothbrush",            "Household", "pcs", "25.00",  "40.00",  "20"),
]


def clear() -> None:
    with connection.transaction() as conn:
        cur = conn.cursor()
        mine = ("SELECT * FROM (SELECT DISTINCT i.sale_id FROM sale_items i "
                "JOIN products p ON p.id = i.product_id WHERE p.sku LIKE %s) AS t")
        for sql, params in (
            (f"DELETE FROM customer_payments WHERE reference_type='sale' "
             f"AND reference_id IN ({mine})", (SKU + "%",)),
            (f"DELETE FROM sales WHERE id IN ({mine})", (SKU + "%",)),
            ("DELETE FROM sale_items WHERE product_id IN "
             "(SELECT id FROM products WHERE sku LIKE %s)", (SKU + "%",)),
            ("DELETE FROM purchase_items WHERE product_id IN "
             "(SELECT id FROM products WHERE sku LIKE %s)", (SKU + "%",)),
            ("DELETE FROM supplier_payments WHERE supplier_id IN "
             "(SELECT id FROM suppliers WHERE name LIKE %s)", (NAME + "%",)),
            ("DELETE FROM purchases WHERE supplier_id IN "
             "(SELECT id FROM suppliers WHERE name LIKE %s)", (NAME + "%",)),
            ("DELETE FROM stock_movements WHERE product_id IN "
             "(SELECT id FROM products WHERE sku LIKE %s)", (SKU + "%",)),
            ("DELETE FROM stock WHERE product_id IN "
             "(SELECT id FROM products WHERE sku LIKE %s)", (SKU + "%",)),
            ("DELETE FROM products WHERE sku LIKE %s", (SKU + "%",)),
            ("DELETE FROM suppliers WHERE name LIKE %s", (NAME + "%",)),
            ("DELETE FROM customer_payments WHERE customer_id IN "
             "(SELECT id FROM customers WHERE name LIKE %s)", (NAME + "%",)),
            ("DELETE FROM customers WHERE name LIKE %s AND is_walk_in = 0", (NAME + "%",)),
            ("UPDATE categories SET parent_id = NULL WHERE name LIKE %s", (NAME + "%",)),
            ("DELETE FROM categories WHERE name LIKE %s", (NAME + "%",)),
        ):
            cur.execute(sql, params)
        cur.close()
    print("Demo data removed.")


def build(auth: AuthService) -> None:
    categories = CategoryService(auth)
    products = ProductService(auth)
    suppliers = SupplierService(auth)
    customers = CustomerService(auth)
    purchases = PurchaseService(auth)
    sales = SaleService(auth)

    warehouse = next(w for w in WarehouseService(auth).list_all() if w.is_active).id
    units = {u.short_name: u.id for u in categories.list_units()}

    made = {}
    for label in ("Grocery", "Beverages", "Household"):
        made[label] = categories.create(f"{NAME}{label}")
    print(f"  {len(made)} categories")

    created = {}
    for sku, name, group, unit, cost, price, reorder in CATALOGUE:
        created[sku] = products.create(
            f"{SKU}{sku}", name, category_id=made[group], unit_id=units.get(unit),
            purchase_price=cost, sale_price=price, reorder_level=reorder,
            auto_barcode=True)
    print(f"  {len(created)} products, each with a barcode")

    supplier = suppliers.create(f"{NAME}Wholesale Depot", phone="01711000000",
                                address="Karwan Bazar, Dhaka")
    rahim = customers.create(f"{NAME}Rahim Store", phone="01811000000")
    customers.create(f"{NAME}Karim Traders", phone="01911000000")
    print("  1 supplier, 2 named customers")

    # Stock arrives the way it really does: through a purchase.
    lines = [PurchaseLine(created[sku], Decimal("60"), Decimal(cost))
             for sku, _n, _g, _u, cost, _p, _r in CATALOGUE]
    purchases.create(PurchaseDraft(
        supplier_id=supplier, warehouse_id=warehouse, lines=lines,
        paid_amount=Decimal("40000.00"), note="Opening stock"))
    print("  1 purchase — 60 of each item, part paid")

    # A fortnight of trading, so the dashboard trend has a shape.
    basket = [("RICE5", 2), ("OIL1", 3), ("SALT1", 2), ("TEA400", 1),
              ("COLA", 4), ("SOAP", 2), ("SUGR1", 2), ("WATER", 5)]
    counter = 0
    for days_ago in range(13, -1, -1):
        for n in range(1 + days_ago % 3):
            sku, qty = basket[counter % len(basket)]
            counter += 1
            product = products.get(created[sku])
            total = Decimal(str(qty)) * product.sale_price
            on_credit = counter % 7 == 0
            sale_id = sales.create(SaleDraft(
                warehouse_id=warehouse,
                customer_id=rahim if on_credit else None,
                lines=[SaleLine(created[sku], Decimal(str(qty)), product.sale_price)],
                tendered=Decimal("0") if on_credit else total))
            if days_ago:
                with connection.transaction() as conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE sales SET sale_date = sale_date - INTERVAL %s DAY "
                                "WHERE id = %s", (days_ago, sale_id))
                    cur.execute("UPDATE customer_payments SET paid_at = paid_at - "
                                "INTERVAL %s DAY WHERE reference_type='sale' "
                                "AND reference_id = %s", (days_ago, sale_id))
                    cur.close()
    print(f"  {counter} sales across 14 days, a few on credit")


def main() -> int:
    settings = get_settings()
    configure_logging("WARNING", settings.log_dir)
    connection.init_pool()
    try:
        if "--clear" in sys.argv:
            clear()
            return 0

        auth = AuthService()
        auth.login("admin", "admin123")
        print(f"Building demo data in {settings.database.dsn}")
        clear()                       # start from a known state
        build(auth)
        print("\nDone. Sign in as admin / admin123 and look at the Dashboard.")
        print("Remove it again with: python scripts/demo_data.py --clear")
        return 0
    except SmartPOSError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1
    finally:
        connection.close_pool()


if __name__ == "__main__":
    sys.exit(main())
