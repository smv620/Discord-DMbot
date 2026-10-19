#!/usr/bin/env bash
# Tests scripts/ci-already-green.sh (#1984) against canned GitHub answers.
set -u
here=$(cd "$(dirname "$0")" && pwd)
script=$here/../ci-already-green.sh
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export CI_API_DIR=$tmp GITHUB_SHA=abc GITHUB_RUN_ID=9
fails=0

job() { printf '{"name":"%s","conclusion":"%s"}' "$1" "$2"; }
jobs_file() { # jobs_file <run id> <core conclusion> <web conclusion>
  printf '{"jobs":[%s,%s,%s,%s,%s,%s]}' \
    "$(job 'scripts (server helpers)' success)" "$(job 'docker images' success)" \
    "$(job 'core (Python)' "$2")" "$(job 'web (customer website)' "$3")" \
    "$(job 'ears (Node voice service)' skipped)" "$(job 'changes (which jobs to run)' success)" >"$tmp/jobs-$1.json"
}
check() { # check <name> <want> [env assignments...]
  local name=$1 want=$2 got
  shift 2
  got=$(env "$@" bash "$script" | head -1)
  if [ "$got" != "$want" ]; then
    echo "FAIL $name: want '$want', got '$got'"
    fails=$((fails + 1))
  fi
}

echo '{"workflow_runs":[]}' >"$tmp/runs.json"
check "a new commit has no earlier run" "done=false" NEED_EARS=false

echo '{"workflow_runs":[{"id":9,"head_sha":"abc"}]}' >"$tmp/runs.json"
check "this run itself never counts" "done=false" NEED_EARS=false

echo '{"workflow_runs":[{"id":5,"head_sha":"abc"}]}' >"$tmp/runs.json"
jobs_file 5 success success
check "a green run with every needed job" "done=true" NEED_EARS=false
check "ears skipped before but needed now" "done=false"

jobs_file 5 skipped success
check "core was skipped before but is needed now" "done=false" NEED_EARS=false
check "core was skipped before and is not needed now" "done=true" NEED_EARS=false NEED_CORE=false

echo '{"workflow_runs":[{"id":5,"head_sha":"other"}]}' >"$tmp/runs.json"
jobs_file 5 success success
check "a run of another commit" "done=false" NEED_EARS=false

echo '{"workflow_runs":[{"id":4,"head_sha":"abc"},{"id":5,"head_sha":"abc"}]}' >"$tmp/runs.json"
jobs_file 4 skipped success
jobs_file 5 success success
check "any one earlier run that covers it" "done=true" NEED_EARS=false

rm -f "$tmp/runs.json"
check "no answer from GitHub" "done=false" NEED_EARS=false

workflow=$here/../../.github/workflows/ci.yml
for name in "scripts (server helpers)" "docker images" "core (Python)" "ears (Node voice service)" "web (customer website)"; do
  if ! grep -qF "name: $name" "$workflow" || ! grep -qF "\"$name\"" "$script"; then
    echo "FAIL job name '$name' differs between ci.yml and ci-already-green.sh"
    fails=$((fails + 1))
  fi
done

[ "$fails" -eq 0 ] && echo "ci-already-green: all checks passed"
exit "$fails"
