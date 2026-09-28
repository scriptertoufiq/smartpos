"""Customer master, purchase history and due collection."""

from __future__ import annotations


class CustomerView:
    """Customer master, purchase history and due collection.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
