"""The sales counter -- barcode/keyboard product entry, cart, discount, payment and invoice printing. The screen cashiers live in."""

from __future__ import annotations


class PosView:
    """The sales counter -- barcode/keyboard product entry, cart,
    discount, payment and invoice printing. The screen cashiers live in.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
