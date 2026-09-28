"""Exception types raised by the service layer.

The UI catches `SmartPOSError` and shows the message to the user; anything else
is treated as a bug and logged with a traceback.
"""

from __future__ import annotations


class SmartPOSError(Exception):
    """Base class for every error the application raises deliberately."""


class ConfigurationError(SmartPOSError):
    """Settings are missing or invalid."""


class DatabaseError(SmartPOSError):
    """The database rejected a statement or could not be reached."""


class ValidationError(SmartPOSError):
    """User-supplied data failed a business rule."""


class AuthenticationError(SmartPOSError):
    """Username or password is wrong, or the account is disabled."""


class PermissionDenied(SmartPOSError):
    """The signed-in user's role does not allow this action."""


class InsufficientStock(SmartPOSError):
    """A sale or transfer asked for more units than the warehouse holds."""
