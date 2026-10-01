"""M12 tests: backup, restore and shop settings.

Acceptance criteria from docs/PLAN.md:
  * a backup restored into an empty database reproduces every row
  * restore cannot run without a typed confirmation
  * changing the shop name changes the next printed invoice
  * a failed backup reports why (mysqldump missing, no disk space, bad path)

The restore test runs against a throwaway schema, never the development one:
restoring over a live database to prove a point would be exactly the mistake
the confirmation step exists to prevent.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import mysql.connector
import pytest

from smartpos.config import get_settings
from smartpos.core.constants import Role
from smartpos.core.exceptions import DatabaseError, PermissionDenied, ValidationError
from smartpos.db import connection
from smartpos.db.migrations import split_statements
from smartpos.services.auth_service import AuthService
from smartpos.services.backup_service import CONFIRMATION, BackupService
from smartpos.services.product_service import ProductService
from smartpos.services.settings_service import SettingsService
from smartpos.services.supplier_service import SupplierService

MARK = "ZZBAK"
SCRATCH_DB = "smartpos_restore_check"


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
        cur.execute("DELETE FROM products WHERE sku LIKE %s", (MARK + "%",))
        cur.execute("DELETE FROM suppliers WHERE name LIKE %s", (MARK + "%",))
        cur.execute("DELETE FROM users WHERE username LIKE 'zz_%'")
        cur.close()


@pytest.fixture
def ctx(db, tmp_path):
    _purge()
    SettingsService.invalidate()
    auth = AuthService()
    auth.login("admin", "admin123")
    service = BackupService(auth)
    yield {"auth": auth, "backup": service, "settings": SettingsService(auth),
           "products": ProductService(auth), "dir": tmp_path}
    _purge()
    SettingsService.invalidate()


# ------------------------------------------------------------------ settings

def test_defaults_are_present(ctx):
    values = ctx["settings"].all(refresh=True)
    assert values["shop_name"]
    assert values["currency_symbol"]


def test_saving_and_reading_back(ctx):
    settings = ctx["settings"]
    original = settings.get("shop_name")
    try:
        settings.save({"shop_name": f"{MARK} Corner Store",
                       "shop_phone": "01711000000"})
        assert settings.get("shop_name") == f"{MARK} Corner Store"
        assert settings.get("shop_phone") == "01711000000"
    finally:
        settings.save({"shop_name": original, "shop_phone": ""})


def test_an_empty_shop_name_is_refused(ctx):
    with pytest.raises(ValidationError, match="cannot be empty"):
        ctx["settings"].save({"shop_name": "   "})


@pytest.mark.parametrize("bad", ["-1", "101", "not a number"])
def test_a_bad_tax_rate_is_refused(ctx, bad):
    with pytest.raises(ValidationError):
        ctx["settings"].save({"default_tax_rate": bad})


def test_unknown_keys_are_ignored(ctx):
    ctx["settings"].save({"shop_phone": "0180000000", "nonsense_key": "x"})
    assert "nonsense_key" not in ctx["settings"].all(refresh=True)
    ctx["settings"].save({"shop_phone": ""})


def test_a_missing_logo_file_is_refused(ctx):
    with pytest.raises(ValidationError, match="No file at"):
        ctx["settings"].save({"shop_logo": "/no/such/logo.png"})


def test_settings_need_an_admin(ctx):
    ctx["auth"].create_user("zz_set_mgr", "Manager", "password1", Role.MANAGER)
    manager = AuthService()
    manager.login("zz_set_mgr", "password1")
    with pytest.raises(PermissionDenied):
        SettingsService(manager).save({"shop_name": "Nope"})


def test_changing_the_shop_name_changes_the_next_invoice(ctx, tmp_path):
    """Acceptance criterion."""
    from pypdf import PdfReader

    from smartpos.reports.invoice import render_thermal

    settings = ctx["settings"]
    original = settings.get("shop_name")
    sale = {
        "invoice_no": "INV-TEST-000001",
        "sale_date": None, "customer_name": "Walk-in Customer",
        "subtotal": Decimal("100.00"), "discount": Decimal("0.00"),
        "tax_amount": Decimal("0.00"), "grand_total": Decimal("100.00"),
        "paid_amount": Decimal("100.00"), "change_amount": Decimal("0.00"),
        "items": [{"product_name": "Thing", "quantity": Decimal("1.000"),
                   "unit_price": Decimal("100.00"), "discount": Decimal("0.00"),
                   "line_total": Decimal("100.00")}],
    }
    try:
        settings.save({"shop_name": f"{MARK} Renamed Store"})
        path = render_thermal(sale, tmp_path / "after.pdf",
                              shop=settings.shop_profile())
        text = " ".join(p.extract_text() for p in PdfReader(str(path)).pages)
        assert f"{MARK} Renamed Store" in text.replace("\n", " ")
    finally:
        settings.save({"shop_name": original})


# -------------------------------------------------------------------- backup

def test_a_backup_is_written_and_listed(ctx):
    path = ctx["backup"].create_backup(ctx["dir"] / "smartpos_test.sql")
    assert path.is_file() and path.stat().st_size > 0
    text = path.read_text(encoding="utf-8")
    assert "CREATE TABLE" in text
    assert "FOREIGN_KEY_CHECKS" in text


def test_the_backup_holds_every_table_and_the_view(ctx):
    path = ctx["backup"].create_backup(ctx["dir"] / "full.sql")
    text = path.read_text(encoding="utf-8")
    with connection.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE()")
        objects = cur.fetchall()
        cur.close()
    for name, kind in objects:
        marker = "CREATE TABLE" if kind == "BASE TABLE" else "CREATE"
        assert f"`{name}`" in text, f"{kind.lower()} {name} is missing from the dump"
        assert marker in text


def test_rows_are_written_as_inserts(ctx):
    ctx["products"].create(f"{MARK}-1", "Backup Me", sale_price="12.34")
    path = ctx["backup"].create_backup(ctx["dir"] / "withrows.sql")
    text = path.read_text(encoding="utf-8")
    assert "INSERT INTO `products`" in text
    assert "Backup Me" in text


def test_awkward_characters_survive_the_dump(ctx):
    """A quote or a backslash in a product name must not break the file."""
    tricky = "O'Brien \\ \"Special\" 50%"
    ctx["products"].create(f"{MARK}-2", tricky, sale_price="1.00")
    path = ctx["backup"].create_backup(ctx["dir"] / "tricky.sql")
    statements = split_statements(path.read_text(encoding="utf-8"))
    assert any("O\\'Brien" in s for s in statements), "the quote was not escaped"


def test_a_bad_destination_folder_reports_why(ctx):
    """Acceptance: a failed backup says what went wrong."""
    with pytest.raises(ValidationError, match="Cannot write to"):
        ctx["backup"].create_backup(Path("/no/such/place/backup.sql"))


def test_backups_are_listed_newest_first(ctx, monkeypatch):
    monkeypatch.setattr(ctx["backup"], "backup_dir", lambda: ctx["dir"])
    first = ctx["backup"].create_backup(ctx["dir"] / "smartpos_20200101_000000.sql")
    second = ctx["backup"].create_backup(ctx["dir"] / "smartpos_20300101_000000.sql")
    import os
    os.utime(first, (1_600_000_000, 1_600_000_000))
    os.utime(second, (1_700_000_000, 1_700_000_000))
    listed = ctx["backup"].list_backups()
    assert [f["name"] for f in listed][:2] == [second.name, first.name]


def test_backup_needs_an_admin(ctx):
    ctx["auth"].create_user("zz_bak_mgr", "Manager", "password1", Role.MANAGER)
    manager = AuthService()
    manager.login("zz_bak_mgr", "password1")
    with pytest.raises(PermissionDenied):
        BackupService(manager).create_backup(ctx["dir"] / "nope.sql")


# ------------------------------------------------------------------- restore

def test_restore_refuses_without_the_typed_confirmation(ctx):
    """Acceptance criterion."""
    path = ctx["backup"].create_backup(ctx["dir"] / "guard.sql")
    for attempt in ("", "yes", "confirm", "Confirm"):
        with pytest.raises(ValidationError, match="Type CONFIRM"):
            ctx["backup"].restore_backup(path, attempt)


def test_restore_refuses_a_missing_file(ctx):
    with pytest.raises(ValidationError, match="No such backup file"):
        ctx["backup"].restore_backup(ctx["dir"] / "ghost.sql", CONFIRMATION)


def test_restore_refuses_a_file_that_is_not_a_backup(ctx):
    path = ctx["dir"] / "shopping-list.sql"
    path.write_text("milk, bread, rice\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="does not look like"):
        ctx["backup"].restore_backup(path, CONFIRMATION)


def test_a_backup_restored_into_an_empty_database_reproduces_every_row(ctx):
    """Acceptance criterion.

    Restored into a throwaway schema, not the development one: proving a
    restore works by overwriting live data would be the very mistake the
    confirmation step guards against.
    """
    ctx["products"].create(f"{MARK}-R1", "Restore Me", sale_price="99.50")
    SupplierService(ctx["auth"]).create(f"{MARK} Restored Supplier")

    # Counted before the dump: create_backup writes its own activity_log row
    # once the file is closed, so the live table ends up one ahead of it.
    with connection.get_connection() as live:
        counter = live.cursor()
        counter.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' "
            "ORDER BY TABLE_NAME")
        tables = [r[0] for r in counter.fetchall()]
        expected_counts = {}
        for table in tables:
            counter.execute(f"SELECT COUNT(*) FROM `{table}`")
            expected_counts[table] = counter.fetchone()[0]
        counter.close()

    dump = ctx["backup"].create_backup(ctx["dir"] / "to_restore.sql")

    settings = get_settings().database
    scratch = mysql.connector.connect(
        host=settings.host, port=settings.port,
        user=settings.user, password=settings.password)
    cursor = scratch.cursor()
    try:
        cursor.execute(f"DROP DATABASE IF EXISTS {SCRATCH_DB}")
        cursor.execute(f"CREATE DATABASE {SCRATCH_DB} "
                       "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        cursor.execute(f"USE {SCRATCH_DB}")

        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        for statement in split_statements(dump.read_text(encoding="utf-8")):
            cursor.execute(statement)
            if cursor.with_rows:
                cursor.fetchall()
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        scratch.commit()

        # Every table must hold exactly what it held when the dump was taken.
        mismatches = []
        for table, expected in expected_counts.items():
            cursor.execute(f"SELECT COUNT(*) FROM `{SCRATCH_DB}`.`{table}`")
            actual = cursor.fetchone()[0]
            if expected != actual:
                mismatches.append(f"{table}: {expected} -> {actual}")

        assert not mismatches, "row counts differ after restore: " + ", ".join(mismatches)
        assert len(tables) == 22, f"expected 22 tables, restored {len(tables)}"

        # And the values, not just the counts.
        cursor.execute(f"SELECT name, sale_price FROM `{SCRATCH_DB}`.products "
                       "WHERE sku = %s", (f"{MARK}-R1",))
        row = cursor.fetchone()
        assert row is not None, "the product did not survive the restore"
        assert row[0] == "Restore Me"
        assert Decimal(str(row[1])) == Decimal("99.50")

        cursor.execute(f"SELECT COUNT(*) FROM `{SCRATCH_DB}`.suppliers "
                       "WHERE name = %s", (f"{MARK} Restored Supplier",))
        assert cursor.fetchone()[0] == 1

        # The view has to come back too, and still resolve.
        cursor.execute(f"SELECT COUNT(*) FROM `{SCRATCH_DB}`.v_cash_movements")
        cursor.fetchone()
    finally:
        cursor.execute(f"DROP DATABASE IF EXISTS {SCRATCH_DB}")
        cursor.close()
        scratch.close()


def test_the_dump_replays_cleanly_twice(ctx):
    """A dump has to be re-runnable: DROP first, then CREATE."""
    dump = ctx["backup"].create_backup(ctx["dir"] / "twice.sql")
    text = dump.read_text(encoding="utf-8")
    assert text.count("DROP TABLE IF EXISTS `products`") == 1
    assert "DROP VIEW IF EXISTS `v_cash_movements`" in text
