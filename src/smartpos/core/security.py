"""Password hashing and permission checks."""

from __future__ import annotations

from smartpos.core.constants import Role


def hash_password(plain: str) -> str:
    """Return a bcrypt hash suitable for `users.password_hash`."""
    raise NotImplementedError


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if `plain` matches the stored bcrypt hash."""
    raise NotImplementedError


def has_permission(role: Role, permission: str) -> bool:
    """Return True if `role` is allowed to perform `permission`."""
    raise NotImplementedError
