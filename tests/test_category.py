"""M4 tests: category CRUD, the tree, and its two structural rules.

Acceptance criteria from docs/PLAN.md:
  * a category in use cannot be deleted; the message names the blocking products
  * a category cannot become its own ancestor
  * sub-categories display nested
"""

from __future__ import annotations

import pytest

from smartpos.core.constants import Role
from smartpos.core.exceptions import DatabaseError, PermissionDenied, ValidationError
from smartpos.db import connection
from smartpos.services.auth_service import AuthService
from smartpos.services.category_service import CategoryService

PREFIX = "ZZCAT "


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
        cur.execute("DELETE FROM products WHERE sku LIKE 'ZZP%'")
        # children first: the self FK is ON DELETE SET NULL, not CASCADE
        cur.execute("UPDATE categories SET parent_id = NULL WHERE name LIKE %s",
                    (PREFIX + "%",))
        cur.execute("DELETE FROM categories WHERE name LIKE %s", (PREFIX + "%",))
        cur.execute("DELETE FROM users WHERE username LIKE 'zz_%'")
        cur.close()


@pytest.fixture
def svc(db):
    _purge()
    auth = AuthService()
    auth.login("admin", "admin123")
    yield CategoryService(auth)
    _purge()


def _add_product(category_id: int, sku: str, name: str) -> None:
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO products (sku, name, category_id, sale_price) "
            "VALUES (%s, %s, %s, 10.00)", (sku, name, category_id))
        cur.close()


# ------------------------------------------------------------------- create

def test_create_top_level_and_sub(svc):
    parent = svc.create(f"{PREFIX}Grocery")
    child = svc.create(f"{PREFIX}Rice", parent)
    assert svc.get(child).parent_id == parent
    assert svc.get(parent).parent_id is None


def test_name_is_whitespace_normalised(svc):
    cid = svc.create(f"{PREFIX}  Spaced   Out  ")
    assert svc.get(cid).name == f"{PREFIX.strip()} Spaced Out"


def test_duplicate_name_under_the_same_parent_is_refused(svc):
    parent = svc.create(f"{PREFIX}Drinks")
    svc.create(f"{PREFIX}Cola", parent)
    with pytest.raises(ValidationError, match="already exists"):
        svc.create(f"{PREFIX}Cola", parent)


def test_the_same_name_under_different_parents_is_allowed(svc):
    """'Rice' under Grocery and under Wholesale are different things."""
    a = svc.create(f"{PREFIX}Grocery")
    b = svc.create(f"{PREFIX}Wholesale")
    svc.create(f"{PREFIX}Rice", a)
    svc.create(f"{PREFIX}Rice", b)          # must not raise


def test_blank_name_is_refused(svc):
    with pytest.raises(ValidationError, match="Name is required"):
        svc.create("   ")


def test_three_levels_deep_is_refused(svc):
    """The proposal promises one level of sub-category."""
    parent = svc.create(f"{PREFIX}Level1")
    child = svc.create(f"{PREFIX}Level2", parent)
    with pytest.raises(ValidationError, match="two levels|2 levels"):
        svc.create(f"{PREFIX}Level3", child)


# --------------------------------------------------------------------- tree

def test_tree_nests_children_under_parents(svc):
    """Acceptance: sub-categories display nested."""
    parent = svc.create(f"{PREFIX}Grocery")
    svc.create(f"{PREFIX}Rice", parent)
    svc.create(f"{PREFIX}Oil", parent)

    node = next(n for n in svc.tree() if n.category.id == parent)
    assert node.depth == 1
    assert {c.category.name for c in node.children} == {f"{PREFIX}Rice", f"{PREFIX}Oil"}
    assert all(c.depth == 2 for c in node.children)


def test_tree_sorts_siblings_by_name(svc):
    parent = svc.create(f"{PREFIX}Sorted")
    for name in ("Zebra", "Apple", "Mango"):
        svc.create(f"{PREFIX}{name}", parent)
    node = next(n for n in svc.tree() if n.category.id == parent)
    assert [c.category.name for c in node.children] == [
        f"{PREFIX}Apple", f"{PREFIX}Mango", f"{PREFIX}Zebra"]


def test_tree_counts_products(svc):
    cid = svc.create(f"{PREFIX}Counted")
    _add_product(cid, "ZZP1", "Thing One")
    _add_product(cid, "ZZP2", "Thing Two")
    node = next(n for n in svc.tree(with_counts=True) if n.category.id == cid)
    assert node.product_count == 2


def test_the_parent_fk_makes_a_dangling_parent_impossible(svc):
    """A category can never point at a parent that does not exist."""
    cid = svc.create(f"{PREFIX}Anchored")
    import mysql.connector
    with pytest.raises(mysql.connector.Error):
        with connection.transaction() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE categories SET parent_id = 99999 WHERE id = %s", (cid,))
            cur.close()


def test_a_child_of_a_disabled_parent_still_appears(svc):
    """Filtering to active categories must not hide an active child whose
    parent happens to be disabled -- it surfaces at the root instead."""
    parent = svc.create(f"{PREFIX}DisabledParent")
    child = svc.create(f"{PREFIX}VisibleChild", parent)
    svc.deactivate(parent)

    visible = svc.tree(active_only=True)
    assert parent not in {n.category.id for n in visible}
    assert child in {n.category.id for n in visible}, "the child vanished with its parent"


def test_flat_tree_carries_depth(svc):
    parent = svc.create(f"{PREFIX}Flat")
    child = svc.create(f"{PREFIX}Nested", parent)
    depths = {c.id: d for c, d in svc.flat_tree()}
    assert depths[parent] == 1 and depths[child] == 2


# ------------------------------------------------------- moving and cycles

def test_a_category_cannot_be_its_own_parent(svc):
    """Acceptance: a category cannot become its own ancestor."""
    cid = svc.create(f"{PREFIX}Selfish")
    with pytest.raises(ValidationError, match="its own parent"):
        svc.update(cid, parent_id=cid, reparent=True)


def test_a_category_cannot_move_inside_its_own_child(svc):
    parent = svc.create(f"{PREFIX}Parent")
    child = svc.create(f"{PREFIX}Child", parent)
    with pytest.raises(ValidationError, match="its own"):
        svc.update(parent, parent_id=child, reparent=True)


def test_a_category_with_children_cannot_become_a_sub_category(svc):
    parent = svc.create(f"{PREFIX}HasKids")
    svc.create(f"{PREFIX}Kid", parent)
    other = svc.create(f"{PREFIX}Elsewhere")
    with pytest.raises(ValidationError, match="sub-categories of its own"):
        svc.update(parent, parent_id=other, reparent=True)


def test_moving_to_the_top_level(svc):
    parent = svc.create(f"{PREFIX}Origin")
    child = svc.create(f"{PREFIX}Moving", parent)
    svc.update(child, parent_id=None, reparent=True)
    assert svc.get(child).parent_id is None


def test_rename_without_reparent_leaves_the_parent_alone(svc):
    parent = svc.create(f"{PREFIX}Keep")
    child = svc.create(f"{PREFIX}Before", parent)
    svc.update(child, name=f"{PREFIX}After")
    after = svc.get(child)
    assert after.name == f"{PREFIX}After"
    assert after.parent_id == parent


# ------------------------------------------------------------------- delete

def test_delete_an_unused_category(svc):
    cid = svc.create(f"{PREFIX}Unused")
    svc.delete(cid)
    assert svc.get(cid) is None


def test_a_category_in_use_cannot_be_deleted_and_the_message_names_products(svc):
    """Acceptance: the refusal names the blocking products."""
    cid = svc.create(f"{PREFIX}InUse")
    _add_product(cid, "ZZP3", "Blocking Product")
    with pytest.raises(ValidationError) as err:
        svc.delete(cid)
    message = str(err.value)
    assert "Blocking Product" in message
    assert "1 product" in message


def test_the_message_summarises_when_there_are_many(svc):
    cid = svc.create(f"{PREFIX}Many")
    for n in range(5):
        _add_product(cid, f"ZZP1{n}", f"Product {n}")
    with pytest.raises(ValidationError, match="and 2 more"):
        svc.delete(cid)


def test_a_category_with_children_cannot_be_deleted(svc):
    parent = svc.create(f"{PREFIX}HasChild")
    svc.create(f"{PREFIX}TheChild", parent)
    with pytest.raises(ValidationError, match="sub-categor"):
        svc.delete(parent)


def test_disable_leaves_the_products_alone(svc):
    cid = svc.create(f"{PREFIX}Disabled")
    _add_product(cid, "ZZP4", "Still Here")
    svc.deactivate(cid)
    assert svc.get(cid).is_active is False
    assert svc.usage(cid)["products"] == 1
    svc.activate(cid)
    assert svc.get(cid).is_active is True


# --------------------------------------------------------------------- units

def test_units_are_seeded(svc):
    units = {u.short_name for u in svc.list_units()}
    assert {"pcs", "kg", "ltr"} <= units


# ------------------------------------------------------------ authorisation

def test_writes_need_a_signed_in_user(db):
    anonymous = CategoryService()
    assert anonymous.tree() is not None
    with pytest.raises(ValidationError, match="signed-in user"):
        anonymous.create("ZZCAT Nope")


def test_a_cashier_cannot_manage_categories(svc):
    admin = AuthService()
    admin.login("admin", "admin123")
    admin.create_user("zz_cat_cashier", "Counter", "password1", Role.CASHIER)
    cashier = AuthService()
    cashier.login("zz_cat_cashier", "password1")
    restricted = CategoryService(cashier)
    assert restricted.tree() is not None
    with pytest.raises(PermissionDenied):
        restricted.create(f"{PREFIX}Nope")
