#!/usr/bin/env bash
# resume-session.sh — pick a saved Claude Code session and resume it with Remote Control.
#
# Usage:
#   ./resume-session.sh              # show a menu
#   ./resume-session.sh 2            # resume entry #2 directly
#   ./resume-session.sh RepoManager  # resume by name
#   ./resume-session.sh --list       # list sessions on this PC as table lines

set -euo pipefail

# Every session lives in this project folder.
PROJECT_DIR="$HOME/Discord-DMbot"

# ---------------------------------------------------------------------------
# Lookup table: "Display Name|session-id|project directory"
# Run with --list to print lines in this format, then paste them here.
# ---------------------------------------------------------------------------
SESSIONS=(
  "RepoManager|8d015ee4-cbd0-463d-af53-81d1249e0a98|$HOME/Discord-DMbot"
  "🖥️ 🐧dev1|23692819-d679-4f24-a028-112dcddfe3a3|$HOME/Discord-DMbot"
)

# Print every saved session on this PC as a table line, newest first.
list_sessions() {
  local files=() file id title
  shopt -s nullglob
  files=( "$HOME"/.claude/projects/*/*.jsonl )
  shopt -u nullglob

  if (( ${#files[@]} == 0 )); then
    echo "No sessions found in $HOME/.claude/projects." >&2
    return 1
  fi

  echo "# Paste the lines you want into the SESSIONS table:"
  ls -1dt "${files[@]}" | grep -v "worktrees-bridge" | while read -r file; do
    id="$(basename "$file" .jsonl)"
    # A rename writes a new customTitle, so use the latest one; otherwise the first title.
    title="$(grep -aoE '"customTitle":"[^"]+"' "$file" | tail -n 1 | cut -d'"' -f4 || true)"
    if [[ -z "$title" ]]; then
      title="$(grep -aoE '"title":"[^"]+"' "$file" | head -n 1 | cut -d'"' -f4 || true)"
    fi
    echo "  \"${title:-[No custom name set]}|$id|\$HOME/Discord-DMbot\""
  done
}

resume() {
  local entry="$1" name id dir
  IFS='|' read -r name id dir <<< "$entry"
  dir="${dir:-$PROJECT_DIR}"

  if ! command -v claude >/dev/null 2>&1; then
    echo "Error: 'claude' is not on your PATH." >&2
    exit 1
  fi
  if [[ ! -d "$dir" ]]; then
    echo "Error: directory '$dir' for '$name' does not exist." >&2
    exit 1
  fi

  echo "Resuming '$name' ($id) in $dir"
  cd "$dir"
  exec claude --resume "$id" --remote-control --name "$name"
}

# Command-line arguments: --list, a number, or an exact name
if [[ $# -gt 0 ]]; then
  arg="$*"
  case "$arg" in
    -l|--list) list_sessions; exit $? ;;
  esac
  if [[ "$arg" =~ ^[0-9]+$ ]] && (( arg >= 1 && arg <= ${#SESSIONS[@]} )); then
    resume "${SESSIONS[arg-1]}"
  fi
  for entry in "${SESSIONS[@]}"; do
    [[ "${entry%%|*}" == "$arg" ]] && resume "$entry"
  done
  echo "Error: no session matching '$arg'." >&2
  exit 1
fi

# Interactive menu
LIST_OPTION="📋 List sessions on this PC"
names=()
for entry in "${SESSIONS[@]}"; do
  names+=("${entry%%|*}")
done
names+=("$LIST_OPTION")

echo "Which session do you want to resume with Remote Control?"
PS3="Enter a number (Ctrl+C to cancel): "
select choice in "${names[@]}"; do
  if [[ "${choice:-}" == "$LIST_OPTION" ]]; then
    list_sessions
    exit $?
  elif [[ -n "${choice:-}" ]]; then
    resume "${SESSIONS[REPLY-1]}"
  fi
  echo "Invalid choice, try again."
done
