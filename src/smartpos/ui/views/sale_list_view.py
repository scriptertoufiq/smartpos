"""Past sales with date, customer and warehouse filters."""

from __future__ import annotations


class SaleListView:
    """Past sales with date, customer and warehouse filters.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
