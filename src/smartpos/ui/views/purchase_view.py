"""Record a purchase from a supplier into a warehouse."""

from __future__ import annotations


class PurchaseView:
    """Record a purchase from a supplier into a warehouse.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
