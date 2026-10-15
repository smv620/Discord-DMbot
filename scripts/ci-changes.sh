#!/usr/bin/env bash
# Says which CI jobs a push needs (#1323), as lines for $GITHUB_OUTPUT: core=…, ears=…, web=….
#
# A job is skipped only when nothing it reads can have changed. Anything this script does not
# know by name runs everything, so a new folder can never slip past CI. Pushes to development,
# beta and main always run everything, and so does any failure to work out the changes.
#
# Usage: scripts/ci-changes.sh [base-ref]   (default origin/development; GITHUB_REF is read)
set -u

base_ref=${1:-origin/development}

everything() {
  echo "core=true"
  echo "ears=true"
  echo "web=true"
  exit 0
}

case "${GITHUB_REF:-}" in
  refs/heads/development | refs/heads/beta | refs/heads/main) everything ;;
esac

base=$(git merge-base "$base_ref" HEAD 2>/dev/null) || everything
# --no-renames lists both the old and the new path of a moved file.
files=$(git diff --no-renames --name-only "$base" HEAD 2>/dev/null) || everything
[ -n "$files" ] || everything # nothing differs from the base: not a case to be clever about

core=false
ears=false
web=false
while IFS= read -r f; do
  case "$f" in
    core/*) core=true ;;
    # core's tests read the plans the website shows (core/tests/test_plans.py)
    web/src/content/plans.json) core=true web=true ;;
    web/*) web=true ;;
    # ears' tests read the protocol fixtures; core's read them too (test_protocol.py)
    protocol/*) core=true ears=true ;;
    ears/*) ears=true ;;
    # core's tests read the test scripts; the rest of docs/ is words nobody runs
    docs/test-scripts/*) core=true ;;
    docs/* | LICENSE | CLAUDE.md) ;;
    *) everything ;; # workflows, scripts/, compose files, .env.example, README.md, anything new
  esac
done <<<"$files"

echo "core=$core"
echo "ears=$ears"
echo "web=$web"
