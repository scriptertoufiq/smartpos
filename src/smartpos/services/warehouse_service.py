"""Warehouses and outlets.

A warehouse is any stock location -- the shop floor, a godown, a second branch.
They are deactivated, never deleted: every purchase, sale and stock movement
ever recorded points at one, and those documents must keep resolving.
"""

from __future__ import annotations

from smartpos.core.exceptions import ValidationError
from smartpos.core.logging import get_logger
from smartpos.db.connection import transaction
from smartpos.db.user_repository import ActivityLogRepository
from smartpos.db.warehouse_repository import WarehouseRepository
from smartpos.models.warehouse import Warehouse
from smartpos.services.auth_service import AuthService

log = get_logger(__name__)

MANAGE = "warehouse.manage"
VIEW = "warehouse.view"

MAX_CODE = 20
MAX_NAME = 120


def _to_model(row: dict) -> Warehouse:
    return Warehouse(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        address=row.get("address"),
        phone=row.get("phone"),
        is_active=bool(row["is_active"]),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


class WarehouseService:
    """Reads are open to anyone signed in; writes need `warehouse.manage`.

    `auth` is optional so that read-only callers -- the user editor's warehouse
    picker, for instance -- can use it without a session.
    """

    def __init__(self, auth: AuthService | None = None) -> None:
        self._repo = WarehouseRepository()
        self._audit = ActivityLogRepository()
        self._auth = auth

    # ------------------------------------------------------------------ reads

    def list_all(self, active_only: bool = True) -> list[Warehouse]:
        return [_to_model(r) for r in self._repo.list_all(active_only)]

    def get(self, warehouse_id: int) -> Warehouse | None:
        row = self._repo.get(warehouse_id)
        return _to_model(row) if row else None

    def label_for(self, warehouse_id: int | None) -> str:
        """Human name for an id, for tables that show a posting."""
        if warehouse_id is None:
            return "—"
        warehouse = self.get(warehouse_id)
        return warehouse.name if warehouse else f"#{warehouse_id} (missing)"

    def usage(self, warehouse_id: int) -> dict[str, int]:
        """What would be affected by disabling this outlet."""
        return {
            "products_in_stock": self._repo.stock_on_hand(warehouse_id),
            "users_posted": self._repo.users_posted(warehouse_id),
        }

    # ----------------------------------------------------------------- writes

    def create(self, code: str, name: str, address: str | None = None,
               phone: str | None = None) -> int:
        actor = self._require(MANAGE)
        code, name = self._clean_code(code), self._clean_name(name)

        with transaction() as conn:
            if self._repo.code_taken(code, conn=conn):
                raise ValidationError(f"The code {code!r} is already used by another outlet.")
            warehouse_id = self._repo.insert(
                {"code": code, "name": name,
                 "address": (address or "").strip() or None,
                 "phone": (phone or "").strip() or None},
                conn)
            self._audit.record(actor.id if actor else None, "warehouse.create",
                               "warehouses", warehouse_id,
                               {"code": code, "name": name}, conn)
        log.info("Warehouse created: %s (%s)", name, code)
        return warehouse_id

    def update(self, warehouse_id: int, code: str | None = None, name: str | None = None,
               address: str | None = None, phone: str | None = None) -> None:
        actor = self._require(MANAGE)

        with transaction() as conn:
            existing = self._repo.get(warehouse_id, conn)
            if existing is None:
                raise ValidationError("That outlet no longer exists.")

            changes: dict = {}
            if code is not None:
                cleaned = self._clean_code(code)
                if cleaned != existing["code"]:
                    if self._repo.code_taken(cleaned, warehouse_id, conn):
                        raise ValidationError(
                            f"The code {cleaned!r} is already used by another outlet.")
                    changes["code"] = cleaned
            if name is not None:
                changes["name"] = self._clean_name(name)
            if address is not None:
                changes["address"] = address.strip() or None
            if phone is not None:
                changes["phone"] = phone.strip() or None

            if not changes:
                return
            self._repo.update(warehouse_id, changes, conn)
            self._audit.record(actor.id if actor else None, "warehouse.update",
                               "warehouses", warehouse_id, changes, conn)
        log.info("Warehouse %s updated: %s", warehouse_id, list(changes))

    def deactivate(self, warehouse_id: int) -> None:
        """Close an outlet. Refused while it is the only one, or still holds stock."""
        actor = self._require(MANAGE)

        with transaction() as conn:
            existing = self._repo.get(warehouse_id, conn)
            if existing is None:
                raise ValidationError("That outlet no longer exists.")
            if not existing["is_active"]:
                return

            if self._repo.count_active(exclude_id=warehouse_id, conn=conn) == 0:
                raise ValidationError(
                    "This is the only active outlet. Sales and purchases have "
                    "nowhere to go without one.")

            holding = self._repo.stock_on_hand(warehouse_id, conn)
            if holding:
                raise ValidationError(
                    f"{existing['name']} still holds stock of {holding} "
                    f"product{'s' if holding != 1 else ''}. Transfer it to another "
                    "outlet first, or the goods disappear from every report while "
                    "sitting on its shelves.")

            self._repo.set_active(warehouse_id, False, conn)
            self._audit.record(actor.id if actor else None, "warehouse.deactivate",
                               "warehouses", warehouse_id, conn=conn)
        log.info("Warehouse %s disabled", existing["name"])

    def activate(self, warehouse_id: int) -> None:
        actor = self._require(MANAGE)
        with transaction() as conn:
            if self._repo.get(warehouse_id, conn) is None:
                raise ValidationError("That outlet no longer exists.")
            self._repo.set_active(warehouse_id, True, conn)
            self._audit.record(actor.id if actor else None, "warehouse.activate",
                               "warehouses", warehouse_id, conn=conn)

    # -------------------------------------------------------------- internals

    def _require(self, permission: str):
        if self._auth is None:
            raise ValidationError(
                "This action needs a signed-in user; the service was created "
                "without one.")
        return self._auth.require(permission)

    @staticmethod
    def _clean_code(code: str) -> str:
        code = (code or "").strip().upper()
        if not code:
            raise ValidationError("Code is required.")
        if len(code) > MAX_CODE:
            raise ValidationError(f"Code cannot exceed {MAX_CODE} characters.")
        if any(c.isspace() for c in code):
            raise ValidationError("Code cannot contain spaces.")
        return code

    @staticmethod
    def _clean_name(name: str) -> str:
        name = (name or "").strip()
        if not name:
            raise ValidationError("Name is required.")
        if len(name) > MAX_NAME:
            raise ValidationError(f"Name cannot exceed {MAX_NAME} characters.")
        return name
