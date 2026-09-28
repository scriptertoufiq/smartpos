"""Product categories, including sub-categories."""

from __future__ import annotations


class CategoryView:
    """Product categories, including sub-categories.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
