#!/usr/bin/env bash
# Tests for scripts/set-key. Runs on temporary files only: never touches a real .env, and
# a stand-in replaces docker, so nothing is restarted.
#   bash scripts/tests/test-set-key.sh
set -euo pipefail

script="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/set-key"
compose_file="$(cd "$(dirname "$script")/.." && pwd)/docker-compose.yml"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export DMBOT_ENV_FILE="$work/.env" DMBOT_ENV_EXAMPLE="$work/.env.example"
export DMBOT_TEST_NO_TERMINAL=1
# A stand-in for core/src: the names the code reads (#1970).
export DMBOT_CODE_SOURCE="$work/code"
mkdir -p "$DMBOT_CODE_SOURCE"
printf 'get("COMMENTED_API_KEY")\nget("DISCORD_TOKEN")\n' >"$DMBOT_CODE_SOURCE/config.py"

# The docker stand-in logs every call, and "ps" lists the services in $FAKE_RUNNING.
export DMBOT_DOCKER="$work/docker" FAKE_LOG="$work/docker.log" FAKE_RUNNING=""
cat >"$DMBOT_DOCKER" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$FAKE_LOG"
if [[ " $* " == *" ps "* ]]; then printf '%s\n' "$FAKE_RUNNING"; fi
EOF
chmod +x "$DMBOT_DOCKER"

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
SPEECHMATICS_API_KEY=
SMTP_APP_PASSWORD=
DMBOT_WEB_DB_PASSWORD=
EOF
  printf 'DISCORD_TOKEN=old-discord-token\nTRANSCRIBER=deepgram\nDEEPGRAM_API_KEY=\n' >"$DMBOT_ENV_FILE"
  chmod 644 "$DMBOT_ENV_FILE"
  : >"$FAKE_LOG"
  FAKE_RUNNING=""
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

ok() { [[ $code -eq 0 ]]; }
refused() { [[ $code -ne 0 ]]; }
says() { grep -qF -- "$1" <<<"$out"; }
never_says() { ! grep -qiF -- "$1" <<<"$out"; }
has_line() { grep -qxF -- "$1" "$DMBOT_ENV_FILE"; }
count_lines() { grep -c -- "^$1=" "$DMBOT_ENV_FILE"; }
mode_is() { [[ $(stat -c %a "$DMBOT_ENV_FILE") == "$1" ]]; }
unchanged() { [[ $(cat "$DMBOT_ENV_FILE") == "$(printf 'DISCORD_TOKEN=old-discord-token\nTRANSCRIBER=deepgram\nDEEPGRAM_API_KEY=')" ]]; }
docker_called() { grep -qF -- "$1" "$FAKE_LOG"; }
restarted() { grep -q " up " "$FAKE_LOG"; }
not_restarted() { ! restarted; }

key=abcdef0123456789abcdef0123456789wxyz

reset
run "$key"$'\n' DEEPGRAM_API_KEY
check "sets an empty key" has_line "DEEPGRAM_API_KEY=$key"
check "exit 0 on success" ok
check "shows the length and only the last 4 characters" says "(36 characters, ends in wxyz)"
check "never prints the key" never_says "$key"
check "keeps other settings" has_line "TRANSCRIBER=deepgram"
check ".env is private (600)" mode_is 600
check "no temporary files left" test "$(find "$work" -name '.env.?*' ! -name .env.example | wc -l)" -eq 0

reset
run "new-token.with.dots_and-dashes"$'\n' discord_token
check "accepts lower-case names and replaces a value" has_line "DISCORD_TOKEN=new-token.with.dots_and-dashes"
check "replaces rather than adds" test "$(count_lines DISCORD_TOKEN)" -eq 1

reset
run $'  \t'"$key"$' \r\n' DEEPGRAM_API_KEY
check "trims spaces and line endings from a phone paste" has_line "DEEPGRAM_API_KEY=$key"

reset
run "$key" DEEPGRAM_API_KEY
check "keeps a paste with no line ending" has_line "DEEPGRAM_API_KEY=$key"

reset
run "$key"$'\n' EARS_SHARED_SECRET
check "adds a setting missing from .env" has_line "EARS_SHARED_SECRET=$key"

# The menu
reset
run $'2\n'"$key"$'\n'
check "menu: shows DISCORD_TOKEN as set" says "1) DISCORD_TOKEN (set)"
check "menu: shows EARS_SHARED_SECRET as empty" says "2) EARS_SHARED_SECRET (empty)"
check "menu: shows DEEPGRAM_API_KEY" says "3) DEEPGRAM_API_KEY (empty)"
check "menu: leaves out plain settings" never_says "TRANSCRIBER"
check "menu: leaves out database passwords" never_says "DB_PASSWORD"
check "menu: offers the Gmail app password" says "SMTP_APP_PASSWORD (empty)"
check "menu: sets the picked key" has_line "EARS_SHARED_SECRET=$key"

reset
printf 'DEEPGRAM_API_KEY=""\n' >>"$DMBOT_ENV_FILE"
run $'\n'
check "menu: a quoted empty value shows as empty" says "3) DEEPGRAM_API_KEY (empty)"
check "menu: just Enter cancels" ok
check "menu: cancel says so" says "Cancelled"

reset
run ""
check "menu: end of input cancels" ok
check "menu: cancel changes nothing" unchanged

for bad in 0 5 08 x 18446744073709551617; do
  reset
  run "$bad"$'\n'"$key"$'\n'
  check "menu: rejects pick '$bad'" refused
  check "menu: pick '$bad' changes nothing" unchanged
done

# Bad pastes
# shellcheck disable=SC2016  # the $ is part of the test value
for bad in "has space in it" $'esc\033[31mapes-x' $'tab\tinside-xx' "quote'd-value-x" \
  'dollar$value-xx' 'hash#value-xxxx' 'back\slash-xxxx' "short"; do
  reset
  run "$bad"$'\n' DEEPGRAM_API_KEY
  check "rejects '$bad'" refused
  check "'$bad' changes nothing" unchanged
done

reset
run $'\n' DEEPGRAM_API_KEY
check "rejects an empty paste" refused
check "empty paste: says so" says "nothing was pasted"
check "empty paste changes nothing" unchanged

reset
run "" DEEPGRAM_API_KEY
check "end of input at the paste: refused" refused
check "end of input at the paste: says so" says "nothing was pasted"

# The sending password (#1347): a Google app password or a Resend API key. Nothing else.
reset
run "abcd efgh ijkl mnop"$'\n' SMTP_APP_PASSWORD
check "app password: spaces are removed" has_line "SMTP_APP_PASSWORD=abcdefghijklmnop"
check "app password: saved" ok
check "app password: never printed" never_says "abcdefghijklmnop"
check "app password: no part of it shown" never_says "mnop"
check "app password: says how long" says "Saved SMTP_APP_PASSWORD (16 characters)."
for bad in "abcd efgh ijkl mno" "abcd efgh ijkl mno1" "abcdefghijklmnopq" "abcd-efgh-ijkl-mnop"; do
  reset
  run "$bad"$'\n' SMTP_APP_PASSWORD
  check "app password: rejects '$bad'" refused
  check "app password: '$bad' says 16 letters" says "16 letters"
  check "app password: '$bad' says Resend too" says "starts with re_"
  check "app password: '$bad' changes nothing" unchanged
  check "app password: '$bad' is not echoed" never_says "$bad"
done

# A Resend key: re_ then letters, digits and _, 20 to 80 characters in all (made-up values)
resend_key="re_Abc123_Def456ghi789JKL012"
reset
run "$resend_key"$'\n' SMTP_APP_PASSWORD
check "resend key: saved" ok
check "resend key: in the file" has_line "SMTP_APP_PASSWORD=$resend_key"
check "resend key: never printed" never_says "$resend_key"
check "resend key: no part of it shown" never_says "JKL012"
check "resend key: no start of it shown" never_says "re_Abc"
check "resend key: says how long" says "Saved SMTP_APP_PASSWORD (${#resend_key} characters)."
reset
run "re_$(printf 'a%.0s' $(seq 1 17))"$'\n' SMTP_APP_PASSWORD   # 20 characters: the shortest
check "resend key: 20 characters is allowed" ok
check "resend key: 20 characters, none shown" never_says "aaaaaa"
reset
run "re_$(printf 'a%.0s' $(seq 1 77))"$'\n' SMTP_APP_PASSWORD   # 80 characters: the longest
check "resend key: 80 characters is allowed" ok
check "resend key: 80 characters, none shown" never_says "aaaaaa"
reset
run "" SMTP_APP_PASSWORD
check "mail password: an empty paste says nothing was pasted" says "nothing was pasted"
for bad in "re_short" "re_$(printf 'a%.0s' $(seq 1 16))" "re_$(printf 'a%.0s' $(seq 1 78))" \
  "rx_Abc123_Def456ghi789JKL012" "Re_Abc123_Def456ghi789JKL012" "re_Abc123-Def456ghi789JKL012" \
  "re_Abc123.Def456ghi789JKL012"; do
  reset
  run "$bad"$'\n' SMTP_APP_PASSWORD
  check "resend key: rejects '$bad'" refused
  check "resend key: '$bad' changes nothing" unchanged
  check "resend key: '$bad' is not echoed" never_says "$bad"
  check "resend key: '$bad' says Resend" says "starts with re_"
  check "resend key: '$bad' not even its end is shown" never_says "${bad: -6}"
done

# Names
for name in DMBOT_DB_PASSWORD DMBOT_WEB_DB_PASSWORD POSTGRES_ADMIN_PASSWORD ADMIN_PASSWORD_HASH TRANSCRIBER NOT_A_SETTING 'DISCORD_TOKEN|.*'; do
  reset
  run "$key"$'\n' "$name"
  check "refuses $name" refused
  check "$name changes nothing" unchanged
done

reset
run "$key"$'\n' sk-proj-abc123_secret.looking
check "an argument that looks like a key: refused" refused
check "an argument that looks like a key: never echoed" never_says "abc123"
check "an argument that looks like a key: history hint" says "history -c"

reset
run "$key"$'\n' DEEPGRAM_API_KEY "$key"
check "two arguments: refused" refused
check "two arguments: never echoed" never_says "$key"
check "two arguments change nothing" unchanged

reset
run "old-discord-token"$'\n' DISCORD_TOKEN
check "same value: says so" says "already has that key"
check "same value: .env is still made private" mode_is 600

reset
rm "$DMBOT_ENV_FILE"
run "$key"$'\n' DEEPGRAM_API_KEY
check "no .env: makes one from .env.example" has_line "TRANSCRIBER=whisper-local"
check "no .env: sets the key" has_line "DEEPGRAM_API_KEY=$key"
check "no .env: new file is private" mode_is 600

reset
printf 'DEEPGRAM_API_KEY=first\nDEEPGRAM_API_KEY=second\n' >>"$DMBOT_ENV_FILE"
run "$key"$'\n' DEEPGRAM_API_KEY
check "duplicate lines all get the new value" test "$(grep -c "first\|second" "$DMBOT_ENV_FILE")" -eq 0

# Restarting
reset
run "$key"$'\ny\n' DEEPGRAM_API_KEY
check "restart: not offered when DMbot isn't running" never_says "Restart DMbot"
check "restart: never starts a stopped DMbot" not_restarted
check "restart: gives the command instead" says "docker compose up -d"

reset
FAKE_RUNNING=$'postgres\ncore\nears'
run "$key"$'\ny\n' DEEPGRAM_API_KEY
check "restart: asks docker what is running" docker_called " ps "
check "restart: counts a crash-looping core as running" docker_called "--status running --status restarting"
check "restart: yes restarts core and ears with this folder's compose file" \
  docker_called "compose -f $compose_file up -d core ears"
check "restart: says it's done" says "Done."

reset
FAKE_RUNNING=core
run "$key"$'\nn\n' DEEPGRAM_API_KEY
check "restart: no means no restart" not_restarted
check "restart: no says so" says "Not restarted"

reset
FAKE_RUNNING=core
run "$key"$'\n' DEEPGRAM_API_KEY
check "restart: end of input at the question isn't a failure" ok
check "restart: end of input doesn't restart" not_restarted

reset
FAKE_RUNNING=core
run "$key"$'\ny\n' SPEECHMATICS_API_KEY
check "restart: never for the bake-off key" not_restarted
check "restart: bake-off key says why" says "no restart is needed"

# Piped in without the test switch: refused, so the paste can't show, even with
# DMBOT_ENV_FILE set (#811). The stand-in .env stays set, so a failure can't touch a real one.
reset
set +e
out=$(printf '%s\n' "$key" | DMBOT_TEST_NO_TERMINAL='' "$script" DEEPGRAM_API_KEY 2>&1)
code=$?
set -e
check "no terminal and no test switch is refused" refused
check "no terminal says to log in first" says "Log in to the server first"
check "no terminal changes nothing" unchanged

# DEPLOY.md's "Turn on the admin page" leaves ADMIN_EMAILS to dev1 because of this.
reset
run "owner@example.com"$'\n' ADMIN_EMAILS
check "refuses ADMIN_EMAILS: a setting, not a key" refused
check "ADMIN_EMAILS refusal changes nothing" unchanged

# The real .env.example must list the ElevenLabs key as an active line, or set-key refuses it
# (#1440: the owner could not set the key while the line was commented out).
reset
cp "$(dirname "$script")/../.env.example" "$DMBOT_ENV_EXAMPLE"
run "$key"$'\n' ELEVENLABS_API_KEY
check "the real .env.example lets set-key set ELEVENLABS_API_KEY" ok
check "the ElevenLabs key is saved" has_line "ELEVENLABS_API_KEY=$key"
check "the ElevenLabs key is never printed" never_says "$key"

# A key left commented in .env.example (#1970)
reset
printf '# COMMENTED_API_KEY=\n# UNREAD_API_KEY=\n# COMPOSE_TOKEN=web. prose that names a thing\n#NO_SPACE_KEY=\n' >>"$DMBOT_ENV_EXAMPLE"
run $'the-commented-key\n' COMMENTED_API_KEY
check "a commented key the code reads is accepted by name" ok
check "it is saved" has_line "COMMENTED_API_KEY=the-commented-key"
check "it is never printed" never_says "the-commented-key"
reset
printf '# COMMENTED_API_KEY=\n# UNREAD_API_KEY=\n#NO_SPACE_KEY=\n' >>"$DMBOT_ENV_EXAMPLE"
run $'\n'
check "the list shows the commented key" says "COMMENTED_API_KEY"
check "but not one the code doesn't read" never_says "UNREAD_API_KEY"
check "nor one with no space after #" never_says "NO_SPACE_KEY"
reset
printf '# UNREAD_API_KEY=\n' >>"$DMBOT_ENV_EXAMPLE"
run $'x\n' UNREAD_API_KEY
check "a commented name the code doesn't read is refused" refused
check "and nothing is saved" unchanged

printf '%d passed, %d failed\n' "$passed" "$failed"
((failed == 0))
