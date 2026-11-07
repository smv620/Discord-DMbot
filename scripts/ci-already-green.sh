#!/usr/bin/env bash
# Says whether this exact commit, or a commit with exactly the same files, already has a green CI run
# (#1984, #3703), so a second push of the same commit (a branch made from a green one, a fast-forward
# of development) or a merge commit that changed nothing beyond a commit already tested (the last
# merge of a batch has the batch branch's tree; a merge of a PR that already merged development has
# the PR head's tree) does not run it all again.
#
# Prints "done=true", the run it relied on and, for a commit with the same files, "same_as=<sha>";
# or "done=false". It only ever skips when an earlier run finished green AND each job this run needs
# (the jobs ci-changes.sh says to run, plus scripts and docker, which always run) succeeded there: a
# run that skipped core because the push touched only the website must not stand in for a push that
# needs core. "Same files" means the same git tree (`<sha>^{tree}`), read from the clone the
# `changes` job made with full history; a sha this clone cannot resolve is skipped over, never a
# reason to skip the run. A new commit with new files has no earlier run, so it is never skipped;
# any failure to ask GitHub means "done=false".
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

covers() { # covers <run id>: did every job this run needs succeed there?
  local jobs name
  jobs=$(api "actions/runs/$1/jobs?per_page=50") || return 1
  for name in "${need[@]}"; do
    jq -e --arg n "$name" 'any(.jobs[]?; .name == $n and .conclusion == "success")' <<<"$jobs" >/dev/null || return 1
  done
}

tree_of() { # tree_of <sha>: its git tree, or nothing when this clone does not have the commit
  git rev-parse --verify -q "$1^{tree}" 2>/dev/null
}

[ -n "${GITHUB_SHA:-}" ] || no

# 1. An earlier green run of this very commit.
runs=$(api "actions/workflows/ci.yml/runs?head_sha=${GITHUB_SHA}&status=success&per_page=20") || no
ids=$(jq -r --arg me "${GITHUB_RUN_ID:-0}" \
  '.workflow_runs[]? | select(.head_sha == env.GITHUB_SHA and (.id | tostring) != $me) | .id' <<<"$runs") || no
for id in $ids; do
  if covers "$id"; then
    echo "done=true"
    echo "run=$id"
    exit 0
  fi
done

# 2. A green run among the last 50 successful ones, on a commit with the same files (#3703).
mine=$(tree_of "$GITHUB_SHA")
[ -n "$mine" ] || no
recent=$(api "actions/workflows/ci.yml/runs?status=success&per_page=50") || no
others=$(jq -r --arg me "${GITHUB_RUN_ID:-0}" \
  '.workflow_runs[]? | select(.head_sha != env.GITHUB_SHA and (.id | tostring) != $me) | "\(.id) \(.head_sha)"' \
  <<<"$recent") || no
while read -r id sha; do
  [ -n "${id:-}" ] || continue
  [ "$(tree_of "$sha")" = "$mine" ] || continue # a different tree, or a sha not in this clone
  if covers "$id"; then
    echo "done=true"
    echo "run=$id"
    echo "same_as=$sha"
    exit 0
  fi
done <<<"$others"
no
