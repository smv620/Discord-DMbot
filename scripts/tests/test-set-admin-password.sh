#!/usr/bin/env bash
# Tests for scripts/set-admin-password. Temporary files only: never touches a real .env,
# and a stand-in replaces the hasher, so no image is needed.
#   bash scripts/tests/test-set-admin-password.sh
set -euo pipefail

script="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/set-admin-password"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export DMBOT_ENV_FILE="$work/.env" DMBOT_ENV_EXAMPLE="$work/.env.example"
export DMBOT_TEST_NO_TERMINAL=1

# The hasher stand-in records what it was given and prints a fixed, hash-like value.
export DMBOT_HASHER="$work/hasher" HASHER_SAW="$work/saw"
cat >"$DMBOT_HASHER" <<'HASHER'
#!/usr/bin/env bash
cat >"$HASHER_SAW"
echo "JGFyZ29uMmlkJHY9MTkkbT02NTUzNix0PTMscD00JGZha2VzYWx0JGZha2VoYXNo"
HASHER
chmod +x "$DMBOT_HASHER"

passed=0
failed=0

reset() {
  printf '# comment\nADMIN_EMAILS=\nADMIN_PASSWORD_HASH=\n' >"$DMBOT_ENV_EXAMPLE"
  printf 'ADMIN_EMAILS=owner@example.com\nADMIN_PASSWORD_HASH=old\nOTHER=kept\n' >"$DMBOT_ENV_FILE"
  rm -f "$HASHER_SAW"
}

run() {
  set +e
  out=$(printf '%s' "$1" | "$script" 2>&1)
  code=$?
  set -e
}

check() {
  local what=$1
  shift
  if "$@"; then
    passed=$((passed + 1))
  else
    failed=$((failed + 1))
    printf 'FAIL: %s\n  output: %s\n  .env:\n%s\n' "$what" "$out" "$(cat "$DMBOT_ENV_FILE")"
  fi
}

good="a long admin sentence here"

reset
run "$good"$'\n'"$good"$'\n'
check "a good password is saved" [ "$code" -eq 0 ]
check "only the hash is written" grep -qx 'ADMIN_PASSWORD_HASH=JGFyZ29uMmlkJHY9MTkkbT02NTUzNix0PTMscD00JGZha2VzYWx0JGZha2VoYXNo' "$DMBOT_ENV_FILE"
check "other settings are kept" grep -qx 'OTHER=kept' "$DMBOT_ENV_FILE"
check "the password never reaches .env" bash -c "! grep -q '$good' '$DMBOT_ENV_FILE'"
check "the password is never shown" bash -c "[[ \"\$1\" != *'$good'* ]]" _ "$out"
check "the hasher got the password on standard input" grep -qx "$good" "$HASHER_SAW"
check ".env is private" [ "$(stat -c %a "$DMBOT_ENV_FILE")" = 600 ]

reset
run "$good"$'\n'"a different sentence here"$'\n'
check "two different passwords are refused" [ "$code" -ne 0 ]
check "nothing changes when they differ" grep -qx 'ADMIN_PASSWORD_HASH=old' "$DMBOT_ENV_FILE"
check "the hasher isn't asked when they differ" [ ! -e "$HASHER_SAW" ]

reset
run $'short pass\nshort pass\n'
check "a short password is refused" [ "$code" -ne 0 ]
check "the refusal says how long" bash -c "[[ \"\$1\" == *'16 characters'* ]]" _ "$out"
check "nothing changes when it's short" grep -qx 'ADMIN_PASSWORD_HASH=old' "$DMBOT_ENV_FILE"

reset
cat >"$DMBOT_HASHER" <<'HASHER'
#!/usr/bin/env bash
cat >/dev/null
echo "Error: no such service"
HASHER
run "$good"$'\n'"$good"$'\n'
check "a hasher error isn't saved as a hash" [ "$code" -ne 0 ]
check "nothing changes on a hasher error" grep -qx 'ADMIN_PASSWORD_HASH=old' "$DMBOT_ENV_FILE"

reset
cat >"$DMBOT_HASHER" <<'HASHER'
#!/usr/bin/env bash
cat >"$HASHER_SAW"
echo "JGFyZ29uMmlkJHY9MTkkbT02NTUzNix0PTMscD00JGZha2VzYWx0JGZha2VoYXNo"
HASHER
padded="  a padded admin password  "
run "$padded"$'\n'"$padded"$'\n'
check "spaces at either end are kept" grep -qx -- "$padded" "$HASHER_SAW"

reset
set +e
out=$(printf '%s\n%s\n' "$good" "$good" | DMBOT_TEST_NO_TERMINAL='' "$script" 2>&1)
code=$?
set -e
check "piped in without the test switch is refused" [ "$code" -ne 0 ]
check "the refusal says never to paste it into a chat" grep -q "Never paste it into a chat" <<<"$out"
check "nothing changes without a terminal" grep -qx 'ADMIN_PASSWORD_HASH=old' "$DMBOT_ENV_FILE"
check "the hasher isn't asked without a terminal" [ ! -e "$HASHER_SAW" ]

printf '%d passed, %d failed\n' "$passed" "$failed"
((failed == 0))
