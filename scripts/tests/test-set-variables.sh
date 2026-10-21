#!/usr/bin/env bash
# Tests for scripts/set-variables. Runs on temporary files only: never touches a real .env, and
# a stand-in replaces docker, so nothing is restarted.
#   bash scripts/tests/test-set-variables.sh
set -euo pipefail

script="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/set-variables"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export DMBOT_ENV_FILE="$work/.env" DMBOT_ENV_EXAMPLE="$work/.env.example"
export DMBOT_TEST_NO_TERMINAL=1
# A stand-in for config.py: the names DMbot reads (#1970).
export DMBOT_CONFIG_SOURCE="$work/config.py"
cat >"$DMBOT_CONFIG_SOURCE" <<'EOF'
get("DMBOT_SWITCH")
get("DMBOT_OTHER_SWITCH")
get("SOME_COMMENTED_TOKEN")
get("TRANSCRIBER")
EOF

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

original=$'DISCORD_TOKEN=old-discord-token\nTRANSCRIBER=deepgram\nMAIL_ALERTS_FROM=DMbot alerts <old@example.org>\nLOG_LEVEL=\nDEEPGRAM_API_KEY=old-deepgram-key\nSMTP_APP_PASSWORD=old-mail-password\nDMBOT_DB_PASSWORD=old-db-password\n'

reset() {
  cat >"$DMBOT_ENV_EXAMPLE" <<'EOF'
# comment
DISCORD_TOKEN=
DATABASE_URL=postgresql://dmbot:dmbot@localhost:5432/dmbot
DMBOT_DB_PASSWORD=
EARS_SHARED_SECRET=
DEEPGRAM_API_KEY=
SMTP_APP_PASSWORD=
ADMIN_PASSWORD_HASH=
TRANSCRIBER=whisper-local
MAIL_ALERTS_FROM=
LOG_LEVEL=INFO
SHARD_COUNT=1
FOO_PASSWORD_FILE=
SECRET_NAME=
# DMBOT_SWITCH=0
# DMBOT_OTHER_SWITCH=5
# COMPOSE_PROFILES=web. Discord prose that happens to start with a name
# DMBOT_NOT_READ=1
# SOME_COMMENTED_TOKEN=
#DMBOT_NO_SPACE=1
#  DMBOT_TWO_SPACES=1
TRANSCRIBER_SHADOW=x
# TRANSCRIBER=whisper-local
EOF
  printf '%s' "$original" >"$DMBOT_ENV_FILE"
  chmod 644 "$DMBOT_ENV_FILE"
  : >"$FAKE_LOG"
  rm -f "$work"/.env.backup-*
  FAKE_RUNNING=""
}

# run INPUT ARGS...: runs set-variables with INPUT on stdin; sets $out and $code.
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
unchanged() { [[ $(cat "$DMBOT_ENV_FILE") == "${original%$'\n'}" ]]; }
backups() { find "$work" -maxdepth 1 -name '.env.backup-*' | wc -l; }
docker_called() { grep -qF -- "$1" "$FAKE_LOG"; }
restarted() { grep -q " up " "$FAKE_LOG"; }
not_restarted() { ! restarted; }

# Changing a setting shows the value that is there now, then writes the new one
reset
run $'ops@example.org\n' MAIL_ALERTS_FROM
check "shows the current value first" says "Now: DMbot alerts <old@example.org>"
check "sets the new value" has_line "MAIL_ALERTS_FROM=ops@example.org"
check "exit 0 on success" ok
check "says what it is now" says "MAIL_ALERTS_FROM is now ops@example.org"
check "replaces rather than adds" test "$(count_lines MAIL_ALERTS_FROM)" -eq 1
check "keeps other settings" has_line "TRANSCRIBER=deepgram"
check ".env is private (600)" mode_is 600
check "makes one backup" test "$(backups)" -eq 1
check "the backup is the old .env" test "$(cat "$work"/.env.backup-*)" = "${original%$'\n'}"
check "the backup is private (600)" test "$(stat -c %a "$work"/.env.backup-*)" = "600"
check "tells where the backup is" says ".env.backup-"
check "no temporary files left" test "$(find "$work" -name '.env.??????' | wc -l)" -eq 0

# Enter alone leaves it unchanged
reset
run $'\n' MAIL_ALERTS_FROM
check "Enter alone changes nothing" unchanged
check "Enter alone exits 0" ok
check "Enter alone says so" says "Nothing was changed"
check "Enter alone makes no backup" test "$(backups)" -eq 0

# Same value again
reset
run $'DMbot alerts <old@example.org>\n' MAIL_ALERTS_FROM
check "the same value changes nothing" unchanged
check "the same value says so" says "already has that value"
check "the same value makes no backup" test "$(backups)" -eq 0

# Values with spaces and <>, and a phone paste
reset
run $'  DMbot support <support@example.com> \r\n' MAIL_ALERTS_FROM
check "keeps spaces and <> inside, trims the ends" has_line "MAIL_ALERTS_FROM=DMbot support <support@example.com>"

# A dash empties a setting
reset
run $'-\n' MAIL_ALERTS_FROM
check "a dash empties it" has_line "MAIL_ALERTS_FROM="
check "a dash says it is empty" says "MAIL_ALERTS_FROM is now empty"
check "emptying makes a backup" test "$(backups)" -eq 1

# Names: lower case works, an empty line and an absent line show what DMbot would do
reset
run $'DEBUG\n' log_level
check "accepts lower-case names" has_line "LOG_LEVEL=DEBUG"
check "an empty setting with a default says what DMbot uses" says "DMbot uses its own default: INFO"
reset
run $'\n' SHARD_COUNT
check "a missing setting says its default" says "nothing in .env, so DMbot uses its own default: 1"
reset
run $'3\n' SHARD_COUNT
check "adds a setting missing from .env" has_line "SHARD_COUNT=3"
check "adding keeps the old lines" has_line "TRANSCRIBER=deepgram"

# Values that would be misread are refused, and the text typed is not left in .env
# The $ and ` here are meant literally: they are the characters the script must refuse.
# shellcheck disable=SC2016
for bad in 'a"b' "a'b" 'a$b' 'a#b' 'a\b' 'a`b'; do
  reset
  run "$bad"$'\n' MAIL_ALERTS_FROM
  check "refuses $bad" refused
  check "nothing changed for $bad" unchanged
  check "no backup for $bad" test "$(backups)" -eq 0
done
reset
run "$(printf 'a%.0s' $(seq 1 501))"$'\n' MAIL_ALERTS_FROM
check "refuses a value over 500 characters" refused
check "nothing changed for the long value" unchanged

# Everything that holds a secret is refused, with the right pointer
reset
run $'whatever\n' DISCORD_TOKEN
check "refuses a token" refused
check "points to set-key for a token" says "scripts/set-key DISCORD_TOKEN"
check "a token is unchanged" unchanged
check "a refused token never shows its value" never_says "old-discord-token"
reset
run $'whatever\n' DEEPGRAM_API_KEY
check "refuses a key" refused
check "points to set-key for a key" says "scripts/set-key DEEPGRAM_API_KEY"
check "a refused key never shows its value" never_says "old-deepgram-key"
reset
run $'whatever\n' SMTP_APP_PASSWORD
check "refuses the mail password" refused
check "points to set-key for the mail password" says "scripts/set-key SMTP_APP_PASSWORD"
check "a refused mail password never shows its value" never_says "old-mail-password"
reset
run $'whatever\n' DMBOT_DB_PASSWORD
check "refuses a database password" refused
check "explains the lock-out" says "lock DMbot out"
check "a refused database password never shows its value" never_says "old-db-password"
reset
run $'whatever\n' ADMIN_PASSWORD_HASH
check "refuses the admin hash" refused
check "points to set-admin-password" says "scripts/set-admin-password"
reset
run $'whatever\n' DATABASE_URL
check "refuses DATABASE_URL (a password is inside it)" refused
reset
run $'whatever\n' NOT_A_SETTING
check "refuses a name not in .env.example" refused
check "says it isn't one of DMbot's settings" says "isn't one of DMbot's settings"
check "an unknown name leaves .env alone" unchanged
reset
run $'whatever\n' A B
check "refuses two names" refused
reset
run $'whatever\n' 'abc def;rm'
check "refuses something that is not a name" refused
check "refusing a bad name changes nothing" unchanged

# One rule for the menu and for a typed name: a name that looks like a secret anywhere is out
reset
run $'whatever\n' FOO_PASSWORD_FILE
check "refuses a name with PASSWORD in the middle" refused
reset
run $'whatever\n' SECRET_NAME
check "refuses a name that starts with SECRET" refused
reset
run $'\n'
check "menu: never lists a name with PASSWORD in the middle" never_says "FOO_PASSWORD_FILE"
check "menu: never lists a name that starts with SECRET" never_says "SECRET_NAME"

# Hidden characters from a phone paste are refused; real accents are not
for hidden in $'\xe2\x80\x8b' $'\xe2\x80\x8c' $'\xe2\x80\x8d' $'\xe2\x81\xa0' $'\xef\xbb\xbf' $'\xc2\xa0'; do
  reset
  run "a${hidden}b"$'\n' MAIL_ALERTS_FROM
  check "refuses a hidden character ($(printf '%s' "$hidden" | od -An -tx1 | tr -d ' \n'))" refused
  check "a hidden character changes nothing" unchanged
done
reset
run $'Caf\xc3\xa9 \xc3\x89lan\n' MAIL_ALERTS_FROM
check "accepts accented letters" has_line $'MAIL_ALERTS_FROM=Caf\xc3\xa9 \xc3\x89lan'

# Values with = or & are written as typed
reset
run $'a=b&c\n' MAIL_ALERTS_FROM
check "keeps = and & in a value" has_line "MAIL_ALERTS_FROM=a=b&c"

# Two lines for one setting become one, with the new value
reset
printf 'LOG_LEVEL=INFO\nTRANSCRIBER=deepgram\nLOG_LEVEL=DEBUG\n' >"$DMBOT_ENV_FILE"
run $'WARNING\n' LOG_LEVEL
check "a duplicated setting is replaced everywhere" test "$(grep -c '^LOG_LEVEL=WARNING$' "$DMBOT_ENV_FILE")" -eq 2
check "no old duplicate is left" test "$(grep -c '^LOG_LEVEL=\(INFO\|DEBUG\)$' "$DMBOT_ENV_FILE")" -eq 0

# What is there now is shown even when the line is written by hand
reset
printf 'MAIL_ALERTS_FROM="DMbot alerts <quoted@example.org>"\n' >"$DMBOT_ENV_FILE"
run $'\n' MAIL_ALERTS_FROM
check "shows a quoted value without its quotes" says "Now: DMbot alerts <quoted@example.org>"
reset
printf 'MAIL_ALERTS_FROM=plain@example.org # my note\n' >"$DMBOT_ENV_FILE"
run $'\n' MAIL_ALERTS_FROM
check "shows a line with a trailing note as it is (not 'empty')" says "Now: plain@example.org # my note"

# A symlinked .env becomes a plain file
reset
cp "$DMBOT_ENV_FILE" "$work/real.vars"
rm -f "$DMBOT_ENV_FILE"
ln -s "$work/real.vars" "$DMBOT_ENV_FILE"
run $'ops@example.org\n' MAIL_ALERTS_FROM
check "a symlinked .env is replaced by a plain file" test ! -L "$DMBOT_ENV_FILE"
check "the new plain file has the change" has_line "MAIL_ALERTS_FROM=ops@example.org"
rm -f "$work/real.vars"

# The real .env.example through the menu: nothing secret can ever be listed (catches drift
# whenever someone adds a setting). Read-only: only the repo's template is read.
reset
real_example="$(cd "$(dirname "$script")/.." && pwd)/.env.example"
set +e
out=$(printf '\n' | DMBOT_ENV_EXAMPLE="$real_example" "$script" 2>&1)
code=$?
set -e
check "the real template: the menu opens and cancels" ok
check "the real template: lists some settings" says "1) "
for forbidden in _KEY _TOKEN _SECRET PASSWORD _HASH DATABASE_URL; do
  check "the real template: the menu never lists $forbidden" never_says "$forbidden"
done

# The menu
reset
run $'2\n'"ops@example.org"$'\n'
check "menu: shows TRANSCRIBER as set" says "TRANSCRIBER (set)"
check "menu: shows LOG_LEVEL as empty" says "LOG_LEVEL (empty)"
check "menu: shows MAIL_ALERTS_FROM as set" says "MAIL_ALERTS_FROM (set)"
check "menu: never lists a token" never_says "DISCORD_TOKEN"
check "menu: never lists a key" never_says "DEEPGRAM_API_KEY"
check "menu: never lists a secret" never_says "EARS_SHARED_SECRET"
check "menu: never lists a password" never_says "PASSWORD"
check "menu: never lists the admin hash" never_says "ADMIN_PASSWORD_HASH"
check "menu: never lists DATABASE_URL" never_says "DATABASE_URL"
check "menu: picks the second setting (MAIL_ALERTS_FROM)" has_line "MAIL_ALERTS_FROM=ops@example.org"
reset
run $'\n'
check "menu: Enter cancels" ok
check "menu: cancel says so" says "Cancelled. Nothing was changed."
check "menu: cancel changes nothing" unchanged
reset
run $'99\n'
check "menu: a number off the list is refused" refused
reset
run $'abc\n'
check "menu: a word is refused" refused
check "menu: a bad pick changes nothing" unchanged

# No .env yet: made from the template, no backup to make
reset
rm -f "$DMBOT_ENV_FILE"
run $'DEBUG\n' LOG_LEVEL
check "makes .env from .env.example when there is none" has_line "LOG_LEVEL=DEBUG"
check "a new .env gets the template's other lines" has_line "TRANSCRIBER=whisper-local"
check "a new .env needs no backup" test "$(backups)" -eq 0
check "a new .env is private (600)" mode_is 600

# The last line wins when .env has two
reset
printf 'LOG_LEVEL=INFO\nLOG_LEVEL=DEBUG\n' >"$DMBOT_ENV_FILE"
run $'\n' LOG_LEVEL
check "shows the last of two lines, as Docker Compose reads it" says "Now: DEBUG"

# Restart offer
reset
FAKE_RUNNING=$'core\nears\nweb-api'
run $'ops@example.org\ny\n' MAIL_ALERTS_FROM
check "y restarts the running services, without their dependencies" docker_called "up -d --no-deps core ears web-api"
check "y says it is done" says "DMbot is using the new value"
reset
FAKE_RUNNING=$'core\nears\nweb-api'
run $'ops@example.org\n\n' MAIL_ALERTS_FROM
check "Enter does not restart" not_restarted
check "no restart says how to do it later" says "docker compose up -d"
reset
FAKE_RUNNING=$'core\nears'
run $'ops@example.org\ny\n' MAIL_ALERTS_FROM
check "only restarts what is running (no web-api)" docker_called "up -d --no-deps core ears"
check "does not start web-api when it was stopped" test "$(grep -c 'web-api' "$FAKE_LOG")" -eq 0
reset
FAKE_RUNNING=""
run $'ops@example.org\ny\n' MAIL_ALERTS_FROM
check "does not start a stopped DMbot" not_restarted
reset
FAKE_RUNNING=$'core\nears'
run $'\n' MAIL_ALERTS_FROM
check "nothing changed means no restart" not_restarted

# Settings written as "# NAME=value" (#1970)
reset
run $'\n' DMBOT_SWITCH
check "a commented setting the code reads can be reached by name" ok
check "it shows the example's value as the default" says "DMbot uses its own default: 0"
reset
run $'1\n' DMBOT_SWITCH
check "setting it appends NAME=value to .env" has_line "DMBOT_SWITCH=1"
check "it is added once" test "$(count_lines DMBOT_SWITCH)" -eq 1
check "the old lines stay" has_line "TRANSCRIBER=deepgram"
check "a backup is made first" test "$(backups)" -eq 1
check "the backup is the old .env" test "$(cat "$work"/.env.backup-*)" = "${original%$'\n'}"
check ".env stays private" mode_is 600
run $'\n' DMBOT_SWITCH
check "now it is in .env, with its value" says "Now: 1"
# The menu lists it as not in .env, and leaves out the rest
reset
run $'\n'
check "the menu lists a commented setting" says "DMBOT_SWITCH (not in .env)"
check "and another one" says "DMBOT_OTHER_SWITCH (not in .env)"
check "a prose comment is not listed" never_says "COMPOSE_PROFILES"
check "a commented name the code doesn't read is not listed" never_says "DMBOT_NOT_READ"
check "a commented secret is not listed" never_says "SOME_COMMENTED_TOKEN"
check "# with no space or two spaces is not a setting" never_says "DMBOT_NO_SPACE"
check "two spaces is not a setting either" never_says "DMBOT_TWO_SPACES"
check "an active setting is not listed twice" test "$(grep -c ') TRANSCRIBER ' <<<"$out")" -le 1
check "a commented setting missing from .env says so" says "DMBOT_SWITCH (not in .env)"
printf '%s\nDMBOT_OTHER_SWITCH=7\n' "$original" >"$DMBOT_ENV_FILE"
run $'\n'
check "once in .env it shows set" says "DMBOT_OTHER_SWITCH (set)"
# By name: what isn't a setting is refused, a commented secret too
reset
run $'1\n' DMBOT_NOT_READ
check "a commented name the code doesn't read is refused" refused
check "and says it is not a setting" says "isn't one of DMbot's settings"
check "and changes nothing" unchanged
reset
run $'1\n' SOME_COMMENTED_TOKEN
check "a commented secret is sent to set-key" says "scripts/set-key SOME_COMMENTED_TOKEN"
check "and changes nothing" unchanged
reset
run $'1\n' COMPOSE_PROFILES
check "a prose comment's name is refused" refused
# Without config.py to check against, a commented setting is not offered
reset
DMBOT_CONFIG_SOURCE="$work/missing.py" run $'1\n' DMBOT_SWITCH
check "no config.py to check: a commented setting is refused" refused

# Descriptions (#2181): the list says what each setting is, and picking one explains it before the
# value is asked for, without printing the current value in the description.
descriptions_file="$work/descriptions"
printf '%s\n' '# comment' 'TRANSCRIBER|Which service writes the text|Long text about the transcriber. Needs a restart.' >"$descriptions_file"
export DMBOT_SETTING_DESCRIPTIONS="$descriptions_file"
reset
run $'\n'
check "the list shows a short description" says "TRANSCRIBER (set) - Which service writes the text"
check "a setting with no description is still listed" says "LOG_LEVEL (empty)"
check "no stray dash for a setting with no description" never_says "LOG_LEVEL (empty) -"
reset
run $'\n' TRANSCRIBER
check "picking a setting shows its detail" says "Long text about the transcriber. Needs a restart."
check "the detail comes before the current value" test "$(grep -n 'Long text' <<<"$out" | cut -d: -f1)" -lt "$(grep -n 'Now:' <<<"$out" | cut -d: -f1)"
check "the detail does not print the value" never_says "Long text about the transcriber. Needs a restart. deepgram"
reset
run $'\n' LOG_LEVEL
check "a setting with no description still works" says "Now:"
unset DMBOT_SETTING_DESCRIPTIONS

# Every setting the real menu lists has a description, so a new setting cannot appear without one.
real_root=$(cd "$(dirname "$script")/.." && pwd)
real_out=$(printf '\n' | DMBOT_ENV_FILE="$work/real.env" DMBOT_ENV_EXAMPLE="$real_root/.env.example" \
  DMBOT_CONFIG_SOURCE="$real_root/core/src/dmbot/config.py" \
  DMBOT_SETTING_DESCRIPTIONS="$real_root/scripts/setting-descriptions" "$script" 2>&1 || true)
listed_names=$(sed -nE 's/^ +[0-9]+\) ([A-Z][A-Z0-9_]*) .*/\1/p' <<<"$real_out")
missing=""
for name in $listed_names; do
  grep -q "^$name|[^|]\+|[^|]\+$" "$real_root/scripts/setting-descriptions" || missing+=" $name"
done
code=0
out="no description for:$missing"
check "the real menu lists settings" test "$(wc -l <<<"$listed_names")" -gt 50
check "every listed setting has a short and a detailed description" test -z "$missing"
stale=""
while IFS='|' read -r name _; do
  [[ $name == \#* || -z $name ]] && continue
  grep -qx "$name" <<<"$listed_names" || stale+=" $name"
done <"$real_root/scripts/setting-descriptions"
out="described but not in the menu:$stale"
check "no description is left for a setting the menu does not list" test -z "$stale"

printf '%d passed, %d failed\n' "$passed" "$failed"
[[ $failed -eq 0 ]]
