"""User accounts and roles. Admin only."""

from __future__ import annotations


class UserView:
    """User accounts and roles. Admin only.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
