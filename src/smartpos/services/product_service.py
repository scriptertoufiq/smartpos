"""Products, including barcode lookup and CSV import/export."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from smartpos.core.exceptions import ValidationError
from smartpos.core.logging import get_logger
from smartpos.db.category_repository import CategoryRepository, UnitRepository
from smartpos.db.connection import transaction
from smartpos.db.product_repository import ProductRepository
from smartpos.db.user_repository import ActivityLogRepository
from smartpos.models.product import Product
from smartpos.services.auth_service import AuthService
from smartpos.utils import barcode as barcode_utils

log = get_logger(__name__)

MANAGE = "product.manage"
VIEW = "product.view"

MAX_SKU = 40
MAX_NAME = 160

CSV_COLUMNS = [
    "sku", "name", "barcode", "category", "unit",
    "purchase_price", "sale_price", "tax_rate", "reorder_level",
]


@dataclass
class ImportResult:
    """What a CSV import did, and what it could not do.

    Good rows are kept even when others fail: a shop importing five thousand
    products should not lose the lot because three have a typo.
    """

    imported: int = 0
    updated: int = 0
    failed: list[tuple[int, str]] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return self.imported + self.updated + len(self.failed)

    def summary(self) -> str:
        parts = []
        if self.imported:
            parts.append(f"{self.imported} added")
        if self.updated:
            parts.append(f"{self.updated} updated")
        if self.failed:
            parts.append(f"{len(self.failed)} skipped")
        return ", ".join(parts) or "nothing to do"


def _to_model(row: dict) -> Product:
    product = Product(
        id=row["id"],
        sku=row["sku"],
        barcode=row.get("barcode"),
        name=row["name"],
        category_id=row.get("category_id"),
        unit_id=row.get("unit_id"),
        purchase_price=row["purchase_price"],
        sale_price=row["sale_price"],
        tax_rate=row["tax_rate"],
        reorder_level=row["reorder_level"],
        image_path=row.get("image_path"),
        is_active=bool(row["is_active"]),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )
    # Names carried along by the join, for tables that would otherwise re-query.
    product.category_name = row.get("category_name")   # type: ignore[attr-defined]
    product.unit_name = row.get("unit_name")           # type: ignore[attr-defined]
    return product


class ProductService:
    """Reads are open to anyone signed in; writes need `product.manage`."""

    def __init__(self, auth: AuthService | None = None) -> None:
        self._repo = ProductRepository()
        self._categories = CategoryRepository()
        self._units = UnitRepository()
        self._audit = ActivityLogRepository()
        self._auth = auth

    # ------------------------------------------------------------------ reads

    def search(self, term: str, limit: int = 50, active_only: bool = True,
               category_id: int | None = None) -> list[Product]:
        """Match on name, SKU or barcode -- what the counter's search box calls."""
        return [_to_model(r) for r in self._repo.search(
            (term or "").strip(), limit, active_only, category_id)]

    def get_by_barcode(self, barcode: str) -> Product | None:
        """The scanner path: exact, indexed, no wildcards."""
        barcode = (barcode or "").strip()
        if not barcode:
            return None
        row = self._repo.get_by_barcode(barcode)
        return _to_model(row) if row else None

    def get_by_sku(self, sku: str) -> Product | None:
        row = self._repo.get_by_sku((sku or "").strip())
        return _to_model(row) if row else None

    def get(self, product_id: int) -> Product | None:
        row = self._repo.get(product_id)
        return _to_model(row) if row else None

    def count(self) -> int:
        return self._repo.count()

    # ----------------------------------------------------------------- writes

    def create(self, sku: str, name: str, *, barcode: str | None = None,
               category_id: int | None = None, unit_id: int | None = None,
               purchase_price: Decimal | str | float = 0,
               sale_price: Decimal | str | float = 0,
               tax_rate: Decimal | str | float = 0,
               reorder_level: Decimal | str | float = 0,
               image_path: str | None = None,
               auto_barcode: bool = False) -> int:
        actor = self._require()
        data = self._validated(sku, name, barcode, category_id, unit_id,
                               purchase_price, sale_price, tax_rate, reorder_level)
        data["image_path"] = image_path or None

        with transaction() as conn:
            self._check_unique(data["sku"], data["barcode"], None, conn)
            product_id = self._repo.insert(data, conn)

            if auto_barcode and not data["barcode"]:
                generated = barcode_utils.generate_code(product_id)
                self._repo.update(product_id, {"barcode": generated}, conn)

            self._audit.record(actor.id, "product.create", "products", product_id,
                               {"sku": data["sku"], "name": data["name"]}, conn)
        log.info("Product created: %s (%s)", data["name"], data["sku"])
        return product_id

    def update(self, product_id: int, **fields) -> None:
        actor = self._require()

        with transaction() as conn:
            existing = self._repo.get(product_id, conn)
            if existing is None:
                raise ValidationError("That product no longer exists.")

            changes: dict = {}
            if "sku" in fields and fields["sku"] is not None:
                changes["sku"] = self._clean_sku(fields["sku"])
            if "name" in fields and fields["name"] is not None:
                changes["name"] = self._clean_name(fields["name"])
            if "barcode" in fields:
                changes["barcode"] = self._clean_barcode(fields["barcode"])
            for key in ("category_id", "unit_id", "image_path"):
                if key in fields:
                    changes[key] = fields[key] or None
            for key, places in (("purchase_price", 2), ("sale_price", 2),
                                ("tax_rate", 2), ("reorder_level", 3)):
                if key in fields and fields[key] is not None:
                    changes[key] = self._money(fields[key], key, places)

            if not changes:
                return
            self._check_unique(changes.get("sku", existing["sku"]),
                               changes.get("barcode", existing["barcode"]),
                               product_id, conn)
            self._repo.update(product_id, changes, conn)
            self._audit.record(actor.id, "product.update", "products",
                               product_id, {k: str(v) for k, v in changes.items()}, conn)
        log.info("Product %s updated: %s", product_id, list(changes))

    def deactivate(self, product_id: int) -> None:
        actor = self._require()
        with transaction() as conn:
            if self._repo.get(product_id, conn) is None:
                raise ValidationError("That product no longer exists.")
            self._repo.set_active(product_id, False, conn)
            self._audit.record(actor.id, "product.deactivate", "products",
                               product_id, conn=conn)

    def activate(self, product_id: int) -> None:
        actor = self._require()
        with transaction() as conn:
            if self._repo.get(product_id, conn) is None:
                raise ValidationError("That product no longer exists.")
            self._repo.set_active(product_id, True, conn)
            self._audit.record(actor.id, "product.activate", "products",
                               product_id, conn=conn)

    def delete(self, product_id: int) -> None:
        """Only while nothing references it; otherwise deactivate."""
        actor = self._require()
        with transaction() as conn:
            existing = self._repo.get(product_id, conn)
            if existing is None:
                raise ValidationError("That product no longer exists.")
            if self._repo.has_activity(product_id, conn):
                raise ValidationError(
                    f"{existing['name']!r} appears on sales, purchases or the stock "
                    "ledger and cannot be deleted. Disable it instead.")
            self._audit.record(actor.id, "product.delete", "products", None,
                               {"sku": existing["sku"], "name": existing["name"]}, conn)
            self._repo.delete(product_id, conn)

    # ------------------------------------------------------------- barcodes

    def generate_barcode(self, product_id: int) -> str:
        """Assign a barcode to a product that does not have one."""
        actor = self._require()
        with transaction() as conn:
            existing = self._repo.get(product_id, conn)
            if existing is None:
                raise ValidationError("That product no longer exists.")
            if existing["barcode"]:
                return existing["barcode"]

            code = barcode_utils.generate_code(product_id)
            if self._repo.barcode_taken(code, product_id, conn):
                raise ValidationError(
                    f"The generated barcode {code} is already in use. "
                    "Enter one by hand.")
            self._repo.update(product_id, {"barcode": code}, conn)
            self._audit.record(actor.id, "product.generate_barcode", "products",
                               product_id, {"barcode": code}, conn)
        log.info("Barcode %s assigned to product %s", code, product_id)
        return code

    def render_labels(self, product_ids: list[int], output: Path,
                      per_row: int = 3) -> Path:
        codes = []
        for pid in product_ids:
            product = self.get(pid)
            if product is None:
                continue
            codes.append(product.barcode or self.generate_barcode(pid))
        if not codes:
            raise ValidationError("None of those products have a barcode to print.")
        return barcode_utils.render_label_sheet(codes, output, per_row)

    # ------------------------------------------------------------------- CSV

    def export_csv(self, path: Path, active_only: bool = False) -> int:
        rows = self._repo.list_all(active_only)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for r in rows:
                writer.writerow({
                    "sku": r["sku"],
                    "name": r["name"],
                    "barcode": r["barcode"] or "",
                    "category": r.get("category_name") or "",
                    "unit": r.get("unit_name") or "",
                    "purchase_price": r["purchase_price"],
                    "sale_price": r["sale_price"],
                    "tax_rate": r["tax_rate"],
                    "reorder_level": r["reorder_level"],
                })
        log.info("Exported %d products to %s", len(rows), path)
        return len(rows)

    def import_csv(self, path: Path, update_existing: bool = False) -> ImportResult:
        """Bulk-load products, row by row.

        Each row is its own transaction, so one bad row does not undo the rows
        before it. The result names every row that failed and why.
        """
        self._require()
        if not path.is_file():
            raise ValidationError(f"No such file: {path}")

        result = ImportResult()
        categories = {c["name"].lower(): c["id"] for c in self._categories.list_all()}
        units = {}
        for u in self._units.list_all():
            units[u["short_name"].lower()] = u["id"]
            units[u["name"].lower()] = u["id"]

        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            missing = {"sku", "name"} - set(h.strip().lower() for h in (reader.fieldnames or []))
            if missing:
                raise ValidationError(
                    f"The file needs at least a 'sku' and a 'name' column; "
                    f"missing {', '.join(sorted(missing))}.")

            for line_no, raw in enumerate(reader, start=2):   # row 1 is the header
                row = {(k or "").strip().lower(): (v or "").strip()
                       for k, v in raw.items()}
                try:
                    self._import_row(row, categories, units, update_existing, result)
                except ValidationError as exc:
                    result.failed.append((line_no, str(exc)))
                except Exception as exc:                       # noqa: BLE001
                    log.exception("Unexpected error importing row %d", line_no)
                    result.failed.append((line_no, f"unexpected error: {exc}"))

        log.info("Import of %s: %s", path.name, result.summary())
        return result

    def _import_row(self, row: dict, categories: dict, units: dict,
                    update_existing: bool, result: ImportResult) -> None:
        sku = self._clean_sku(row.get("sku", ""))
        name = self._clean_name(row.get("name", ""))

        category_id = None
        if row.get("category"):
            category_id = categories.get(row["category"].lower())
            if category_id is None:
                raise ValidationError(f"unknown category {row['category']!r}")
        unit_id = None
        if row.get("unit"):
            unit_id = units.get(row["unit"].lower())
            if unit_id is None:
                raise ValidationError(f"unknown unit {row['unit']!r}")

        payload = {
            "barcode": self._clean_barcode(row.get("barcode") or None),
            "category_id": category_id,
            "unit_id": unit_id,
            "purchase_price": self._money(row.get("purchase_price") or 0, "purchase_price", 2),
            "sale_price": self._money(row.get("sale_price") or 0, "sale_price", 2),
            "tax_rate": self._money(row.get("tax_rate") or 0, "tax_rate", 2),
            "reorder_level": self._money(row.get("reorder_level") or 0, "reorder_level", 3),
        }

        with transaction() as conn:
            existing = self._repo.get_by_sku(sku, conn)
            if existing and not update_existing:
                raise ValidationError(f"SKU {sku!r} already exists")
            if payload["barcode"] and self._repo.barcode_taken(
                    payload["barcode"], existing["id"] if existing else None, conn):
                raise ValidationError(f"barcode {payload['barcode']} already in use")

            if existing:
                self._repo.update(existing["id"], {"name": name, **payload}, conn)
                result.updated += 1
            else:
                self._repo.insert({"sku": sku, "name": name, **payload}, conn)
                result.imported += 1

    # -------------------------------------------------------------- internals

    def _validated(self, sku, name, barcode, category_id, unit_id,
                   purchase_price, sale_price, tax_rate, reorder_level) -> dict:
        return {
            "sku": self._clean_sku(sku),
            "name": self._clean_name(name),
            "barcode": self._clean_barcode(barcode),
            "category_id": category_id or None,
            "unit_id": unit_id or None,
            "purchase_price": self._money(purchase_price, "purchase price", 2),
            "sale_price": self._money(sale_price, "sale price", 2),
            "tax_rate": self._money(tax_rate, "tax rate", 2),
            "reorder_level": self._money(reorder_level, "reorder level", 3),
        }

    def _check_unique(self, sku: str, barcode: str | None,
                      exclude_id: int | None, conn) -> None:
        if self._repo.sku_taken(sku, exclude_id, conn):
            raise ValidationError(f"The SKU {sku!r} is already used by another product.")
        if barcode and self._repo.barcode_taken(barcode, exclude_id, conn):
            raise ValidationError(
                f"The barcode {barcode} is already used by another product.")

    def _require(self):
        if self._auth is None:
            raise ValidationError(
                "This action needs a signed-in user; the service was created "
                "without one.")
        return self._auth.require(MANAGE)

    @staticmethod
    def _clean_sku(sku: str) -> str:
        sku = (sku or "").strip().upper()
        if not sku:
            raise ValidationError("SKU is required.")
        if len(sku) > MAX_SKU:
            raise ValidationError(f"SKU cannot exceed {MAX_SKU} characters.")
        if any(c.isspace() for c in sku):
            raise ValidationError("SKU cannot contain spaces.")
        return sku

    @staticmethod
    def _clean_name(name: str) -> str:
        name = " ".join((name or "").split())
        if not name:
            raise ValidationError("Name is required.")
        if len(name) > MAX_NAME:
            raise ValidationError(f"Name cannot exceed {MAX_NAME} characters.")
        return name

    @staticmethod
    def _clean_barcode(barcode: str | None) -> str | None:
        """Blank means "no barcode" -- stored as NULL, because the unique index
        allows many NULLs but only one empty string."""
        barcode = (barcode or "").strip()
        if not barcode:
            return None
        if not barcode.isdigit():
            raise ValidationError("A barcode must be digits only.")
        if len(barcode) == barcode_utils.EAN13_LENGTH and not barcode_utils.is_valid(barcode):
            raise ValidationError(
                f"{barcode} is 13 digits but its check digit is wrong "
                f"(expected {barcode_utils.check_digit(barcode[:12])}).")
        if len(barcode) > 64:
            raise ValidationError("A barcode cannot exceed 64 characters.")
        return barcode

    @staticmethod
    def _money(value, label: str, places: int) -> Decimal:
        try:
            amount = Decimal(str(value).strip() or "0")
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError(f"{label} must be a number, got {value!r}.") from exc
        if amount < 0:
            raise ValidationError(f"{label} cannot be negative.")
        return amount.quantize(Decimal(10) ** -places)
