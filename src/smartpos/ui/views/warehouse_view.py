"""Warehouses and outlets."""

from __future__ import annotations


class WarehouseView:
    """Warehouses and outlets.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
