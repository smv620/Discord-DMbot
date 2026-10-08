"""What the offline tools share: where the repo's test scripts and testing log are, the
commit being measured, and the public log's layout. Only from a checkout (pip install
-e): an installed copy has no docs/ next to it."""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
HISTORY = REPO / "docs" / "testing-history.log"
TEST_SCRIPTS = REPO / "docs" / "test-scripts"

_SHA = re.compile(r"[0-9a-f]{4,40}")


def commit_id(value: str | None) -> str | None:
    """A commit id, shortened, or None: nothing else may go in the public log."""
    if not value:
        return None
    value = value.strip().casefold()
    return value[:7] if _SHA.fullmatch(value) else None


def commit(given: str | None = None, *, tool: str = "replay") -> str:
    """Which code was measured, for the record: `--commit`, then GIT_COMMIT (a container
    has no git checkout to ask), then git itself. Only a commit id goes in the public log."""
    if found := commit_id(given):
        return found
    if found := commit_id(env := os.environ.get("GIT_COMMIT")):
        return found
    if env:
        print(f"{tool}: GIT_COMMIT isn't a commit id; ignoring it", file=sys.stderr)
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def public_name(path: Path, *, other: str = "a recording") -> str:
    """A file's name for the public log: only the repo's own test files are named, since
    a file of someone's own could be named after them."""
    try:
        path.resolve().relative_to(TEST_SCRIPTS)
    except ValueError:
        return other
    return path.name


def history_entry(
    lines: list[str], *, title: str, kind: str = "Twin run", today: dt.date | None = None
) -> str:
    """An entry for docs/testing-history.log, in its usual layout."""
    day = (today or dt.date.today()).isoformat()
    rule = "=" * 78
    body = "\n".join(f"  {line}" for line in lines)
    return f"\n{rule}\n{day}  {kind}: {title}\n{rule}\n{body}\n"
