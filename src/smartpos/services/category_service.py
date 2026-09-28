"""Product categories and units."""

from __future__ import annotations

from smartpos.models.category import Category, Unit


class CategoryService:
    def list_all(self, active_only: bool = True) -> list[Category]:
        raise NotImplementedError

    def tree(self) -> list[Category]:
        """Return categories ordered so children follow their parent."""
        raise NotImplementedError

    def create(self, category: Category) -> int:
        raise NotImplementedError

    def update(self, category: Category) -> None:
        raise NotImplementedError

    def delete(self, category_id: int) -> None:
        """Refuse if any product still uses the category."""
        raise NotImplementedError

    def list_units(self) -> list[Unit]:
        raise NotImplementedError
