#!/usr/bin/env python3
"""Create the SmartPOS schema on a MySQL server that Docker did not set up.

Use this when the shop already runs its own MySQL instance:

    python scripts/init_db.py           # create the schema
    python scripts/init_db.py --show    # just report what is there

It reads connection details from .env and applies docker/mysql/init/*.sql.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smartpos.config import get_settings                      # noqa: E402
from smartpos.core.exceptions import SmartPOSError            # noqa: E402
from smartpos.core.logging import configure_logging           # noqa: E402
from smartpos.db import connection, migrations                # noqa: E402


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_dir)

    try:
        connection.init_pool()
        try:
            if "--show" in sys.argv:
                schema = migrations.describe_schema()
                if not schema:
                    print(f"{settings.database.dsn}: no tables.")
                    return 0
                print(f"{settings.database.dsn}: {len(schema)} tables")
                for name, cols in schema.items():
                    print(f"  {name:<24} {cols:>3} columns")
                return 0

            applied = migrations.apply_migrations(force="--force" in sys.argv)
            schema = migrations.describe_schema()
            print(f"Applied {len(applied)} files to {settings.database.dsn}:")
            for name in applied:
                print(f"  {name}")
            print(f"\n{len(schema)} tables created.")
            return 0
        finally:
            connection.close_pool()
    except SmartPOSError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
