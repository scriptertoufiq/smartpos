#!/usr/bin/env python3
"""Create the SmartPOS schema on a MySQL server that Docker did not set up.

Use this when the shop already runs its own MySQL instance:

    python scripts/init_db.py

It reads connection details from .env and applies docker/mysql/init/*.sql.
"""

from __future__ import annotations

import sys


def main() -> int:
    raise NotImplementedError("See smartpos.db.migrations.apply_migrations().")


if __name__ == "__main__":
    sys.exit(main())
