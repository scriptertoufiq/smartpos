"""M3 tests: warehouse CRUD and its guards.

Acceptance criteria from docs/PLAN.md:
  * warehouses deactivate, never delete -- historic documents still resolve
  * a duplicate code is refused with a field-level message
  * the seeded MAIN outlet is editable but cannot be closed while it is the
    only active one
"""

from __future__ import annotations

import pytest

from smartpos.core.constants import Role
from smartpos.core.exceptions import DatabaseError, PermissionDenied, ValidationError
from smartpos.db import connection
from smartpos.services.auth_service import AuthService
from smartpos.services.warehouse_service import WarehouseService

PREFIX = "ZZ"


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
        cur.execute("UPDATE users SET warehouse_id = NULL WHERE warehouse_id IN "
                    "(SELECT id FROM warehouses WHERE code LIKE %s)", (PREFIX + "%",))
        cur.execute("DELETE FROM stock WHERE warehouse_id IN "
                    "(SELECT id FROM warehouses WHERE code LIKE %s)", (PREFIX + "%",))
        cur.execute("DELETE FROM warehouses WHERE code LIKE %s", (PREFIX + "%",))
        cur.execute("DELETE FROM users WHERE username LIKE 'zz_%'")
        cur.execute("UPDATE warehouses SET is_active = 1 WHERE code = 'MAIN'")
        cur.close()


@pytest.fixture
def svc(db):
    _purge()
    auth = AuthService()
    auth.login("admin", "admin123")
    yield WarehouseService(auth)
    _purge()


# ------------------------------------------------------------------- create

def test_create_and_read_back(svc):
    wid = svc.create(f"{PREFIX}A", "Godown A", "Back lane", "01700000000")
    w = svc.get(wid)
    assert w.code == f"{PREFIX}A"
    assert w.name == "Godown A"
    assert w.address == "Back lane"
    assert w.is_active


def test_code_is_stored_in_capitals(svc):
    wid = svc.create(f"{PREFIX.lower()}b", "Lowercase Code")
    assert svc.get(wid).code == f"{PREFIX}B"


def test_duplicate_code_is_refused(svc):
    svc.create(f"{PREFIX}C", "First")
    with pytest.raises(ValidationError, match="already used"):
        svc.create(f"{PREFIX}C", "Second")


def test_duplicate_code_is_case_insensitive(svc):
    svc.create(f"{PREFIX}D", "First")
    with pytest.raises(ValidationError, match="already used"):
        svc.create(f"{PREFIX.lower()}d", "Second")


@pytest.mark.parametrize("bad_code", ["", "   ", "HAS SPACE", "X" * 21])
def test_invalid_codes_are_refused(svc, bad_code):
    with pytest.raises(ValidationError):
        svc.create(bad_code, "Somewhere")


def test_blank_name_is_refused(svc):
    with pytest.raises(ValidationError, match="Name is required"):
        svc.create(f"{PREFIX}E", "   ")


def test_a_failed_create_writes_nothing(svc):
    before = len(svc.list_all(active_only=False))
    with pytest.raises(ValidationError):
        svc.create(f"{PREFIX}F", "")
    assert len(svc.list_all(active_only=False)) == before


# --------------------------------------------------------------------- read

def test_list_excludes_closed_when_asked(svc):
    wid = svc.create(f"{PREFIX}G", "To Be Closed")
    svc.deactivate(wid)
    assert f"{PREFIX}G" not in {w.code for w in svc.list_all(active_only=True)}
    assert f"{PREFIX}G" in {w.code for w in svc.list_all(active_only=False)}


def test_label_for_handles_missing_and_none(svc):
    assert svc.label_for(None) == "—"
    assert "missing" in svc.label_for(999999)


# ------------------------------------------------------------------- update

def test_the_seeded_main_outlet_can_be_edited(svc):
    """Acceptance: MAIN is editable."""
    main = next(w for w in svc.list_all(active_only=False) if w.code == "MAIN")
    original = main.name
    try:
        svc.update(main.id, name="Main Outlet (renamed)")
        assert svc.get(main.id).name == "Main Outlet (renamed)"
    finally:
        svc.update(main.id, name=original)


def test_update_to_a_taken_code_is_refused(svc):
    svc.create(f"{PREFIX}H", "One")
    other = svc.create(f"{PREFIX}I", "Two")
    with pytest.raises(ValidationError, match="already used"):
        svc.update(other, code=f"{PREFIX}H")


def test_update_of_a_missing_outlet_is_refused(svc):
    with pytest.raises(ValidationError, match="no longer exists"):
        svc.update(987654, name="Ghost")


# --------------------------------------------------------- close and reopen

def test_close_then_reopen(svc):
    wid = svc.create(f"{PREFIX}J", "Seasonal Stall")
    svc.deactivate(wid)
    assert svc.get(wid).is_active is False
    svc.activate(wid)
    assert svc.get(wid).is_active is True


def test_closing_is_idempotent(svc):
    wid = svc.create(f"{PREFIX}K", "Twice")
    svc.deactivate(wid)
    svc.deactivate(wid)          # must not raise
    assert svc.get(wid).is_active is False


def test_the_only_active_outlet_cannot_be_closed(svc):
    """Acceptance: MAIN cannot be closed while it is the only active one."""
    main = next(w for w in svc.list_all(active_only=True) if w.code == "MAIN")
    assert len(svc.list_all(active_only=True)) == 1, "test needs a single outlet"
    with pytest.raises(ValidationError, match="only active outlet"):
        svc.deactivate(main.id)


def test_an_outlet_holding_stock_cannot_be_closed(svc):
    """Closing it would hide goods that are physically still on its shelves."""
    wid = svc.create(f"{PREFIX}L", "Holds Stock")
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO stock (product_id, warehouse_id, quantity) "
                    "SELECT id, %s, 5 FROM products LIMIT 1", (wid,))
        moved = cur.rowcount
        cur.close()
    if not moved:
        pytest.skip("no products yet -- stock guard is covered once M5 lands")
    with pytest.raises(ValidationError, match="still holds stock"):
        svc.deactivate(wid)


def test_an_outlet_with_zero_stock_rows_can_still_be_closed(svc):
    """A zero-quantity row is not stock; it is just a ledger slot."""
    wid = svc.create(f"{PREFIX}M", "Empty Shelves")
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO stock (product_id, warehouse_id, quantity) "
                    "SELECT id, %s, 0 FROM products LIMIT 1", (wid,))
        cur.close()
    svc.deactivate(wid)
    assert svc.get(wid).is_active is False


def test_usage_reports_what_would_be_affected(svc):
    wid = svc.create(f"{PREFIX}N", "Staffed")
    auth = AuthService()
    auth.login("admin", "admin123")
    auth.create_user("zz_posted", "Posted Person", "password1", Role.CASHIER,
                     warehouse_id=wid)
    assert svc.usage(wid)["users_posted"] == 1


# ------------------------------------------------------------ authorisation

def test_writes_need_a_signed_in_user(db):
    anonymous = WarehouseService()
    assert anonymous.list_all()                       # reads are open
    with pytest.raises(ValidationError, match="signed-in user"):
        anonymous.create("ZZX", "No Session")


def test_a_cashier_cannot_create_an_outlet(svc):
    admin = AuthService()
    admin.login("admin", "admin123")
    admin.create_user("zz_wh_cashier", "Counter", "password1", Role.CASHIER)

    cashier = AuthService()
    cashier.login("zz_wh_cashier", "password1")
    restricted = WarehouseService(cashier)
    assert restricted.list_all()                      # may look
    with pytest.raises(PermissionDenied):             # may not touch
        restricted.create(f"{PREFIX}O", "Nope")
    with pytest.raises(PermissionDenied):
        restricted.deactivate(1)
