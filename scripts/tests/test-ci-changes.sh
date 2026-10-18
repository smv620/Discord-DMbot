#!/usr/bin/env bash
# Tests scripts/ci-changes.sh (#1323) against a throwaway git repository.
set -u
here=$(cd "$(dirname "$0")" && pwd)
script=$here/../ci-changes.sh
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cd "$tmp" || exit 1
git init -q -b development .
git config user.email t@example.com
git config user.name t
for f in core/a.py core/src/dmbot/ui/b.py core/src/dmbot/rules/data/srd52/spells.json docs/OWNER_CHECKS.md ears/a.ts web/a.tsx web/src/content/plans.json web/src/content/consent-promises.ts protocol/fixtures.json \
  docs/PLAN.md docs/DEPLOY.md docs/LIVE_TEST.md docs/next-test-step.md docs/testing-status.log docs/testing-history.log LICENSE \
  docs/GO_LIVE.md docs/ci/supervisor-review.yml CLAUDE.md docs/test-scripts/s.md scripts/x.sh README.md; do
  mkdir -p "$(dirname "$f")"
  echo base >"$f"
done
git add -A && git commit -qm base
git update-ref refs/remotes/origin/development HEAD

fails=0
# check "name" "files changed on a branch (space separated)" "expected core ears web" [GITHUB_REF]
check() {
  local name=$1 changed=$2 want=$3 ref=${4:-refs/heads/feature}
  git checkout -q -B feature refs/remotes/origin/development
  for f in $changed; do
    case "$f" in
      DELETE:*) git rm -q "${f#DELETE:}" ;;
      *)
        mkdir -p "$(dirname "$f")"
        echo "$RANDOM" >>"$f"
        ;;
    esac
  done
  git add -A && git commit -qm change --allow-empty
  local got
  got=$(GITHUB_REF=$ref bash "$script" | sed 's/^[a-z]*=//' | tr '\n' ' ')
  got=${got% }
  if [ "$got" != "$want" ]; then
    echo "FAIL $name: want '$want', got '$got'"
    fails=$((fails + 1))
  else
    echo "ok   $name"
  fi
}

check "website only" "web/a.tsx" "false false true"
check "words nobody reads" "docs/DEPLOY.md docs/LIVE_TEST.md docs/next-test-step.md LICENSE" "false false false"
check "the testing logs are not read by tests" "docs/testing-status.log docs/testing-history.log" "false false false"
check "the plan is read by core" "docs/PLAN.md" "true false false"
check "the go-live map is read by the website check" "docs/GO_LIVE.md" "true false true"
check "CLAUDE.md is read by core" "CLAUDE.md" "true false false"
check "the workflow template is checked by core" "docs/ci/supervisor-review.yml" "true false false"
check "a deleted core file counts" "DELETE:core/a.py" "true false false"
check "core only" "core/a.py" "true false false"
check "core code is read by the website's button check" "core/src/dmbot/ui/b.py" "true false true"
check "the rules data is read by the Credits page" "core/src/dmbot/rules/data/srd52/spells.json" "true false true"
check "owner checks doc is read by the website tests" "docs/OWNER_CHECKS.md" "false false true"
check "ears only" "ears/a.ts" "false true false"
check "plans.json is read by core" "web/src/content/plans.json" "true false true"
check "the consent promises are read by core" "web/src/content/consent-promises.ts" "true false true"
check "protocol is read by both" "protocol/fixtures.json" "true true false"
check "test scripts are read by core" "docs/test-scripts/s.md" "true false false"
check "a workflow runs everything" ".github/workflows/ci.yml" "true true true"
check "a scripts change runs everything" "scripts/x.sh" "true true true"
check "an unknown file runs everything" "something-new/file" "true true true"
check "README is read by core" "README.md" "true true true"
check "development runs everything" "web/a.tsx" "true true true" refs/heads/development
check "beta runs everything" "docs/DEPLOY.md" "true true true" refs/heads/beta
check "main runs everything" "docs/DEPLOY.md" "true true true" refs/heads/main
check "mixed website and core" "web/a.tsx core/a.py" "true false true"

# Not a git repository, or no base: everything runs.
(cd / && GITHUB_REF=refs/heads/x bash "$script" | tr '\n' ' ' | grep -q "core=true ears=true web=true") ||
  { echo "FAIL outside a repository"; fails=$((fails + 1)); }
git checkout -q -B nobase refs/remotes/origin/development
git update-ref -d refs/remotes/origin/development
(GITHUB_REF=refs/heads/x bash "$script" | tr '\n' ' ' | grep -q "core=true ears=true web=true") ||
  { echo "FAIL no base"; fails=$((fails + 1)); }

[ "$fails" -eq 0 ] && echo "all passed"
exit "$fails"
