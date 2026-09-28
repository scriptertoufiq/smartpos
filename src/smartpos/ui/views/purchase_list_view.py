"""Past purchases with filters and payment status."""

from __future__ import annotations


class PurchaseListView:
    """Past purchases with filters and payment status.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
