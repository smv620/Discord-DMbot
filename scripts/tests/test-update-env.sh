#!/usr/bin/env bash
# Tests for scripts/update-env. Runs on temporary files only: never touches a real .env.
#   bash scripts/tests/test-update-env.sh
set -euo pipefail

script="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/update-env"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export DMBOT_ENV_FILE="$work/.env" DMBOT_ENV_EXAMPLE="$work/.env.example"

passed=0
failed=0

# The new template: a setting moved, new ones added, OLD_SETTING gone, new comments.
template() {
  cat >"$DMBOT_ENV_EXAMPLE" <<'EOF'
# Copy to .env and fill in.

# Discord (new comment)
DISCORD_TOKEN=
DISCORD_DEV_GUILD_ID=

# Transcription
TRANSCRIBER=whisper-local
DEEPGRAM_API_KEY=
NEW_API_KEY=
NEW_MODEL=nova-3
LOG_LEVEL=INFO
DATABASE_URL=postgresql://dmbot:password@localhost:5432/dmbot
# EXAMPLE_ONLY=commented-out settings stay comments
EOF
}

# The owner's current .env, in an older shape.
old_env() {
  cat >"$DMBOT_ENV_FILE" <<'EOF'
# my old notes
TRANSCRIBER=deepgram
DISCORD_TOKEN=tok.en-123
export DEEPGRAM_API_KEY=dg0123456789
DISCORD_DEV_GUILD_ID = 1234567890
LOG_LEVEL=
DATABASE_URL=postgresql://u:p@db:5432/x?a=b
OLD_SETTING="quoted value # kept"
CORE_EXTRAS=dev
# COMMENTED_OUT=parked-secret
# Copy to .env and fill in.
DISCORD_TOKEN=tok.en-last
EOF
  chmod 600 "$DMBOT_ENV_FILE"
}

reset() {
  find "$work" -mindepth 1 -name '.env*' -delete
  find "$work" -mindepth 1 -name 'real.env' -delete
  template
  old_env
}

# run ARGS...: runs update-env; sets $out and $code.
run() {
  set +e
  out=$("$script" "$@" 2>&1)
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

ok() { [[ $code -eq 0 ]]; }
refused() { [[ $code -ne 0 ]]; }
says() { grep -qF -- "$1" <<<"$out"; }
never_says() { ! grep -qF -- "$1" <<<"$out"; }
has_line() { grep -qxF -- "$1" "$DMBOT_ENV_FILE"; }
lacks() { ! grep -qF -- "$1" "$DMBOT_ENV_FILE"; }
line_no() { grep -nxF -- "$1" "$DMBOT_ENV_FILE" | cut -d: -f1; }
before() { [[ $(line_no "$1") -lt $(line_no "$2") ]]; }
mode_is() { [[ $(stat -c %a "$1") == "$2" ]]; }
backups() { find "$work" -name '.env.backup-*' | wc -l; }
leftovers() { find "$work" -name '.env.update.*' | wc -l; }
save() { cp "$DMBOT_ENV_FILE" "$work/saved"; }
same_as_saved() { cmp -s "$DMBOT_ENV_FILE" "$work/saved"; }

reset
save
run
check "exit 0" ok
check "keeps a value" has_line "TRANSCRIBER=deepgram"
check "last duplicate wins, as in Docker Compose" has_line "DISCORD_TOKEN=tok.en-last"
check "drops 'export'" has_line "DEEPGRAM_API_KEY=dg0123456789"
check "spaces around = are tidied" has_line "DISCORD_DEV_GUILD_ID=1234567890"
check "keeps a value with = and ? in it" has_line "DATABASE_URL=postgresql://u:p@db:5432/x?a=b"
check "keeps an empty value empty (not the template default)" has_line "LOG_LEVEL="
check "new setting gets the template default" has_line "NEW_MODEL=nova-3"
check "new key is added empty" has_line "NEW_API_KEY="
check "follows the template order" before "DISCORD_TOKEN=tok.en-last" "TRANSCRIBER=deepgram"
check "uses the template's comments" has_line "# Discord (new comment)"
check "commented-out template settings stay comments" has_line "# EXAMPLE_ONLY=commented-out settings stay comments"
check "leaves out the old file's own comments" lacks "# my old notes"
check "keeps each setting once" test "$(grep -c '^DISCORD_TOKEN=' "$DMBOT_ENV_FILE")" -eq 1
check "keeps old settings missing from the template, at the end" before "DATABASE_URL=postgresql://u:p@db:5432/x?a=b" "CORE_EXTRAS=dev"
check "keeps an old quoted value exactly" has_line 'OLD_SETTING="quoted value # kept"'
check "never copies a commented-out old setting" lacks "parked-secret"
check "reports the new layout" says "- used the layout and notes of the new .env.example"
check "reports kept count" says "- kept your values for 6 settings"
check "reports new settings by name" says "- added 2 new settings from .env.example, set to the default: NEW_API_KEY, NEW_MODEL"
check "reports old extras by name" says "- kept at the end 2 settings not in .env.example (yours, or no longer used): OLD_SETTING, CORE_EXTRAS"
check "reports left-out comment lines (not the template's)" says "- left out 2 comment lines from your old .env (the backup keeps them)"
check "lists other empty keys" says "Other empty keys (only if you use them): NEW_API_KEY"
check "points to set-key" says "To fill in a key: scripts/set-key"
check "no needed key is empty" never_says "Needed"
check "warns a restart drops voice" says "leaves voice for about a minute"
check "says how to restart" says "docker compose up -d"
for secret in tok.en-last dg0123456789 "u:p@db" "quoted value" 1234567890 parked-secret; do
  check "never prints the value '$secret'" never_says "$secret"
done
check "new .env is private" mode_is "$DMBOT_ENV_FILE" 600
check "makes one backup" test "$(backups)" -eq 1
backup=$(find "$work" -name '.env.backup-*' | head -n 1)
check "backup is the old file, unchanged" cmp -s "$backup" "$work/saved"
check "backup is private" mode_is "$backup" 600
check "says how to delete the backup" says "rm $backup"
check "no temporary files left" test "$(leftovers)" -eq 0

# Running again changes nothing.
run
check "second run: already matches" says "already matches"
check "second run: no new backup" test "$(backups)" -eq 1
check "second run: still lists empty keys" says "NEW_API_KEY"
check "second run: no restart hint" never_says "docker compose"

reset
save
run --check
check "--check: exit 0" ok
check "--check: says nothing changed yet" says "Nothing changed yet. Running scripts/update-env would:"
check "--check: mentions the layout" says "- use the layout and notes of the new .env.example"
check "--check: future tense" says "- keep your values for 6 settings"
check "--check: reports new settings" says "- add 2 new settings"
check "--check: says how to do it" says "To do it: scripts/update-env"
check "--check: .env unchanged" same_as_saved
check "--check: no backup" test "$(backups)" -eq 0
check "--check: no temporary files left" test "$(leftovers)" -eq 0

reset
printf 'TRANSCRIBER=deepgram\r\nDEEPGRAM_API_KEY=dg0123456789\r\n' >"$DMBOT_ENV_FILE"
run
check "Windows line endings: removed from values" has_line "DEEPGRAM_API_KEY=dg0123456789"
check "Windows line endings: none left" test "$(grep -c $'\r' "$DMBOT_ENV_FILE")" -eq 0

reset
printf 'TRANSCRIBER=deepgram\n' >"$DMBOT_ENV_FILE"
run
check "singular: 1 setting" says "- kept your values for 1 setting"
check "needed key for the transcriber is flagged" says "Needed, still empty: DISCORD_TOKEN DEEPGRAM_API_KEY"
check "needed keys aren't listed twice" says "Other empty keys (only if you use them): NEW_API_KEY"

reset
printf 'TRANSCRIBER="cloud"\nDISCORD_TOKEN=x\nCLOUD_STT_API_KEY= # todo\nPOSTGRES_ADMIN_PASSWORD=\nDMBOT_DB_PASSWORD=""  \n' >"$DMBOT_ENV_FILE"
printf 'CLOUD_STT_API_KEY=\nPOSTGRES_ADMIN_PASSWORD=\nDMBOT_DB_PASSWORD=\n' >>"$DMBOT_ENV_EXAMPLE"
run
check "empty with a comment counts as empty; quoted transcriber read" says "Needed, still empty: CLOUD_STT_API_KEY"
check "empty database passwords are flagged" says "Database passwords, still empty: POSTGRES_ADMIN_PASSWORD DMBOT_DB_PASSWORD"
check "database passwords: points to the editor" says "nano $DMBOT_ENV_FILE"
check "a comment after = stays a comment" has_line "CLOUD_STT_API_KEY= # todo"

reset
printf 'DISCORD_TOKEN=x\nDEEPGRAM_API_KEY=""\n' >"$DMBOT_ENV_FILE"
run
check "a quoted empty key counts as empty" says "DEEPGRAM_API_KEY"

reset
for i in 1 2 3 4 5 6; do printf 'MORE_%d=\n' "$i" >>"$DMBOT_ENV_EXAMPLE"; done
run
check "many new settings: count only" says "- added 8 new settings from .env.example, set to the default (see .env)"

reset
: >"$DMBOT_ENV_FILE"
run
check "empty old .env: becomes the template" cmp -s "$DMBOT_ENV_FILE" "$DMBOT_ENV_EXAMPLE"

reset
cp "$DMBOT_ENV_EXAMPLE" "$DMBOT_ENV_FILE"
run
check "copy of the template: already matches" says "already matches"
check "copy of the template: no backup" test "$(backups)" -eq 0

reset
printf '%s\n' 'A="esc\"aped"' "B='single # quoted'" >"$DMBOT_ENV_FILE"
run
check "closed double quotes with an escaped quote are kept" has_line 'A="esc\"aped"'
check "closed single quotes are kept" has_line "B='single # quoted'"

# Lines it can't copy safely: refuse, change nothing, never print values.
for bad in $'OK=1\nA="line1\nFAKE=inside\nline3"' $'OK=1\nPEM=\'-----BEGIN-----\nabc\n-----END-----\'' \
  $'OK=1\nmy.dotted=secretvalue' $'OK=1\nBARE_NAME_secretvalue' $'OK=1\nKEY: secretvalue'; do
  label=${bad//$'\n'/ | }
  reset
  printf '%s\n' "$bad" >"$DMBOT_ENV_FILE"
  save
  run
  check "refuses: $label" refused
  check "names line 2: $label" says "line 2"
  check "changes nothing: $label" same_as_saved
  check "no backup: $label" test "$(backups)" -eq 0
  check "never prints the value: $label" never_says "secretvalue"
  check "never prints a multi-line value: $label" never_says "line1"
  check "no temporary files left: $label" test "$(leftovers)" -eq 0
done

reset
mv "$DMBOT_ENV_FILE" "$work/real.env"
ln -s "$work/real.env" "$DMBOT_ENV_FILE"
run
check "symlinked .env: refused" refused
check "symlinked .env: still a link" test -L "$DMBOT_ENV_FILE"

reset
find "$work" -name .env -delete
run
check "no .env: refused" refused
check "no .env: points to set-key" says "scripts/set-key"

reset
find "$work" -name .env.example -delete
run
check "no .env.example: refused" refused
check "no .env.example: says how to get it back" says "git checkout .env.example"
check "no .env.example: .env unchanged" has_line "TRANSCRIBER=deepgram"

for args in "--force" "--check extra"; do
  reset
  # shellcheck disable=SC2086  # split on purpose
  run $args
  check "refuses '$args'" refused
  check "'$args': shows how to use it" says "scripts/update-env --check"
  check "'$args' changes nothing" test "$(backups)" -eq 0
done

reset
run --help
check "--help: exit 0" ok
check "--help: shows how to use it" says "scripts/update-env --check"
check "--help: changes nothing" test "$(backups)" -eq 0

printf '%d passed, %d failed\n' "$passed" "$failed"
((failed == 0))
