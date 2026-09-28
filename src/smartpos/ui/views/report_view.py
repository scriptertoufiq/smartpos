"""Report picker plus the result table, with PDF and Excel export."""

from __future__ import annotations


class ReportView:
    """Report picker plus the result table, with PDF and Excel export.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
