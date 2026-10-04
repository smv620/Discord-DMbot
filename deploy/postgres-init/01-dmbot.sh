#!/bin/bash
# Runs once, when the Postgres container first creates its data folder.
# DMbot must use an ordinary role (not a superuser) so row-level security keeps each
# Discord server's data separate, so this creates one and gives it its own database.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -v pw="$DMBOT_DB_PASSWORD" --username "$POSTGRES_USER" --dbname postgres <<'SQL'
CREATE ROLE dmbot LOGIN PASSWORD :'pw';
CREATE DATABASE dmbot OWNER dmbot ENCODING 'UTF8' TEMPLATE template0;
SQL
