"""Shop profile, currency, tax rate, invoice footer and backup."""

from __future__ import annotations


class SettingsView:
    """Shop profile, currency, tax rate, invoice footer and backup.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
