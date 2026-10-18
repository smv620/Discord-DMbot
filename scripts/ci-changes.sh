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
    # the website's button check reads the bot's labels from core's Python (web/test/button-labels.test.ts)
    core/src/dmbot/*.py) core=true web=true ;;
    # the Credits page and its tests read the rules data and its ATTRIBUTION.md files (#1923, #1932)
    core/src/dmbot/rules/data/*) core=true web=true ;;
    core/*) core=true ;;
    # the website's owner-check test reads this doc against the held What's new entries
    docs/OWNER_CHECKS.md) web=true ;;
    # core's tests read the plans the website shows (core/tests/test_plans.py)
    web/src/content/plans.json) core=true web=true ;;
    # core's test_consent_promises.py reads the consent promises the pages show (#1882)
    web/src/content/consent-promises.ts) core=true web=true ;;
    web/*) web=true ;;
    # ears' tests read the protocol fixtures; core's read them too (test_protocol.py)
    protocol/*) core=true ears=true ;;
    ears/*) ears=true ;;
    # Core reads most of docs/ (the plan and story memory feed the sidebar, the test scripts feed
    # devtools and tests, docs/ci/ holds a workflow its tests check) and CLAUDE.md. Only these are
    # words nobody reads. The two testing logs are written by devtools (--log) but no test reads
    # the real files (tests use temporary ones; checked with grep in #1355), so a log-only push
    # skips core.
    docs/DEPLOY.md | docs/LIVE_TEST.md | docs/next-test-step.md | LICENSE) ;;
    docs/testing-status.log | docs/testing-history.log) ;;
    # the web check:golive step compares docs/GO_LIVE.md with the site's switches (#1881)
    docs/GO_LIVE.md) core=true web=true ;;
    docs/* | CLAUDE.md) core=true ;;
    *) everything ;; # workflows, scripts/, compose files, .env.example, README.md, anything new
  esac
done <<<"$files"

echo "core=$core"
echo "ears=$ears"
echo "web=$web"
