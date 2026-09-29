"""Password hashing and permission checks."""

from __future__ import annotations

import bcrypt

from smartpos.core.constants import ROLE_PERMISSIONS, Role
from smartpos.core.exceptions import ValidationError

# Work factor. 12 costs roughly a tenth of a second on a shop PC -- slow enough
# to make a stolen hash expensive to attack, fast enough not to delay a login.
BCRYPT_ROUNDS = 12

# bcrypt itself ignores anything past 72 bytes. Silently truncating would mean
# two different passwords unlocking the same account, so it is refused instead.
MAX_PASSWORD_BYTES = 72

MIN_PASSWORD_LENGTH = 6

WILDCARD = "*"


def validate_password(plain: str) -> str:
    """Check a new password before it is hashed. Returns it unchanged."""
    if not plain:
        raise ValidationError("Password cannot be empty.")
    if len(plain) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(plain.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValidationError(
            f"Password is too long (limit {MAX_PASSWORD_BYTES} bytes)."
        )
    return plain


def hash_password(plain: str) -> str:
    """Return a bcrypt hash suitable for `users.password_hash`."""
    validate_password(plain)
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if `plain` matches the stored bcrypt hash.

    Never raises on a malformed or empty hash -- a corrupt row must read as
    "wrong password", not as a crash at the login screen.
    """
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True if a stored hash was made with a weaker cost than we now use."""
    try:
        cost = int(hashed.split("$")[2])
    except (IndexError, ValueError):
        return True
    return cost < BCRYPT_ROUNDS


def has_permission(role: Role, permission: str) -> bool:
    """Return True if `role` is allowed to perform `permission`."""
    granted = ROLE_PERMISSIONS.get(Role(role), frozenset())
    return WILDCARD in granted or permission in granted


def permissions_for(role: Role) -> frozenset[str]:
    """Every permission a role holds. Admin's wildcard is left as `*`."""
    return ROLE_PERMISSIONS.get(Role(role), frozenset())
