#!/usr/bin/env bash
# Tests scripts/ci-already-green.sh (#1984) against canned GitHub answers.
set -u
here=$(cd "$(dirname "$0")" && pwd)
script=$here/../ci-already-green.sh
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export CI_API_DIR=$tmp GITHUB_SHA=abc GITHUB_RUN_ID=9
# CI sets GITHUB_REF to the branch; these cases must not read the real branch head (#3723).
unset GITHUB_REF
fails=0

job() { printf '{"name":"%s","conclusion":"%s"}' "$1" "$2"; }
jobs_file() { # jobs_file <run id> <core conclusion> <web conclusion> [docker conclusion]
  printf '{"jobs":[%s,%s,%s,%s,%s,%s]}' \
    "$(job 'scripts (server helpers)' success)" "$(job 'docker images' "${4:-success}")" \
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

jobs_file 5 success success skipped
check "a run that skipped docker does not stand in for a push that needs docker" "done=false" NEED_EARS=false
check "a run that skipped docker stands in when docker is not needed" "done=true" NEED_EARS=false NEED_DOCKER=false
check "no docker answer runs docker" "done=false" NEED_EARS=false NEED_DOCKER=

echo '{"workflow_runs":[{"id":5,"head_sha":"other"}]}' >"$tmp/runs.json"
jobs_file 5 success success
check "a run of another commit" "done=false" NEED_EARS=false

echo '{"workflow_runs":[{"id":4,"head_sha":"abc"},{"id":5,"head_sha":"abc"}]}' >"$tmp/runs.json"
jobs_file 4 skipped success
jobs_file 5 success success
check "any one earlier run that covers it" "done=true" NEED_EARS=false

rm -f "$tmp/runs.json"
check "no answer from GitHub" "done=false" NEED_EARS=false

# A run for a commit the feature branch no longer points at finishes at once (#3723). The head is
# read from CI_REMOTE_HEAD in these tests; no canned run is needed for any of them.
superseded() { # superseded <name> <want first line> [env assignments...]
  local name=$1 want=$2
  shift 2
  local got
  got=$(env GITHUB_SHA=abc "$@" bash "$script" | head -1)
  if [ "$got" != "$want" ]; then
    echo "FAIL $name: want '$want', got '$got'"
    fails=$((fails + 1))
  fi
}
superseded "a superseded commit on a feature branch gives done=true" "done=true" \
  GITHUB_REF=refs/heads/clouddev/x CI_REMOTE_HEAD=fed987
out=$(env GITHUB_SHA=abc GITHUB_REF=refs/heads/clouddev/x CI_REMOTE_HEAD=fed987 bash "$script")
if [ "$out" != "$(printf 'done=true\nsuperseded_by=fed987')" ]; then
  echo "FAIL superseded output names the newer head: got '$out'"
  fails=$((fails + 1))
fi
superseded "the current head runs normally" "done=false" \
  GITHUB_REF=refs/heads/clouddev/x CI_REMOTE_HEAD=abc
superseded "development never skips" "done=false" \
  GITHUB_REF=refs/heads/development CI_REMOTE_HEAD=fed987
superseded "beta never skips" "done=false" GITHUB_REF=refs/heads/beta CI_REMOTE_HEAD=fed987
superseded "main never skips" "done=false" GITHUB_REF=refs/heads/main CI_REMOTE_HEAD=fed987
superseded "a pull_request run never takes this path" "done=false" \
  GITHUB_REF=refs/pull/12/merge CI_REMOTE_HEAD=fed987
superseded "an unreadable head runs everything" "done=false" \
  GITHUB_REF=refs/heads/clouddev/x CI_REMOTE_HEAD=unreadable
superseded "an empty head answer runs everything" "done=false" \
  GITHUB_REF=refs/heads/clouddev/x CI_REMOTE_HEAD=

workflow=$here/../../.github/workflows/ci.yml
for name in "scripts (server helpers)" "docker images" "core (Python)" "ears (Node voice service)" "web (customer website)"; do
  if ! grep -qF "name: $name" "$workflow" || ! grep -qF "\"$name\"" "$script"; then
    echo "FAIL job name '$name' differs between ci.yml and ci-already-green.sh"
    fails=$((fails + 1))
  fi
done

[ "$fails" -eq 0 ] && echo "ci-already-green: all checks passed"
exit "$fails"
