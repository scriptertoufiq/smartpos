"""Products, including barcode lookup and CSV import/export."""

from __future__ import annotations

from pathlib import Path

from smartpos.models.product import Product


class ProductService:
    def search(self, term: str, limit: int = 50) -> list[Product]:
        """Match on name, SKU or barcode -- what the counter's search box calls."""
        raise NotImplementedError

    def get_by_barcode(self, barcode: str) -> Product | None:
        """The scanner path: must be fast and exact."""
        raise NotImplementedError

    def get(self, product_id: int) -> Product | None:
        raise NotImplementedError

    def create(self, product: Product) -> int:
        raise NotImplementedError

    def update(self, product: Product) -> None:
        raise NotImplementedError

    def deactivate(self, product_id: int) -> None:
        raise NotImplementedError

    def generate_barcode(self, product_id: int) -> str:
        """Assign a barcode to a product that does not have one."""
        raise NotImplementedError

    def import_csv(self, path: Path) -> int:
        """Bulk-load products, returning the number of rows inserted."""
        raise NotImplementedError

    def export_csv(self, path: Path) -> None:
        raise NotImplementedError
