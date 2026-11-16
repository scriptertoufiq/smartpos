#!/bin/bash
# ---------------------------------------------------------------------------
# SmartPOS · 006 · Test grants
# The backup test restores a dump into a throwaway schema, never the live one,
# so the application user needs rights on that scratch schema too. Without this
# the test fails with "Access denied ... to database 'smartpos_restore_check'".
#
# A shell script, not .sql, so it can use whatever MYSQL_USER the container was
# started with.
#
# Only runs on a fresh data volume. For an existing container, run once:
#   docker compose exec db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
#     "GRANT ALL PRIVILEGES ON \`smartpos_restore_check\`.* TO \`$MYSQL_USER\`@\`%\`"'
# ---------------------------------------------------------------------------
set -e

mysql -uroot -p"$MYSQL_ROOT_PASSWORD" <<SQL
GRANT ALL PRIVILEGES ON \`smartpos_restore_check\`.* TO '${MYSQL_USER}'@'%';
FLUSH PRIVILEGES;
SQL
