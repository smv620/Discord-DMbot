#!/usr/bin/env bash
# Keeps the core job's Postgres start-up honest (#3958): the service's health check asks over TCP
# as the postgres user (the image's temporary init server listens on the unix socket only, so a
# socket check says "ready" too early, and as root it logs `role "root" does not exist`), and the
# wait after the restart is a real query that fails the job clearly. It reads .github/workflows/ci.yml.
set -u
here=$(cd "$(dirname "$0")" && pwd)
ci=$here/../../.github/workflows/ci.yml
fails=0

ok() { echo "ok: $1"; }
bad() { echo "FAIL: $1"; fails=$((fails + 1)); }

# the health check
if grep -q -- '--health-cmd "pg_isready -h 127.0.0.1 -p 5432 -U postgres"' "$ci"; then
  ok "the health check asks over TCP as the postgres user"
else
  bad "the postgres service's health check must be: pg_isready -h 127.0.0.1 -p 5432 -U postgres"
fi
if grep -nE -- '--health-cmd +pg_isready( |$)' "$ci" >/dev/null; then
  bad "a bare 'pg_isready' health check answers over the unix socket during init and runs as root"
else
  ok "no bare pg_isready health check"
fi

# the wait after the restart
if grep -q "docker restart" "$ci" && grep -q "did not come back within a minute" "$ci"; then
  ok "the wait after the restart gives up with a clear error"
else
  bad "the wait after 'docker restart' must end in an error that says the service did not come back"
fi
if awk '/docker restart/{f=1} f&&/SELECT 1/{found=1} END{exit !found}' "$ci"; then
  ok "the wait runs a real query"
else
  bad "the wait after 'docker restart' must run a real query, not only pg_isready"
fi

# the same two lines in every job that has a postgres service
services=$(grep -c "image: postgres:" "$ci")
checks=$(grep -c -- '--health-cmd "pg_isready -h 127.0.0.1 -p 5432 -U postgres"' "$ci")
if [ "$services" -eq "$checks" ]; then
  ok "every postgres service ($services) has the health check"
else
  bad "$services postgres service(s) but $checks with the TCP health check"
fi

exit "$fails"
