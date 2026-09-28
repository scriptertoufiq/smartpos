"""Supplier master, purchase history and payments made."""

from __future__ import annotations


class SupplierView:
    """Supplier master, purchase history and payments made.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
