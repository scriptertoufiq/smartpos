"""M5 tests: products, barcodes and CSV.

Acceptance criteria from docs/PLAN.md:
  * get_by_barcode() returns in under 50 ms on 5,000 products
  * generated EAN-13 codes validate against a standard checker
  * a CSV import with 3 bad rows out of 100 imports 97 and reports the 3
  * duplicate SKU and duplicate barcode are both refused
"""

from __future__ import annotations

import csv
import time
from decimal import Decimal
from pathlib import Path

import pytest

from smartpos.core.constants import Role
from smartpos.core.exceptions import DatabaseError, PermissionDenied, ValidationError
from smartpos.db import connection
from smartpos.services.auth_service import AuthService
from smartpos.services.category_service import CategoryService
from smartpos.services.product_service import ProductService
from smartpos.utils import barcode as bc

SKU = "ZZP"


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
        cur.execute("DELETE FROM products WHERE sku LIKE %s", (SKU + "%",))
        cur.execute("UPDATE categories SET parent_id = NULL WHERE name LIKE 'ZZCAT%'")
        cur.execute("DELETE FROM categories WHERE name LIKE 'ZZCAT%'")
        cur.execute("DELETE FROM users WHERE username LIKE 'zz_%'")
        cur.close()


@pytest.fixture
def svc(db):
    _purge()
    auth = AuthService()
    auth.login("admin", "admin123")
    yield ProductService(auth)
    _purge()


# ------------------------------------------------------------------ barcodes

@pytest.mark.parametrize("code", [
    "4006381333931", "5901234123457", "0012345678905", "9780201379624",
])
def test_check_digit_matches_real_published_barcodes(code):
    """Acceptance: generated codes validate against a standard checker."""
    assert bc.check_digit(code[:12]) == code[-1]
    assert bc.is_valid(code)


def test_a_wrong_check_digit_is_rejected():
    assert not bc.is_valid("4006381333930")


@pytest.mark.parametrize("bad", ["", "123", "abcdefghijklm", "40063813339311"])
def test_malformed_barcodes_are_rejected(bad):
    assert not bc.is_valid(bad)


@pytest.mark.parametrize("product_id", [1, 42, 999, 5000, 999999999])
def test_generated_codes_are_valid_ean13(product_id):
    code = bc.generate_code(product_id)
    assert len(code) == 13
    assert bc.is_valid(code)
    assert code.startswith("200"), "must use the in-store prefix range"


def test_generated_codes_are_stable_and_unique():
    codes = {bc.generate_code(i) for i in range(1, 200)}
    assert len(codes) == 199
    assert bc.generate_code(7) == bc.generate_code(7)


def test_a_label_image_is_written(tmp_path):
    written = bc.render_label(bc.generate_code(1), tmp_path / "label")
    assert written.is_file() and written.stat().st_size > 0


def test_a_label_sheet_holds_several(tmp_path):
    codes = [bc.generate_code(i) for i in (1, 2, 3, 4, 5)]
    sheet = bc.render_label_sheet(codes, tmp_path / "sheet.png", per_row=3)
    from PIL import Image
    assert Image.open(sheet).width > 0


# -------------------------------------------------------------------- create

def test_create_and_read_back(svc):
    pid = svc.create(f"{SKU}1", "Rice Miniket 5kg", sale_price="420.00",
                     purchase_price="395.50", reorder_level="10")
    p = svc.get(pid)
    assert p.sku == f"{SKU}1"
    assert p.name == "Rice Miniket 5kg"
    assert p.sale_price == Decimal("420.00")
    assert p.purchase_price == Decimal("395.50")
    assert p.reorder_level == Decimal("10.000")
    assert p.is_active


def test_sku_is_stored_in_capitals(svc):
    pid = svc.create(f"{SKU.lower()}2", "Lowercase SKU")
    assert svc.get(pid).sku == f"{SKU}2"


def test_duplicate_sku_is_refused(svc):
    """Acceptance: duplicate SKU refused."""
    svc.create(f"{SKU}3", "First")
    with pytest.raises(ValidationError, match="SKU .* already used"):
        svc.create(f"{SKU}3", "Second")


def test_duplicate_barcode_is_refused(svc):
    """Acceptance: duplicate barcode refused."""
    code = bc.generate_code(123456)
    svc.create(f"{SKU}4", "First", barcode=code)
    with pytest.raises(ValidationError, match="barcode .* already used"):
        svc.create(f"{SKU}5", "Second", barcode=code)


def test_several_products_may_have_no_barcode(svc):
    """The unique index permits many NULLs, but only one empty string --
    which is why blank is stored as NULL."""
    svc.create(f"{SKU}6", "No barcode A", barcode="")
    svc.create(f"{SKU}7", "No barcode B", barcode=None)
    assert svc.get_by_sku(f"{SKU}6").barcode is None


def test_a_13_digit_barcode_with_a_bad_check_digit_is_refused(svc):
    with pytest.raises(ValidationError, match="check digit"):
        svc.create(f"{SKU}8", "Typo", barcode="4006381333930")


def test_a_non_numeric_barcode_is_refused(svc):
    with pytest.raises(ValidationError, match="digits only"):
        svc.create(f"{SKU}9", "Letters", barcode="ABC123")


@pytest.mark.parametrize("bad_sku", ["", "   ", "HAS SPACE", "X" * 41])
def test_invalid_skus_are_refused(svc, bad_sku):
    with pytest.raises(ValidationError):
        svc.create(bad_sku, "Something")


def test_a_negative_price_is_refused(svc):
    with pytest.raises(ValidationError, match="cannot be negative"):
        svc.create(f"{SKU}10", "Negative", sale_price="-1")


def test_a_non_numeric_price_is_refused(svc):
    with pytest.raises(ValidationError, match="must be a number"):
        svc.create(f"{SKU}11", "Wordy", sale_price="cheap")


def test_auto_barcode_on_create(svc):
    pid = svc.create(f"{SKU}12", "Auto coded", auto_barcode=True)
    code = svc.get(pid).barcode
    assert code and bc.is_valid(code)


def test_a_failed_create_writes_nothing(svc):
    before = svc.count()
    with pytest.raises(ValidationError):
        svc.create(f"{SKU}13", "")
    assert svc.count() == before


# ------------------------------------------------------------------- lookup

def test_get_by_barcode_finds_the_product(svc):
    code = bc.generate_code(555)
    svc.create(f"{SKU}14", "Scannable", barcode=code)
    assert svc.get_by_barcode(code).sku == f"{SKU}14"


def test_get_by_barcode_returns_none_for_unknown(svc):
    assert svc.get_by_barcode("2009999999997") is None
    assert svc.get_by_barcode("") is None


def test_search_matches_name_sku_and_barcode(svc):
    code = bc.generate_code(777)
    svc.create(f"{SKU}15", "Soyabean Oil 1L", barcode=code)
    assert svc.search("soyabean")[0].sku == f"{SKU}15"
    assert svc.search(f"{SKU}15")[0].name == "Soyabean Oil 1L"
    assert svc.search(code)[0].name == "Soyabean Oil 1L"


def test_an_exact_barcode_match_sorts_first(svc):
    """Scanning must win even when the digits appear inside another name."""
    code = bc.generate_code(888)
    svc.create(f"{SKU}16", f"Box containing {code}")
    svc.create(f"{SKU}17", "The scanned item", barcode=code)
    assert svc.search(code)[0].sku == f"{SKU}17"


def test_search_can_filter_by_category(svc):
    cats = CategoryService(svc._auth)
    cid = cats.create("ZZCAT Drinks")
    svc.create(f"{SKU}18", "In category", category_id=cid)
    svc.create(f"{SKU}19", "Out of category")
    found = svc.search("", category_id=cid)
    assert [p.sku for p in found] == [f"{SKU}18"]


def test_search_hides_disabled_products_by_default(svc):
    pid = svc.create(f"{SKU}20", "Discontinued")
    svc.deactivate(pid)
    assert not svc.search("Discontinued")
    assert svc.search("Discontinued", active_only=False)


@pytest.mark.slow
def test_barcode_lookup_stays_under_50ms_at_5000_products(svc):
    """Acceptance: the scanner path on a realistic catalogue."""
    rows = []
    for n in range(5000):
        pid_seed = 100000 + n
        rows.append((f"{SKU}B{n}", f"Bulk product {n}", bc.generate_code(pid_seed)))
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.executemany(
            "INSERT INTO products (sku, name, barcode, sale_price) VALUES (%s,%s,%s,9.99)",
            rows)
        cur.close()

    target = rows[4321][2]
    svc.get_by_barcode(target)                      # warm the connection

    timings = []
    for _ in range(20):
        start = time.perf_counter()
        found = svc.get_by_barcode(target)
        timings.append((time.perf_counter() - start) * 1000)
        assert found is not None

    worst = max(timings)
    median = sorted(timings)[len(timings) // 2]
    print(f"\n    5,000 products: median {median:.2f} ms, worst {worst:.2f} ms")
    assert median < 50, f"median lookup {median:.1f} ms exceeds the 50 ms budget"


# ------------------------------------------------------------------- update

def test_update_changes_fields(svc):
    pid = svc.create(f"{SKU}21", "Before", sale_price="10")
    svc.update(pid, name="After", sale_price="12.50")
    p = svc.get(pid)
    assert p.name == "After" and p.sale_price == Decimal("12.50")


def test_update_to_a_taken_sku_is_refused(svc):
    svc.create(f"{SKU}22", "One")
    other = svc.create(f"{SKU}23", "Two")
    with pytest.raises(ValidationError, match="already used"):
        svc.update(other, sku=f"{SKU}22")


def test_generate_barcode_is_idempotent(svc):
    pid = svc.create(f"{SKU}24", "Code me")
    first = svc.generate_barcode(pid)
    assert svc.generate_barcode(pid) == first


def test_deactivate_and_reactivate(svc):
    pid = svc.create(f"{SKU}25", "Toggle")
    svc.deactivate(pid)
    assert svc.get(pid).is_active is False
    svc.activate(pid)
    assert svc.get(pid).is_active is True


def test_delete_an_unused_product(svc):
    pid = svc.create(f"{SKU}26", "Temporary")
    svc.delete(pid)
    assert svc.get(pid) is None


# ---------------------------------------------------------------------- CSV

def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["sku", "name", "barcode", "category", "unit",
                                "purchase_price", "sale_price", "tax_rate",
                                "reorder_level"])
        writer.writeheader()
        writer.writerows(rows)


def test_import_97_of_100_and_report_the_3(svc, tmp_path):
    """Acceptance: 3 bad rows out of 100 -- import 97, report the 3."""
    rows = []
    for n in range(100):
        rows.append({"sku": f"{SKU}C{n}", "name": f"Imported {n}",
                     "sale_price": "25.00", "purchase_price": "20.00",
                     "barcode": "", "category": "", "unit": "pcs",
                     "tax_rate": "0", "reorder_level": "5"})
    rows[10]["sale_price"] = "not a number"
    rows[40]["sku"] = ""
    rows[75]["unit"] = "furlongs"

    path = tmp_path / "products.csv"
    _write_csv(path, rows)

    result = svc.import_csv(path)
    print(f"\n    {result.summary()}")
    assert result.imported == 97
    assert len(result.failed) == 3
    assert {line for line, _ in result.failed} == {12, 42, 77}   # header is line 1
    reasons = " ".join(reason for _, reason in result.failed)
    assert "number" in reasons and "SKU" in reasons and "unit" in reasons
    assert svc.get_by_sku(f"{SKU}C0") is not None, "good rows must survive"
    assert svc.get_by_sku(f"{SKU}C10") is None, "the bad row must not be written"


def test_import_refuses_a_file_without_the_required_columns(svc, tmp_path):
    path = tmp_path / "wrong.csv"
    path.write_text("price,colour\n10,red\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="sku"):
        svc.import_csv(path)


def test_import_resolves_category_and_unit_by_name(svc, tmp_path):
    CategoryService(svc._auth).create("ZZCAT Imported")
    path = tmp_path / "named.csv"
    _write_csv(path, [{"sku": f"{SKU}D1", "name": "Named refs",
                       "category": "ZZCAT Imported", "unit": "kg",
                       "barcode": "", "purchase_price": "1", "sale_price": "2",
                       "tax_rate": "0", "reorder_level": "0"}])
    assert svc.import_csv(path).imported == 1
    p = svc.get_by_sku(f"{SKU}D1")
    assert p.category_id is not None and p.unit_id is not None


def test_import_can_update_existing_rows(svc, tmp_path):
    svc.create(f"{SKU}E1", "Old name", sale_price="5")
    path = tmp_path / "update.csv"
    _write_csv(path, [{"sku": f"{SKU}E1", "name": "New name", "sale_price": "9",
                       "barcode": "", "category": "", "unit": "",
                       "purchase_price": "0", "tax_rate": "0", "reorder_level": "0"}])
    result = svc.import_csv(path, update_existing=True)
    assert result.updated == 1 and result.imported == 0
    assert svc.get_by_sku(f"{SKU}E1").name == "New name"


def test_export_then_import_round_trips(svc, tmp_path):
    svc.create(f"{SKU}F1", "Round trip", sale_price="33.00", unit_id=1)
    out = tmp_path / "export.csv"
    assert svc.export_csv(out) >= 1
    text = out.read_text(encoding="utf-8")
    assert "Round trip" in text and "sku,name,barcode" in text


# ------------------------------------------------------------ authorisation

def test_reads_work_without_a_session(db):
    assert ProductService().search("") is not None


def test_writes_need_a_signed_in_user(db):
    with pytest.raises(ValidationError, match="signed-in user"):
        ProductService().create("ZZPX", "No session")


def test_a_cashier_cannot_edit_products(svc):
    admin = AuthService()
    admin.login("admin", "admin123")
    admin.create_user("zz_prod_cashier", "Counter", "password1", Role.CASHIER)
    cashier = AuthService()
    cashier.login("zz_prod_cashier", "password1")
    restricted = ProductService(cashier)
    assert restricted.search("") is not None            # may look
    with pytest.raises(PermissionDenied):               # may not touch
        restricted.create(f"{SKU}Z1", "Nope")
