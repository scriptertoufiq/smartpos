"""Opening screen: today's sales, today's collection, low-stock count and a sales trend chart."""

from __future__ import annotations


class DashboardView:
    """Opening screen: today's sales, today's collection,
    low-stock count and a sales trend chart.

    Subclasses QWidget once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        raise NotImplementedError
