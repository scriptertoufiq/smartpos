"""Stock on hand per warehouse, transfers, adjustments and the stock movement ledger."""

from __future__ import annotations


class StockView:
    """Stock on hand per warehouse, transfers, adjustments and the
    stock movement ledger.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
