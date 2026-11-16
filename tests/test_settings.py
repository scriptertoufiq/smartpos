"""M12 tests: the settings table and the shop profile.

`SettingsService` was the one service with no test file of its own -- it was
exercised only incidentally, through the backup tests. Everything it promises is
checked here: validation, the per-session cache, the shop block an invoice
prints, and the fact that an unknown key cannot be written from the UI.

Every test restores whatever it changed, because these are the live shop's real
settings, not a fixture's.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from smartpos.core.exceptions import DatabaseError, PermissionDenied, ValidationError
from smartpos.db import connection
from smartpos.services.auth_service import AuthService
from smartpos.services.settings_service import FIELDS, SettingsService


@pytest.fixture(scope="module")
def db():
    try:
        connection.init_pool()
        connection.check_connection()
    except DatabaseError as exc:
        pytest.skip(f"MySQL not reachable: {exc}")
    yield
    connection.close_pool()


@pytest.fixture
def svc(db):
    """A settings service, with the shop's real values put back afterwards."""
    auth = AuthService()
    auth.login("admin", "admin123")
    service = SettingsService(auth)
    original = service.all(refresh=True)
    yield service
    service.save({key: original.get(key, "") for key in FIELDS})
    SettingsService.invalidate()


def _purge_test_users() -> None:
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE username LIKE 'zzset_%'")
        cur.close()


# ------------------------------------------------------------------- reading

def test_every_field_has_a_value(svc):
    """A missing row falls back to its default rather than disappearing."""
    values = svc.all(refresh=True)
    for key, (_label, default) in FIELDS.items():
        assert key in values, f"{key} is missing from all()"
        assert values[key] is not None


def test_get_falls_back_to_the_given_default(svc):
    assert svc.get("no_such_setting", "fallback") == "fallback"


def test_the_shop_profile_carries_what_an_invoice_prints(svc):
    svc.save({"shop_name": "ZZ Test Emporium", "shop_phone": "01999999999",
              "currency_symbol": "BDT", "invoice_footer": "ZZ thank you"})
    profile = svc.shop_profile()
    assert profile["name"] == "ZZ Test Emporium"
    assert profile["phone"] == "01999999999"
    assert profile["currency"] == "BDT"
    assert profile["footer"] == "ZZ thank you"
    # The keys the invoice renderer asks for, all present.
    assert {"name", "address", "phone", "currency", "footer", "logo"} <= set(profile)


def test_the_shop_name_never_comes_back_empty(svc):
    """Even with nothing stored, an invoice has something to print."""
    assert svc.shop_profile()["name"]


# ---------------------------------------------------------------- the cache

def test_values_are_cached_within_a_session(svc):
    svc.save({"shop_phone": "01700000001"})
    first = svc.all()
    # Change the row underneath the service, without going through it.
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE settings SET setting_value = %s WHERE setting_key = %s",
                    ("01700000002", "shop_phone"))
        cur.close()
    assert svc.all()["shop_phone"] == first["shop_phone"], "the cache was bypassed"
    assert svc.all(refresh=True)["shop_phone"] == "01700000002"


def test_invalidate_drops_the_cache(svc):
    svc.all(refresh=True)
    SettingsService.invalidate()
    assert svc.all()["shop_name"]          # refills without raising


def test_saving_refreshes_the_cache(svc):
    svc.save({"shop_name": "ZZ Fresh Name"})
    assert svc.all()["shop_name"] == "ZZ Fresh Name"


# ------------------------------------------------------------- the writes

def test_an_unknown_key_is_ignored(svc):
    """The UI cannot invent a setting; only FIELDS may be written."""
    svc.save({"shop_name": "ZZ Known", "totally_made_up": "nonsense"})
    assert "totally_made_up" not in svc.all(refresh=True)


def test_saving_nothing_known_is_a_no_op(svc):
    before = svc.all(refresh=True)
    svc.save({"not_a_field": "x"})
    assert svc.all(refresh=True) == before


def test_a_partial_save_leaves_the_other_fields_alone(svc):
    svc.save({"shop_name": "ZZ Keeper", "shop_phone": "01711111111"})
    svc.save({"shop_phone": "01722222222"})
    values = svc.all(refresh=True)
    assert values["shop_name"] == "ZZ Keeper"
    assert values["shop_phone"] == "01722222222"


# -------------------------------------------------------------- validation

def test_the_shop_name_cannot_be_blanked(svc):
    with pytest.raises(ValidationError, match="shop name"):
        svc.save({"shop_name": "   "})


def test_a_currency_symbol_is_required(svc):
    with pytest.raises(ValidationError, match="currency symbol is required"):
        svc.save({"currency_symbol": ""})


def test_a_currency_symbol_has_a_length_limit(svc):
    with pytest.raises(ValidationError, match="exceed 8"):
        svc.save({"currency_symbol": "X" * 9})


@pytest.mark.parametrize("bad", ["abc", "-1", "101"])
def test_a_bad_tax_rate_is_refused(svc, bad):
    with pytest.raises(ValidationError):
        svc.save({"default_tax_rate": bad})


def test_a_tax_rate_is_stored_to_two_places(svc):
    svc.save({"default_tax_rate": "7.5"})
    assert svc.all(refresh=True)["default_tax_rate"] == "7.50"
    assert svc.default_tax_rate() == Decimal("7.50")


def test_the_tax_rate_boundaries_are_allowed(svc):
    for rate in ("0", "100"):
        svc.save({"default_tax_rate": rate})
    assert svc.default_tax_rate() == Decimal("100.00")


def test_a_broken_stored_tax_rate_does_not_crash_the_counter(svc):
    """A hand-edited row must not take the till down."""
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE settings SET setting_value = 'rubbish' "
                    "WHERE setting_key = %s", ("default_tax_rate",))
        cur.close()
    SettingsService.invalidate()
    assert svc.default_tax_rate() == Decimal("0")


@pytest.mark.parametrize("bad", ["ten", "-5", "1.5"])
def test_a_bad_idle_lock_is_refused(svc, bad):
    with pytest.raises(ValidationError, match="whole number of minutes"):
        svc.save({"idle_lock_minutes": bad})


def test_a_logo_path_must_point_at_a_real_file(svc):
    with pytest.raises(ValidationError, match="No file at"):
        svc.save({"shop_logo": "/no/such/logo.png"})


def test_a_logo_path_that_exists_is_accepted(svc, tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"not really a png, but it is a file")
    svc.save({"shop_logo": str(logo)})
    assert svc.all(refresh=True)["shop_logo"] == str(logo)


def test_clearing_the_logo_is_allowed(svc):
    svc.save({"shop_logo": ""})
    assert svc.all(refresh=True)["shop_logo"] == ""


# ----------------------------------------------------------- authorisation

def test_writing_needs_a_signed_in_user(db):
    anonymous = SettingsService()
    assert anonymous.all()                      # reads are open
    with pytest.raises(ValidationError, match="signed-in user"):
        anonymous.save({"shop_name": "Nope"})


def test_a_cashier_cannot_change_the_settings(db):
    from smartpos.core.constants import Role

    _purge_test_users()
    admin = AuthService()
    admin.login("admin", "admin123")
    admin.create_user("zzset_cashier", "Counter", "password1", Role.CASHIER)
    try:
        cashier = AuthService()
        cashier.login("zzset_cashier", "password1")
        restricted = SettingsService(cashier)
        assert restricted.all()                 # may look
        with pytest.raises(PermissionDenied):   # may not touch
            restricted.save({"shop_name": "Cashier's Shop"})
    finally:
        _purge_test_users()


# ----------------------------------------------------- the rest of the app

def test_the_currency_symbol_reaches_an_invoice(svc, tmp_path):
    """The setting is not just stored -- it prints."""
    from smartpos.reports.invoice import render_thermal
    from pypdf import PdfReader

    svc.save({"shop_name": "ZZ Printed Shop", "currency_symbol": "QQ"})
    sale = {
        "invoice_no": "ZZ-INV-1", "sale_date": None, "customer_name": "Walk-in",
        "subtotal": Decimal("100.00"), "discount": Decimal("0.00"),
        "tax_amount": Decimal("0.00"), "grand_total": Decimal("100.00"),
        "paid_amount": Decimal("100.00"), "change_amount": Decimal("0.00"),
        "items": [{"product_name": "Test Item", "quantity": Decimal("1"),
                   "unit_price": Decimal("100.00"), "discount": Decimal("0.00"),
                   "line_total": Decimal("100.00")}],
    }
    path = render_thermal(sale, Path(tmp_path) / "receipt.pdf",
                          shop=svc.shop_profile())
    text = "".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    assert "ZZ Printed Shop" in text
    assert "QQ" in text, "the currency symbol did not reach the receipt"
