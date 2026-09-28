"""Create a return against an invoice number, for the whole invoice or selected lines."""

from __future__ import annotations


class SaleReturnView:
    """Create a return against an invoice number, for the whole
    invoice or selected lines.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
