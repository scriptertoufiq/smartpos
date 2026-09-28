"""Login, session and permission checks."""

from __future__ import annotations

from smartpos.models.user import User


class AuthService:
    def login(self, username: str, password: str) -> User:
        """Verify the credentials and return the user.

        Raises AuthenticationError if the password is wrong or the account is
        disabled. Updates `users.last_login_at` on success.
        """
        raise NotImplementedError

    def logout(self) -> None:
        """Clear the current session."""
        raise NotImplementedError

    def current_user(self) -> User | None:
        """Return the signed-in user, or None."""
        raise NotImplementedError

    def require(self, permission: str) -> None:
        """Raise PermissionDenied unless the current user holds `permission`."""
        raise NotImplementedError

    def create_user(self, user: User, password: str) -> int:
        raise NotImplementedError

    def change_password(self, user_id: int, old: str, new: str) -> None:
        raise NotImplementedError
