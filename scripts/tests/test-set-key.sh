#!/usr/bin/env bash
# shellcheck disable=SC2016  # single-quoted bash -c bodies and test values are meant literally
# Tests for scripts/set-key. Runs on temporary files only: never touches a real .env.
#   bash scripts/tests/test-set-key.sh
set -euo pipefail

script="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/set-key"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export DMBOT_ENV_FILE="$work/.env" DMBOT_ENV_EXAMPLE="$work/.env.example"

passed=0
failed=0

reset() {
  cat >"$DMBOT_ENV_EXAMPLE" <<'EOF'
# comment
DISCORD_TOKEN=
DMBOT_DB_PASSWORD=
EARS_SHARED_SECRET=
DEEPGRAM_API_KEY=
TRANSCRIBER=whisper-local
EOF
  printf 'DISCORD_TOKEN=old-discord-token\nTRANSCRIBER=deepgram\nDEEPGRAM_API_KEY=\n' >"$DMBOT_ENV_FILE"
  chmod 644 "$DMBOT_ENV_FILE"
}

# run INPUT ARGS...: runs set-key with INPUT on stdin; sets $out and $code.
run() {
  local input=$1
  shift
  set +e
  out=$(printf '%s' "$input" | "$script" "$@" 2>&1)
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
    printf 'FAIL: %s\n  output: %s\n  .env:\n%s\n' "$what" "$out" "$(cat "$DMBOT_ENV_FILE" 2>/dev/null)"
  fi
}

has_line() { grep -qxF -- "$1" "$DMBOT_ENV_FILE"; }
count_lines() { grep -c -- "^$1=" "$DMBOT_ENV_FILE"; }
unchanged() { [[ $(cat "$DMBOT_ENV_FILE") == "$(printf 'DISCORD_TOKEN=old-discord-token\nTRANSCRIBER=deepgram\nDEEPGRAM_API_KEY=')" ]]; }

key=abcdef0123456789abcdef0123456789wxyz

reset
run "$key"$'\n' DEEPGRAM_API_KEY
check "sets an empty key" has_line "DEEPGRAM_API_KEY=$key"
check "exit 0 on success" test "$code" -eq 0
check "shows only the last 4 characters" grep -q "ends in wxyz" <<<"$out"
check "never prints the key" bash -c '! grep -qF -- "$1" <<<"$2"' _ "$key" "$out"
check "keeps other settings" has_line "TRANSCRIBER=deepgram"
check ".env is private (600)" test "$(stat -c %a "$DMBOT_ENV_FILE")" = 600
check "no temporary files left" test "$(find "$work" -name '.env.?*' ! -name .env.example | wc -l)" -eq 0

reset
run "new-token.with.dots_and-dashes"$'\n' discord_token
check "accepts lower-case names and replaces a value" has_line "DISCORD_TOKEN=new-token.with.dots_and-dashes"
check "replaces rather than adds" test "$(count_lines DISCORD_TOKEN)" -eq 1

reset
run $'  \t'"$key"$' \r\n' DEEPGRAM_API_KEY
check "trims spaces and line endings from a phone paste" has_line "DEEPGRAM_API_KEY=$key"

reset
run "$key"$'\n' EARS_SHARED_SECRET
check "adds a setting missing from .env" has_line "EARS_SHARED_SECRET=$key"

reset
run $'2\n'"$key"$'\n'
check "menu: lists keys and tokens only" bash -c 'grep -q "1) DISCORD_TOKEN (set)" <<<"$1" && grep -q "2) EARS_SHARED_SECRET (empty)" <<<"$1" && grep -q "3) DEEPGRAM_API_KEY (empty)" <<<"$1" && ! grep -q "TRANSCRIBER\|DB_PASSWORD" <<<"$1"' _ "$out"
check "menu: sets the picked key" has_line "EARS_SHARED_SECRET=$key"

for bad in 0 4 08 x ""; do
  reset
  run "$bad"$'\n'"$key"$'\n'
  check "menu: rejects pick '$bad'" bash -c "[[ $code -ne 0 ]]"
  check "menu: pick '$bad' changes nothing" unchanged
done

for bad in "has space in it" "quote'd-value-x" 'dollar$value-xx' 'hash#value-xxxx' 'back\slash-xxxx' "short"; do
  reset
  run "$bad"$'\n' DEEPGRAM_API_KEY
  check "rejects '$bad'" bash -c "[[ $code -ne 0 ]]"
  check "'$bad' changes nothing" unchanged
done

reset
run $'\n' DEEPGRAM_API_KEY
check "rejects an empty paste" bash -c "[[ $code -ne 0 ]] && grep -q 'Nothing was pasted' <<<\"\$1\"" _ "$out"
check "empty paste changes nothing" unchanged

for name in DMBOT_DB_PASSWORD POSTGRES_ADMIN_PASSWORD; do
  reset
  run "$key"$'\n' "$name"
  check "refuses $name" bash -c "[[ $code -ne 0 ]]"
  check "$name changes nothing" unchanged
done

reset
run "$key"$'\n' NOT_A_SETTING
check "refuses names not in .env.example" bash -c "[[ $code -ne 0 ]]"
check "unknown name changes nothing" unchanged

reset
run "$key"$'\n' 'DISCORD_TOKEN|.*'
check "refuses a name with pattern characters" bash -c "[[ $code -ne 0 ]]"

reset
run "old-discord-token"$'\n' DISCORD_TOKEN
check "same value: says so" grep -q "already has that value" <<<"$out"

reset
rm "$DMBOT_ENV_FILE"
run "$key"$'\n' DEEPGRAM_API_KEY
check "no .env: makes one from .env.example" has_line "TRANSCRIBER=whisper-local"
check "no .env: sets the key" has_line "DEEPGRAM_API_KEY=$key"
check "no .env: new file is private" test "$(stat -c %a "$DMBOT_ENV_FILE")" = 600

reset
printf 'DEEPGRAM_API_KEY=first\nDEEPGRAM_API_KEY=second\n' >>"$DMBOT_ENV_FILE"
run "$key"$'\n' DEEPGRAM_API_KEY
check "duplicate lines all get the new value" bash -c '! grep -q "first\|second" "$1"' _ "$DMBOT_ENV_FILE"

printf '%d passed, %d failed\n' "$passed" "$failed"
((failed == 0))
