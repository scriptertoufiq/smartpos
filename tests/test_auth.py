"""M1 tests: login, permissions and admin-only user CRUD.

Every test that writes creates accounts with a `zz_` prefix and removes them
afterwards, so the suite never disturbs the real `admin` row.
"""

from __future__ import annotations

import pytest

from smartpos.core.constants import Role
from smartpos.core.exceptions import (
    AuthenticationError,
    DatabaseError,
    PermissionDenied,
    ValidationError,
)
from smartpos.core.security import (
    has_permission,
    hash_password,
    needs_rehash,
    verify_password,
)
from smartpos.db import connection
from smartpos.services.auth_service import AuthService

TEST_PREFIX = "zz_"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"


# --------------------------------------------------------------------- setup

@pytest.fixture(scope="module")
def db():
    try:
        connection.init_pool()
        connection.check_connection()
    except DatabaseError as exc:
        pytest.skip(f"MySQL not reachable: {exc}")
    yield
    connection.close_pool()


def _purge_test_users() -> None:
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM activity_log WHERE entity_id IN "
                    "(SELECT id FROM users WHERE username LIKE %s)", (TEST_PREFIX + "%",))
        cur.execute("DELETE FROM users WHERE username LIKE %s", (TEST_PREFIX + "%",))
        cur.close()


@pytest.fixture
def auth(db):
    """An AuthService already signed in as the seeded administrator."""
    _purge_test_users()
    service = AuthService()
    service.login(ADMIN_USERNAME, ADMIN_PASSWORD)
    yield service
    _purge_test_users()


# ------------------------------------------------------------------ hashing

def test_hash_and_verify_round_trip():
    hashed = hash_password("correct horse")
    assert hashed != "correct horse"
    assert verify_password("correct horse", hashed)
    assert not verify_password("wrong horse", hashed)


def test_same_password_hashes_differently_each_time():
    """Per-password salt: two identical passwords must not share a hash."""
    assert hash_password("repeated") != hash_password("repeated")


def test_verify_never_raises_on_a_corrupt_hash():
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert verify_password("anything", "") is False


def test_short_password_is_rejected():
    with pytest.raises(ValidationError, match="at least"):
        hash_password("abc")


def test_overlong_password_is_rejected():
    """bcrypt ignores bytes past 72; silently truncating would let two
    different passwords open the same account."""
    with pytest.raises(ValidationError, match="too long"):
        hash_password("x" * 100)


def test_needs_rehash_detects_a_weaker_cost():
    assert needs_rehash("$2b$04$" + "x" * 53) is True
    assert needs_rehash(hash_password("strong enough")) is False


# -------------------------------------------------------------- permissions

def test_admin_holds_every_permission():
    assert has_permission(Role.ADMIN, "user.manage")
    assert has_permission(Role.ADMIN, "anything.at.all")


def test_manager_cannot_manage_users():
    assert has_permission(Role.MANAGER, "purchase.manage")
    assert not has_permission(Role.MANAGER, "user.manage")


def test_cashier_can_sell_but_not_much_else():
    assert has_permission(Role.CASHIER, "sale.create")
    assert not has_permission(Role.CASHIER, "stock.manage")
    assert not has_permission(Role.CASHIER, "user.manage")


# -------------------------------------------------------------------- login

def test_admin_can_sign_in(db):
    service = AuthService()
    user = service.login(ADMIN_USERNAME, ADMIN_PASSWORD)
    assert user.username == ADMIN_USERNAME
    assert user.role is Role.ADMIN
    assert service.is_signed_in()


def test_wrong_password_is_rejected(db):
    with pytest.raises(AuthenticationError):
        AuthService().login(ADMIN_USERNAME, "not the password")


def test_unknown_user_and_wrong_password_give_the_same_message(db):
    """Different messages would hand out a list of valid usernames."""
    service = AuthService()
    with pytest.raises(AuthenticationError) as no_such:
        service.login("zz_nobody_at_all", "whatever")
    with pytest.raises(AuthenticationError) as bad_pass:
        service.login(ADMIN_USERNAME, "whatever")
    assert str(no_such.value) == str(bad_pass.value)


def test_login_records_last_login_and_an_audit_row(auth):
    user = auth.current_user()
    with connection.get_connection() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT last_login_at FROM users WHERE id = %s", (user.id,))
        assert cur.fetchone()["last_login_at"] is not None
        cur.execute(
            "SELECT COUNT(*) AS n FROM activity_log WHERE user_id = %s AND action = 'user.login'",
            (user.id,))
        assert cur.fetchone()["n"] >= 1
        cur.close()


def test_disabled_account_cannot_sign_in(auth):
    uid = auth.create_user(f"{TEST_PREFIX}disabled", "Disabled Person",
                           "password1", Role.CASHIER)
    auth.deactivate_user(uid)
    with pytest.raises(AuthenticationError, match="disabled"):
        AuthService().login(f"{TEST_PREFIX}disabled", "password1")


# ------------------------------------------------------- authorisation gate

def test_user_crud_requires_a_signed_in_user(db):
    service = AuthService()
    with pytest.raises(PermissionDenied, match="sign in"):
        service.list_users()


def test_cashier_cannot_touch_user_management(auth):
    auth.create_user(f"{TEST_PREFIX}cashier", "Counter Staff", "password1", Role.CASHIER)
    cashier = AuthService()
    cashier.login(f"{TEST_PREFIX}cashier", "password1")
    for call in (
        lambda: cashier.list_users(),
        lambda: cashier.create_user(f"{TEST_PREFIX}x", "X", "password1", Role.CASHIER),
        lambda: cashier.deactivate_user(1),
    ):
        with pytest.raises(PermissionDenied):
            call()


def test_manager_cannot_touch_user_management(auth):
    auth.create_user(f"{TEST_PREFIX}manager", "Shop Manager", "password1", Role.MANAGER)
    manager = AuthService()
    manager.login(f"{TEST_PREFIX}manager", "password1")
    with pytest.raises(PermissionDenied):
        manager.list_users()


# ------------------------------------------------------------------- create

def test_create_and_read_back(auth):
    uid = auth.create_user(f"{TEST_PREFIX}newbie", "New Person", "password1",
                           Role.CASHIER, warehouse_id=1)
    user = auth.get_user(uid)
    assert user.username == f"{TEST_PREFIX}newbie"
    assert user.full_name == "New Person"
    assert user.role is Role.CASHIER
    assert user.warehouse_id == 1
    assert user.is_active
    assert user.password_hash != "password1"
    assert verify_password("password1", user.password_hash)


def test_the_new_user_can_sign_in(auth):
    auth.create_user(f"{TEST_PREFIX}signin", "Sign In", "password1", Role.CASHIER)
    signed = AuthService().login(f"{TEST_PREFIX}signin", "password1")
    assert signed.role is Role.CASHIER


def test_duplicate_username_is_refused(auth):
    auth.create_user(f"{TEST_PREFIX}dupe", "First", "password1", Role.CASHIER)
    with pytest.raises(ValidationError, match="already taken"):
        auth.create_user(f"{TEST_PREFIX}dupe", "Second", "password1", Role.CASHIER)


@pytest.mark.parametrize("bad", ["", "ab", "has space", "x" * 51])
def test_invalid_usernames_are_refused(auth, bad):
    with pytest.raises(ValidationError):
        auth.create_user(bad, "Someone", "password1", Role.CASHIER)


def test_blank_full_name_is_refused(auth):
    with pytest.raises(ValidationError, match="Full name"):
        auth.create_user(f"{TEST_PREFIX}noname", "   ", "password1", Role.CASHIER)


def test_a_failed_create_writes_nothing(auth):
    before = len(auth.list_users())
    with pytest.raises(ValidationError):
        auth.create_user(f"{TEST_PREFIX}bad", "Person", "short", Role.CASHIER)
    assert len(auth.list_users()) == before


# --------------------------------------------------------------------- read

def test_list_and_filter(auth):
    auth.create_user(f"{TEST_PREFIX}alpha", "Alpha", "password1", Role.CASHIER)
    auth.create_user(f"{TEST_PREFIX}beta", "Beta", "password1", Role.MANAGER)

    everyone = auth.list_users()
    names = {u.username for u in everyone}
    assert {f"{TEST_PREFIX}alpha", f"{TEST_PREFIX}beta", ADMIN_USERNAME} <= names

    managers = auth.list_users(role=Role.MANAGER)
    assert all(u.role is Role.MANAGER for u in managers)

    found = auth.list_users(search="alpha")
    assert [u.username for u in found] == [f"{TEST_PREFIX}alpha"]


def test_list_can_exclude_disabled_accounts(auth):
    uid = auth.create_user(f"{TEST_PREFIX}gone", "Gone", "password1", Role.CASHIER)
    auth.deactivate_user(uid)
    active = {u.username for u in auth.list_users(include_inactive=False)}
    assert f"{TEST_PREFIX}gone" not in active
    assert f"{TEST_PREFIX}gone" in {u.username for u in auth.list_users()}


# ------------------------------------------------------------------- update

def test_update_name_role_and_username(auth):
    uid = auth.create_user(f"{TEST_PREFIX}edit", "Before", "password1", Role.CASHIER)
    auth.update_user(uid, full_name="After", role=Role.MANAGER,
                     username=f"{TEST_PREFIX}edited")
    user = auth.get_user(uid)
    assert user.full_name == "After"
    assert user.role is Role.MANAGER
    assert user.username == f"{TEST_PREFIX}edited"


def test_update_to_a_taken_username_is_refused(auth):
    auth.create_user(f"{TEST_PREFIX}one", "One", "password1", Role.CASHIER)
    uid = auth.create_user(f"{TEST_PREFIX}two", "Two", "password1", Role.CASHIER)
    with pytest.raises(ValidationError, match="already taken"):
        auth.update_user(uid, username=f"{TEST_PREFIX}one")


def test_update_of_a_missing_user_is_refused(auth):
    with pytest.raises(ValidationError, match="no longer exists"):
        auth.update_user(987654, full_name="Ghost")


# --------------------------------------------------- deactivate and delete

def test_deactivate_then_reactivate(auth):
    uid = auth.create_user(f"{TEST_PREFIX}toggle", "Toggle", "password1", Role.CASHIER)
    auth.deactivate_user(uid)
    assert auth.get_user(uid).is_active is False
    auth.activate_user(uid)
    assert auth.get_user(uid).is_active is True


def test_cannot_disable_your_own_account(auth):
    with pytest.raises(ValidationError, match="your own"):
        auth.deactivate_user(auth.current_user().id)


def test_cannot_delete_your_own_account(auth):
    with pytest.raises(ValidationError, match="your own"):
        auth.delete_user(auth.current_user().id)


def test_delete_removes_an_unused_account(auth):
    uid = auth.create_user(f"{TEST_PREFIX}temp", "Temporary", "password1", Role.CASHIER)
    auth.delete_user(uid)
    assert auth.get_user(uid) is None


def test_the_last_active_admin_cannot_be_disabled(auth):
    """The lockout guard: there must always be a way back in."""
    admin_id = auth.current_user().id
    other = auth.create_user(f"{TEST_PREFIX}admin2", "Second Admin", "password1",
                             Role.ADMIN)
    second = AuthService()
    second.login(f"{TEST_PREFIX}admin2", "password1")

    # Two admins exist, so disabling one is allowed.
    second.deactivate_user(admin_id)
    # Now only this one remains, and it cannot remove itself either way.
    with pytest.raises(ValidationError, match="only active administrator"):
        second.update_user(other, role=Role.CASHIER)

    # restore the seeded admin for the rest of the suite
    second.activate_user(admin_id)


def test_the_last_admin_cannot_be_demoted(auth):
    admin_id = auth.current_user().id
    with pytest.raises(ValidationError, match="only active administrator"):
        auth.update_user(admin_id, role=Role.MANAGER)


# ---------------------------------------------------------------- passwords

def test_change_own_password(auth):
    uid = auth.create_user(f"{TEST_PREFIX}pw", "Password Person", "password1",
                           Role.CASHIER)
    person = AuthService()
    person.login(f"{TEST_PREFIX}pw", "password1")
    person.change_password(uid, "password1", "password2")

    with pytest.raises(AuthenticationError):
        AuthService().login(f"{TEST_PREFIX}pw", "password1")
    assert AuthService().login(f"{TEST_PREFIX}pw", "password2").id == uid


def test_change_password_needs_the_old_one(auth):
    uid = auth.create_user(f"{TEST_PREFIX}pw2", "Person", "password1", Role.CASHIER)
    person = AuthService()
    person.login(f"{TEST_PREFIX}pw2", "password1")
    with pytest.raises(AuthenticationError, match="not correct"):
        person.change_password(uid, "the wrong old one", "password2")


def test_cannot_change_someone_elses_password(auth):
    victim = auth.create_user(f"{TEST_PREFIX}victim", "Victim", "password1", Role.CASHIER)
    auth.create_user(f"{TEST_PREFIX}attacker", "Attacker", "password1", Role.CASHIER)
    attacker = AuthService()
    attacker.login(f"{TEST_PREFIX}attacker", "password1")
    with pytest.raises(PermissionDenied):
        attacker.change_password(victim, "password1", "hijacked1")


def test_admin_can_reset_a_forgotten_password(auth):
    uid = auth.create_user(f"{TEST_PREFIX}forgot", "Forgetful", "password1", Role.CASHIER)
    auth.reset_password(uid, "brandnew1")
    assert AuthService().login(f"{TEST_PREFIX}forgot", "brandnew1").id == uid


def test_new_password_must_differ_from_the_old(auth):
    uid = auth.create_user(f"{TEST_PREFIX}same", "Same", "password1", Role.CASHIER)
    person = AuthService()
    person.login(f"{TEST_PREFIX}same", "password1")
    with pytest.raises(ValidationError, match="different"):
        person.change_password(uid, "password1", "password1")


# ------------------------------------------------------------- audit trail

def test_every_change_is_recorded(auth):
    uid = auth.create_user(f"{TEST_PREFIX}audited", "Audited", "password1", Role.CASHIER)
    auth.update_user(uid, full_name="Audited Again")
    auth.reset_password(uid, "password2")
    auth.deactivate_user(uid)

    with connection.get_connection() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT action FROM activity_log WHERE entity = 'users' AND entity_id = %s",
            (uid,))
        actions = {r["action"] for r in cur.fetchall()}
        cur.close()
    assert {"user.create", "user.update", "user.reset_password",
            "user.deactivate"} <= actions
