#!/usr/bin/env bash
# Says whether this exact commit already has a green CI run (#1984), so a second push of the same
# commit (a branch made from a green one, a fast-forward of development) does not run it all again.
#
# Prints "done=true" and the run it relied on, or "done=false". It only ever skips when an earlier
# run of this commit finished green AND each job this run needs (the jobs ci-changes.sh says to run,
# plus scripts and docker, which always run) succeeded there: a run that skipped core because the
# push touched only the website must not stand in for a push that needs core. A new commit has no
# earlier run, so it is never skipped; any failure to ask GitHub means "done=false".
#
# Usage: scripts/ci-already-green.sh   (reads GH_TOKEN, GITHUB_REPOSITORY, GITHUB_SHA, GITHUB_RUN_ID,
# and NEED_CORE / NEED_EARS / NEED_WEB = "false" for a job this push does not need)
# CI_API_DIR (tests only): a folder of canned answers, runs.json and jobs-<id>.json, used instead of GitHub.
set -u

no() {
  echo "done=false"
  exit 0
}

# These names are the jobs' `name:` in .github/workflows/ci.yml; test-ci-already-green.sh checks they match.
need=("scripts (server helpers)" "docker images")
[ "${NEED_CORE:-}" = "false" ] || need+=("core (Python)")
[ "${NEED_EARS:-}" = "false" ] || need+=("ears (Node voice service)")
[ "${NEED_WEB:-}" = "false" ] || need+=("web (customer website)")

api() { # api <path>: the JSON at that path, from GitHub or the canned folder
  if [ -n "${CI_API_DIR:-}" ]; then
    cat "$CI_API_DIR/$(echo "$1" | sed -E 's#.*/runs/([0-9]+)/jobs.*#jobs-\1.json#; s#.*workflows/.*#runs.json#')" 2>/dev/null
  else
    curl -fsS --max-time 20 -H "Authorization: Bearer ${GH_TOKEN:-}" -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/${GITHUB_REPOSITORY:-}/$1" 2>/dev/null
  fi
}

[ -n "${GITHUB_SHA:-}" ] || no
runs=$(api "actions/workflows/ci.yml/runs?head_sha=${GITHUB_SHA}&status=success&per_page=20") || no
ids=$(jq -r --arg me "${GITHUB_RUN_ID:-0}" \
  '.workflow_runs[]? | select(.head_sha == env.GITHUB_SHA and (.id | tostring) != $me) | .id' <<<"$runs") || no

for id in $ids; do
  jobs=$(api "actions/runs/$id/jobs?per_page=50") || continue
  ok=true
  for name in "${need[@]}"; do
    jq -e --arg n "$name" 'any(.jobs[]?; .name == $n and .conclusion == "success")' <<<"$jobs" >/dev/null || ok=false
  done
  if $ok; then
    echo "done=true"
    echo "run=$id"
    exit 0
  fi
done
no
