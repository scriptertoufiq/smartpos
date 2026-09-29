"""Product categories and units.

The proposal promises one level of sub-category (4.3). The schema's self
foreign key allows any depth, so the limit is enforced here -- along with the
rule that a category can never end up inside itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from smartpos.core.exceptions import ValidationError
from smartpos.core.logging import get_logger
from smartpos.db.category_repository import CategoryRepository, UnitRepository
from smartpos.db.connection import transaction
from smartpos.db.user_repository import ActivityLogRepository
from smartpos.models.category import Category, Unit
from smartpos.services.auth_service import AuthService

log = get_logger(__name__)

MANAGE = "category.manage"

# A top-level category and its sub-categories. Depth 1 is a root, depth 2 is a
# sub-category; there is no depth 3.
MAX_DEPTH = 2
MAX_NAME = 120


@dataclass
class CategoryNode:
    """A category plus its position in the tree, for rendering."""

    category: Category
    depth: int
    children: list["CategoryNode"]
    product_count: int = 0


def _to_model(row: dict) -> Category:
    return Category(
        id=row["id"],
        parent_id=row["parent_id"],
        name=row["name"],
        is_active=bool(row["is_active"]),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


class CategoryService:
    """Reads are open; writes need `category.manage`."""

    def __init__(self, auth: AuthService | None = None) -> None:
        self._repo = CategoryRepository()
        self._units = UnitRepository()
        self._audit = ActivityLogRepository()
        self._auth = auth

    # ------------------------------------------------------------------ reads

    def list_all(self, active_only: bool = False) -> list[Category]:
        return [_to_model(r) for r in self._repo.list_all(active_only)]

    def get(self, category_id: int) -> Category | None:
        row = self._repo.get(category_id)
        return _to_model(row) if row else None

    def tree(self, active_only: bool = False,
             with_counts: bool = False) -> list[CategoryNode]:
        """Categories ordered so children follow their parent.

        Built from a single flat read rather than a query per node, and any row
        whose parent is missing is surfaced at the root instead of vanishing.
        """
        rows = self._repo.list_all(active_only)
        by_id = {r["id"]: _to_model(r) for r in rows}

        nodes = {cid: CategoryNode(category=c, depth=1, children=[])
                 for cid, c in by_id.items()}
        roots: list[CategoryNode] = []

        for cid, node in nodes.items():
            parent_id = node.category.parent_id
            parent = nodes.get(parent_id) if parent_id is not None else None
            if parent is None:
                roots.append(node)          # includes orphans, deliberately
            else:
                node.depth = 2
                parent.children.append(node)

        if with_counts:
            for cid, node in nodes.items():
                node.product_count = self._repo.product_count(cid)

        def by_name(items: list[CategoryNode]) -> list[CategoryNode]:
            items.sort(key=lambda n: n.category.name.lower())
            for item in items:
                by_name(item.children)
            return items

        return by_name(roots)

    def flat_tree(self, active_only: bool = False) -> list[tuple[Category, int]]:
        """The tree as (category, depth) pairs, for a combo box."""
        out: list[tuple[Category, int]] = []

        def walk(nodes: list[CategoryNode]) -> None:
            for node in nodes:
                out.append((node.category, node.depth))
                walk(node.children)

        walk(self.tree(active_only))
        return out

    def usage(self, category_id: int) -> dict[str, int]:
        return {
            "products": self._repo.product_count(category_id),
            "sub_categories": self._repo.child_count(category_id),
        }

    def list_units(self) -> list[Unit]:
        return [Unit(id=r["id"], name=r["name"], short_name=r["short_name"])
                for r in self._units.list_all()]

    # ----------------------------------------------------------------- writes

    def create(self, name: str, parent_id: int | None = None) -> int:
        actor = self._require()
        name = self._clean_name(name)

        with transaction() as conn:
            if parent_id is not None:
                parent = self._repo.get(parent_id, conn)
                if parent is None:
                    raise ValidationError("The parent category no longer exists.")
                if parent["parent_id"] is not None:
                    raise ValidationError(
                        f"{parent['name']!r} is already a sub-category. Categories "
                        f"go {MAX_DEPTH} levels deep, no further.")
            if self._repo.name_taken(name, parent_id, conn=conn):
                where = "at the top level" if parent_id is None else "under that parent"
                raise ValidationError(f"A category called {name!r} already exists {where}.")

            category_id = self._repo.insert(
                {"parent_id": parent_id, "name": name}, conn)
            self._audit.record(actor.id, "category.create", "categories", category_id,
                               {"name": name, "parent_id": parent_id}, conn)
        log.info("Category created: %s", name)
        return category_id

    def update(self, category_id: int, name: str | None = None,
               parent_id: int | None = None, reparent: bool = False) -> None:
        """Rename and/or move a category.

        `reparent` must be set to move it, because `parent_id=None` is a real
        destination (the top level) and cannot double as "leave it alone".
        """
        actor = self._require()

        with transaction() as conn:
            existing = self._repo.get(category_id, conn)
            if existing is None:
                raise ValidationError("That category no longer exists.")

            changes: dict = {}
            target_parent = existing["parent_id"]

            if reparent:
                self._check_move(category_id, parent_id, existing, conn)
                target_parent = parent_id
                if parent_id != existing["parent_id"]:
                    changes["parent_id"] = parent_id

            if name is not None:
                cleaned = self._clean_name(name)
                if cleaned != existing["name"] or "parent_id" in changes:
                    if self._repo.name_taken(cleaned, target_parent, category_id, conn):
                        raise ValidationError(
                            f"A category called {cleaned!r} already exists there.")
                if cleaned != existing["name"]:
                    changes["name"] = cleaned

            if not changes:
                return
            self._repo.update(category_id, changes, conn)
            self._audit.record(actor.id, "category.update", "categories",
                               category_id, changes, conn)
        log.info("Category %s updated: %s", category_id, list(changes))

    def delete(self, category_id: int) -> None:
        """Remove a category outright.

        Refused while products still use it: `products.category_id` is
        ON DELETE SET NULL, so deleting would silently strip the grouping from
        every one of them.
        """
        actor = self._require()

        with transaction() as conn:
            existing = self._repo.get(category_id, conn)
            if existing is None:
                raise ValidationError("That category no longer exists.")

            products = self._repo.product_count(category_id, conn)
            if products:
                names = self._repo.sample_products(category_id, 3, conn)
                listed = ", ".join(names)
                more = f" and {products - len(names)} more" if products > len(names) else ""
                raise ValidationError(
                    f"{existing['name']!r} is used by {products} "
                    f"product{'s' if products != 1 else ''} ({listed}{more}). "
                    "Move them to another category first, or disable this one "
                    "instead of deleting it.")

            children = self._repo.child_count(category_id, conn)
            if children:
                raise ValidationError(
                    f"{existing['name']!r} has {children} sub-categor"
                    f"{'ies' if children != 1 else 'y'}. Delete or move those first.")

            self._audit.record(actor.id, "category.delete", "categories", None,
                               {"name": existing["name"]}, conn)
            self._repo.delete(category_id, conn)
        log.info("Category deleted: %s", existing["name"])

    def deactivate(self, category_id: int) -> None:
        """Hide a category without touching the products that reference it."""
        actor = self._require()
        with transaction() as conn:
            if self._repo.get(category_id, conn) is None:
                raise ValidationError("That category no longer exists.")
            self._repo.set_active(category_id, False, conn)
            self._audit.record(actor.id, "category.deactivate", "categories",
                               category_id, conn=conn)

    def activate(self, category_id: int) -> None:
        actor = self._require()
        with transaction() as conn:
            if self._repo.get(category_id, conn) is None:
                raise ValidationError("That category no longer exists.")
            self._repo.set_active(category_id, True, conn)
            self._audit.record(actor.id, "category.activate", "categories",
                               category_id, conn=conn)

    # -------------------------------------------------------------- internals

    def _check_move(self, category_id: int, new_parent: int | None,
                    existing: dict, conn) -> None:
        if new_parent is None:
            return                                   # to the top level: always fine
        if new_parent == category_id:
            raise ValidationError("A category cannot be its own parent.")

        parent = self._repo.get(new_parent, conn)
        if parent is None:
            raise ValidationError("The parent category no longer exists.")

        # Moving under one of your own descendants detaches the whole branch
        # from the tree and leaves a ring nothing can reach.
        if category_id in self._repo.ancestors_of(new_parent, conn):
            raise ValidationError(
                f"{existing['name']!r} cannot be moved inside its own "
                "sub-category.")

        if parent["parent_id"] is not None:
            raise ValidationError(
                f"{parent['name']!r} is already a sub-category. Categories go "
                f"{MAX_DEPTH} levels deep, no further.")

        if self._repo.child_count(category_id, conn):
            raise ValidationError(
                f"{existing['name']!r} has sub-categories of its own, so it "
                "cannot become one.")

    def _require(self):
        if self._auth is None:
            raise ValidationError(
                "This action needs a signed-in user; the service was created "
                "without one.")
        return self._auth.require(MANAGE)

    @staticmethod
    def _clean_name(name: str) -> str:
        name = " ".join((name or "").split())
        if not name:
            raise ValidationError("Name is required.")
        if len(name) > MAX_NAME:
            raise ValidationError(f"Name cannot exceed {MAX_NAME} characters.")
        return name
