"""What the offline tools share: where the repo's test scripts and testing log are, the
commit being measured, and the public log's layout. Only from a checkout (pip install
-e): an installed copy has no docs/ next to it."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import os
import re
import statistics
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dmbot.ai import AIModels, Reply

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


def add_fast_model_arg(parser: argparse.ArgumentParser) -> None:
    """`--fast-model`: the fast tier uses this model for this run only (#2055). Nothing live
    changes; the default is the bot's own fast model."""
    parser.add_argument(
        "--fast-model",
        metavar="MODEL",
        default=None,
        help="use this model for the fast tier in this run only (default: the bot's own)",
    )


def with_fast_model(models: AIModels, fast: str | None) -> AIModels:
    """The bot's models, with the fast tier's replaced for one run when asked."""
    return dataclasses.replace(models, fast=fast) if fast else models


class Metered:
    """Wraps a client and adds up the tokens it spends (a fake client in a test records the model
    id it was built for). Passes every call through unchanged."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.seconds: list[float] = []  # how long each call took, in the order made (#2263)

    @property
    def model(self) -> str:
        return str(getattr(self._client, "model", ""))

    async def complete(self, system: str, text: str, **kwargs: Any) -> Reply:
        self.calls += 1  # counted when made: a call that fails was still a call
        started = time.monotonic()
        try:
            reply: Reply = await self._client.complete(system, text, **kwargs)
        finally:
            self.seconds.append(time.monotonic() - started)
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        return reply


def timing_line(seconds: Sequence[float]) -> str:
    """ "median 1.2 s, slowest 3.4 s over 17 answers", for the rollback rule ("about a second
    slower per answer", #2152); "" when nothing was timed."""
    if not seconds:
        return ""
    ordered = sorted(seconds)
    median = statistics.median(ordered)
    n = len(ordered)
    noun = "call" if n == 1 else "calls"
    return f"median {median:.1f} s, slowest {ordered[-1]:.1f} s over {n} {noun}"
