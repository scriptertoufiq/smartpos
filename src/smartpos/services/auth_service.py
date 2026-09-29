"""Login, session, permission checks and user administration.

Authorisation is enforced here, at the service boundary, because that is the
only layer every path must cross. Hiding a sidebar button is a convenience, not
a control.
"""

from __future__ import annotations

from typing import Any

from smartpos.core.constants import Role
from smartpos.core.exceptions import (
    AuthenticationError,
    PermissionDenied,
    ValidationError,
)
from smartpos.core.logging import get_logger
from smartpos.core.security import (
    has_permission,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)
from smartpos.db.connection import transaction
from smartpos.db.user_repository import ActivityLogRepository, UserRepository
from smartpos.models.user import User

log = get_logger(__name__)

# The permission that gates everything in this module's admin half.
MANAGE_USERS = "user.manage"

# Deliberately identical for "no such user" and "wrong password": telling them
# apart hands an attacker a list of valid usernames.
_BAD_CREDENTIALS = "Incorrect username or password."


def _to_model(row: dict[str, Any]) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        full_name=row["full_name"],
        password_hash=row["password_hash"],
        role=Role(row["role"]),
        warehouse_id=row["warehouse_id"],
        is_active=bool(row["is_active"]),
        last_login_at=row.get("last_login_at"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


class AuthService:
    """Holds the signed-in user for the life of the application process."""

    def __init__(self) -> None:
        self._users = UserRepository()
        self._audit = ActivityLogRepository()
        self._current: User | None = None

    # ---------------------------------------------------------------- session

    def login(self, username: str, password: str) -> User:
        """Verify the credentials and return the user.

        Raises AuthenticationError if the password is wrong or the account is
        disabled. Updates `users.last_login_at` on success.
        """
        username = (username or "").strip()
        if not username or not password:
            raise AuthenticationError(_BAD_CREDENTIALS)

        row = self._users.get_by_username(username)
        if row is None:
            # Hash anyway so a missing user is not measurably faster to reject.
            verify_password(password, "$2b$12$" + "." * 53)
            raise AuthenticationError(_BAD_CREDENTIALS)

        if not verify_password(password, row["password_hash"]):
            log.warning("Failed login for %r", username)
            raise AuthenticationError(_BAD_CREDENTIALS)

        if not row["is_active"]:
            raise AuthenticationError(
                "This account has been disabled. Ask an administrator to enable it."
            )

        user = _to_model(row)

        with transaction() as conn:
            self._users.touch_last_login(user.id, conn)
            # Upgrade a hash made with an older, weaker cost factor.
            if needs_rehash(row["password_hash"]):
                self._users.set_password(user.id, hash_password(password), conn)
            self._audit.record(user.id, "user.login", "users", user.id, conn=conn)

        self._current = user
        log.info("Signed in: %s (%s)", user.username, user.role)
        return user

    def logout(self) -> None:
        """Clear the current session."""
        if self._current is not None:
            with transaction() as conn:
                self._audit.record(
                    self._current.id, "user.logout", "users", self._current.id, conn=conn
                )
            log.info("Signed out: %s", self._current.username)
        self._current = None

    def current_user(self) -> User | None:
        """Return the signed-in user, or None."""
        return self._current

    def is_signed_in(self) -> bool:
        return self._current is not None

    def can(self, permission: str) -> bool:
        """True if the current user holds `permission`. Never raises."""
        return self._current is not None and has_permission(self._current.role, permission)

    def require(self, permission: str) -> User:
        """Raise PermissionDenied unless the current user holds `permission`."""
        if self._current is None:
            raise PermissionDenied("You must sign in first.")
        if not has_permission(self._current.role, permission):
            raise PermissionDenied(
                f"A {self._current.role} account cannot perform this action."
            )
        return self._current

    # ------------------------------------------------------------ user CRUD
    # Every method below is admin-only.

    def list_users(
        self,
        include_inactive: bool = True,
        role: Role | None = None,
        search: str | None = None,
    ) -> list[User]:
        self.require(MANAGE_USERS)
        return [
            _to_model(r)
            for r in self._users.list_all(include_inactive, role, search)
        ]

    def get_user(self, user_id: int) -> User | None:
        self.require(MANAGE_USERS)
        row = self._users.get(user_id)
        return _to_model(row) if row else None

    def create_user(
        self,
        username: str,
        full_name: str,
        password: str,
        role: Role,
        warehouse_id: int | None = None,
        is_active: bool = True,
    ) -> int:
        """Create an account and return its id."""
        actor = self.require(MANAGE_USERS)
        username = self._clean_username(username)
        full_name = (full_name or "").strip()
        if not full_name:
            raise ValidationError("Full name is required.")
        role = Role(role)
        validate_password(password)

        with transaction() as conn:
            if self._users.username_taken(username, conn=conn):
                raise ValidationError(f"The username {username!r} is already taken.")
            user_id = self._users.create(
                {
                    "username": username,
                    "full_name": full_name,
                    "password_hash": hash_password(password),
                    "role": str(role),
                    "warehouse_id": warehouse_id,
                    "is_active": 1 if is_active else 0,
                },
                conn,
            )
            self._audit.record(
                actor.id, "user.create", "users", user_id,
                {"username": username, "role": str(role)}, conn,
            )
        log.info("User created: %s (%s) by %s", username, role, actor.username)
        return user_id

    def update_user(
        self,
        user_id: int,
        full_name: str | None = None,
        role: Role | None = None,
        warehouse_id: int | None = None,
        username: str | None = None,
    ) -> None:
        """Change an account. Password is handled separately."""
        actor = self.require(MANAGE_USERS)

        with transaction() as conn:
            existing = self._users.get(user_id, conn)
            if existing is None:
                raise ValidationError("That user no longer exists.")

            changes: dict[str, Any] = {}
            if username is not None:
                cleaned = self._clean_username(username)
                if cleaned.lower() != existing["username"].lower():
                    if self._users.username_taken(cleaned, user_id, conn):
                        raise ValidationError(f"The username {cleaned!r} is already taken.")
                    changes["username"] = cleaned
            if full_name is not None:
                cleaned_name = full_name.strip()
                if not cleaned_name:
                    raise ValidationError("Full name is required.")
                changes["full_name"] = cleaned_name
            if role is not None:
                new_role = Role(role)
                if str(new_role) != existing["role"]:
                    self._guard_last_admin(user_id, existing, conn,
                                           demoting=new_role is not Role.ADMIN)
                    changes["role"] = str(new_role)
            if warehouse_id is not None:
                changes["warehouse_id"] = warehouse_id

            if not changes:
                return
            self._users.update_fields(user_id, changes, conn)
            self._audit.record(actor.id, "user.update", "users", user_id, changes, conn)
        log.info("User %s updated by %s: %s", user_id, actor.username, list(changes))

    def set_warehouse(self, user_id: int, warehouse_id: int | None) -> None:
        """Post a user to an outlet, or clear the posting."""
        actor = self.require(MANAGE_USERS)
        with transaction() as conn:
            self._users.update_fields(user_id, {"warehouse_id": warehouse_id}, conn)
            self._audit.record(actor.id, "user.set_warehouse", "users", user_id,
                               {"warehouse_id": warehouse_id}, conn)

    def deactivate_user(self, user_id: int) -> None:
        """Disable an account. The normal way to remove someone."""
        actor = self.require(MANAGE_USERS)
        if user_id == actor.id:
            raise ValidationError("You cannot disable your own account.")

        with transaction() as conn:
            existing = self._users.get(user_id, conn)
            if existing is None:
                raise ValidationError("That user no longer exists.")
            if not existing["is_active"]:
                return
            self._guard_last_admin(user_id, existing, conn, demoting=True)
            self._users.set_active(user_id, False, conn)
            self._audit.record(actor.id, "user.deactivate", "users", user_id, conn=conn)
        log.info("User %s disabled by %s", existing["username"], actor.username)

    def activate_user(self, user_id: int) -> None:
        actor = self.require(MANAGE_USERS)
        with transaction() as conn:
            if self._users.get(user_id, conn) is None:
                raise ValidationError("That user no longer exists.")
            self._users.set_active(user_id, True, conn)
            self._audit.record(actor.id, "user.activate", "users", user_id, conn=conn)

    def delete_user(self, user_id: int) -> None:
        """Remove an account outright.

        Only possible while the account has recorded nothing. Once it has rung
        up a sale or moved stock, deleting it would strip the attribution from
        those documents -- every such foreign key is ON DELETE SET NULL -- so
        the account must be disabled instead.
        """
        actor = self.require(MANAGE_USERS)
        if user_id == actor.id:
            raise ValidationError("You cannot delete your own account.")

        with transaction() as conn:
            existing = self._users.get(user_id, conn)
            if existing is None:
                raise ValidationError("That user no longer exists.")
            if self._users.has_activity(user_id, conn):
                raise ValidationError(
                    f"{existing['username']} has recorded transactions and cannot be "
                    "deleted, because that would remove their name from those "
                    "records. Disable the account instead."
                )
            self._guard_last_admin(user_id, existing, conn, demoting=True)
            self._audit.record(
                actor.id, "user.delete", "users", None,
                {"username": existing["username"], "role": existing["role"]}, conn,
            )
            self._users.delete(user_id, conn)
        log.info("User %s deleted by %s", existing["username"], actor.username)

    # -------------------------------------------------------------- passwords

    def change_password(self, user_id: int, old: str, new: str) -> None:
        """Change your own password, proving you know the current one."""
        if self._current is None:
            raise PermissionDenied("You must sign in first.")
        if user_id != self._current.id:
            # Changing someone else's password is a reset, not a change.
            raise PermissionDenied("You can only change your own password.")

        row = self._users.get(user_id)
        if row is None or not verify_password(old, row["password_hash"]):
            raise AuthenticationError("Your current password is not correct.")
        validate_password(new)
        if verify_password(new, row["password_hash"]):
            raise ValidationError("The new password must be different from the old one.")

        with transaction() as conn:
            self._users.set_password(user_id, hash_password(new), conn)
            self._audit.record(user_id, "user.change_password", "users", user_id, conn=conn)
        log.info("Password changed for %s", row["username"])

    def reset_password(self, user_id: int, new_password: str) -> None:
        """Set someone else's password. Admin only; no old password needed."""
        actor = self.require(MANAGE_USERS)
        validate_password(new_password)
        with transaction() as conn:
            row = self._users.get(user_id, conn)
            if row is None:
                raise ValidationError("That user no longer exists.")
            self._users.set_password(user_id, hash_password(new_password), conn)
            self._audit.record(actor.id, "user.reset_password", "users", user_id, conn=conn)
        log.info("Password reset for %s by %s", row["username"], actor.username)

    # -------------------------------------------------------------- internals

    @staticmethod
    def _clean_username(username: str) -> str:
        username = (username or "").strip()
        if not username:
            raise ValidationError("Username is required.")
        if len(username) < 3:
            raise ValidationError("Username must be at least 3 characters.")
        if len(username) > 50:
            raise ValidationError("Username cannot exceed 50 characters.")
        if any(c.isspace() for c in username):
            raise ValidationError("Username cannot contain spaces.")
        return username

    def _guard_last_admin(self, user_id: int, existing: dict[str, Any],
                          conn: Any, demoting: bool) -> None:
        """Refuse any change that would leave the shop with no way back in."""
        if not demoting or existing["role"] != str(Role.ADMIN) or not existing["is_active"]:
            return
        if self._users.count_active_admins(exclude_id=user_id, conn=conn) == 0:
            raise ValidationError(
                "This is the only active administrator. Create or enable another "
                "admin first, or nobody will be able to manage the system."
            )
