"""The main application window.

Holds the sidebar navigation and a QStackedWidget of the views in
`smartpos.ui.views`. Which entries appear depends on the signed-in user's role.
"""

from __future__ import annotations


class MainWindow:
    """Subclasses QMainWindow once implemented."""

    def __init__(self) -> None:
        raise NotImplementedError

    def build_navigation(self) -> None:
        """Create the sidebar, hiding entries the current role cannot use."""
        raise NotImplementedError

    def show_view(self, name: str) -> None:
        """Bring one of the stacked views to the front."""
        raise NotImplementedError
