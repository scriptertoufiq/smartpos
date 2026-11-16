#!/usr/bin/env python3
"""Back up and restore the SmartPOS database.

Deliberately not on the Settings screen. A restore replaces every row in the
database, and a button for that sitting next to the shop's phone number is an
accident waiting to happen. Reaching for a terminal is the friction.

    python scripts/backup.py                 take a backup
    python scripts/backup.py --list          show what has been taken
    python scripts/backup.py --restore FILE  replace the database (asks first)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartpos.config import get_settings                             # noqa: E402
from smartpos.core.exceptions import SmartPOSError                   # noqa: E402
from smartpos.core.logging import configure_logging                  # noqa: E402
from smartpos.db import connection                                   # noqa: E402
from smartpos.services.auth_service import AuthService               # noqa: E402
from smartpos.services.backup_service import CONFIRMATION, BackupService  # noqa: E402


def main(argv: list[str]) -> int:
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0

    settings = get_settings()
    configure_logging("WARNING", settings.log_dir)
    connection.init_pool()

    auth = AuthService()
    try:
        import getpass
        print(f"Sign in to manage backups on {settings.database.dsn}")
        auth.login(input("Username: ").strip(), getpass.getpass("Password: "))
    except SmartPOSError as exc:
        print(f"\n{exc}", file=sys.stderr)
        connection.close_pool()
        return 1

    service = BackupService(auth)
    try:
        if "--list" in argv:
            backups = service.list_backups()
            if not backups:
                print(f"No backups in {service.backup_dir()}.")
                return 0
            print(f"{len(backups)} backup(s) in {service.backup_dir()}:")
            for item in backups:
                print(f"  {item['taken_at']:%d %b %Y %H:%M}  "
                      f"{item['size_kb']:>9,.1f} KB  {item['name']}")
            return 0

        if "--restore" in argv:
            index = argv.index("--restore")
            if index + 1 >= len(argv):
                print("Which file? Pass the path after --restore.", file=sys.stderr)
                return 1
            target = Path(argv[index + 1])

            print(f"\nThis REPLACES every row in {settings.database.dsn}")
            print(f"with the contents of {target.name}.")
            print("Anything recorded since that backup will be gone.")
            print("A copy of the database as it stands is taken first.\n")
            if input(f"Type {CONFIRMATION} to go ahead: ") != CONFIRMATION:
                print("Cancelled. Nothing was changed.")
                return 0

            result = service.restore_backup(target, CONFIRMATION)
            print(f"\nRestored {target.name} ({result['statements']} statements).")
            print(f"The previous database is saved at {result['safety_copy']}")
            return 0

        path = service.create_backup()
        print(f"\nBacked up to {path}")
        print(f"  {path.stat().st_size / 1024:,.1f} KB")
        return 0

    except SmartPOSError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1
    finally:
        connection.close_pool()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
