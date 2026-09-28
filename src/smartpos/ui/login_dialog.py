"""Login dialog, shown before the main window."""

from __future__ import annotations


class LoginDialog:
    """Collects a username and password and hands them to AuthService.

    Subclasses QDialog once implemented.
    """

    def __init__(self) -> None:
        raise NotImplementedError
