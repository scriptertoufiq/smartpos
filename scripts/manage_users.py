#!/usr/bin/env python3
"""Administer user accounts from the terminal.

The Users screen arrives with the interface in M2. Until then this is how an
administrator creates and manages accounts. It enforces exactly the same rules
as the screen will, because both go through AuthService.

    python scripts/manage_users.py list
    python scripts/manage_users.py create <username> <full name> <role>
    python scripts/manage_users.py rename <username> <new full name>
    python scripts/manage_users.py role   <username> <admin|manager|cashier>
    python scripts/manage_users.py disable <username>
    python scripts/manage_users.py enable  <username>
    python scripts/manage_users.py delete  <username>
    python scripts/manage_users.py passwd  <username>

You are asked to sign in first; every action is recorded against your account.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartpos.config import get_settings                       # noqa: E402
from smartpos.core.constants import Role                       # noqa: E402
from smartpos.core.exceptions import SmartPOSError             # noqa: E402
from smartpos.core.logging import configure_logging            # noqa: E402
from smartpos.db import connection                             # noqa: E402
from smartpos.services.auth_service import AuthService         # noqa: E402


def _find(auth: AuthService, username: str):
    for user in auth.list_users():
        if user.username.lower() == username.lower():
            return user
    raise SystemExit(f"No user named {username!r}.")


def _print_table(users) -> None:
    if not users:
        print("No users.")
        return
    print(f"{'ID':>4}  {'USERNAME':<20} {'NAME':<24} {'ROLE':<9} {'STATUS':<9} LAST LOGIN")
    print("-" * 92)
    for u in users:
        last = u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else "never"
        status = "active" if u.is_active else "disabled"
        print(f"{u.id:>4}  {u.username:<20} {u.full_name:<24} {u.role:<9} {status:<9} {last}")


def _new_password(prompt: str = "New password: ") -> str:
    first = getpass.getpass(prompt)
    if first != getpass.getpass("Repeat it: "):
        raise SystemExit("The two passwords do not match.")
    return first


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    settings = get_settings()
    configure_logging("WARNING", settings.log_dir)
    connection.init_pool()

    auth = AuthService()
    try:
        print(f"Sign in to manage users on {settings.database.dsn}")
        auth.login(input("Username: ").strip(), getpass.getpass("Password: "))
    except SmartPOSError as exc:
        print(f"\n{exc}", file=sys.stderr)
        connection.close_pool()
        return 1

    command, args = argv[0], argv[1:]
    try:
        if command == "list":
            _print_table(auth.list_users())

        elif command == "create":
            if len(args) < 3:
                raise SystemExit("Usage: create <username> <full name> <role>")
            username, role = args[0], args[-1]
            full_name = " ".join(args[1:-1])
            user_id = auth.create_user(username, full_name, _new_password(),
                                       Role(role.lower()))
            print(f"Created {username} (id {user_id}).")

        elif command == "rename":
            user = _find(auth, args[0])
            auth.update_user(user.id, full_name=" ".join(args[1:]))
            print(f"{user.username} renamed.")

        elif command == "role":
            user = _find(auth, args[0])
            auth.update_user(user.id, role=Role(args[1].lower()))
            print(f"{user.username} is now {args[1].lower()}.")

        elif command in ("disable", "enable"):
            user = _find(auth, args[0])
            (auth.deactivate_user if command == "disable" else auth.activate_user)(user.id)
            print(f"{user.username} {command}d.")

        elif command == "delete":
            user = _find(auth, args[0])
            if input(f"Permanently delete {user.username}? type DELETE: ") != "DELETE":
                print("Cancelled.")
                return 0
            auth.delete_user(user.id)
            print(f"{user.username} deleted.")

        elif command == "passwd":
            user = _find(auth, args[0])
            auth.reset_password(user.id, _new_password(f"New password for {user.username}: "))
            print(f"Password reset for {user.username}.")

        else:
            raise SystemExit(f"Unknown command {command!r}. Try --help.")
        return 0

    except SmartPOSError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1
    finally:
        connection.close_pool()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
