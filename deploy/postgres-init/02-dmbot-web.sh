#!/bin/bash
# Runs once, when the Postgres container first creates its data folder (after
# 01-dmbot.sh). The website's API connects as its own role, dmbot_web (#498), which the
# database holds to the signed-in person's own servers and to the website's tables. The
# bot grants it what it needs each time it starts. Skipped when DMBOT_WEB_DB_PASSWORD is
# empty (no website yet). For a server that already has its data folder, see README,
# "Database": the same two lines, once, as the postgres user.
set -euo pipefail
if [ -z "${DMBOT_WEB_DB_PASSWORD:-}" ]; then
  echo "DMBOT_WEB_DB_PASSWORD is empty: not creating the website's database role"
  exit 0
fi
psql -v ON_ERROR_STOP=1 -v pw="$DMBOT_WEB_DB_PASSWORD" --username "${POSTGRES_USER:-postgres}" --dbname postgres <<'SQL'
CREATE ROLE dmbot_web LOGIN PASSWORD :'pw';
SQL
