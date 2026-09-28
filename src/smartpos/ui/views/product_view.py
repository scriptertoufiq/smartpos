"""Product master: create, edit, search, barcode labels, CSV import and export."""

from __future__ import annotations


class ProductView:
    """Product master: create, edit, search, barcode labels,
    CSV import and export.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
