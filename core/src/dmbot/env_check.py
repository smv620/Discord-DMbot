"""Warn when .env is missing settings that .env.example has (#31).

`git pull` never touches .env, so settings added after someone copied .env.example
quietly fall back to their defaults. At start-up core compares the two and logs one
line naming what's missing. It only warns, never stops the bot, and only ever shows
setting names, never values.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from dotenv import find_dotenv

# An active setting line: NAME=… at the start (comments and blank lines don't count).
_SETTING = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def example_names(text: str) -> list[str]:
    """Setting names in .env.example's text, in order, each once."""
    names: list[str] = []
    for line in text.splitlines():
        match = _SETTING.match(line.strip())
        if match and match[1] not in names:
            names.append(match[1])
    return names


def missing_settings(example: list[str], env: Mapping[str, str]) -> list[str]:
    """Names in .env.example that aren't set at all. A setting left blank counts as set:
    the person saw it and chose the default."""
    return [name for name in example if name not in env]


def warning(missing: list[str]) -> str:
    noun = "setting" if len(missing) == 1 else "settings"
    return (
        f"Your .env is missing {len(missing)} {noun} that are in .env.example: "
        f"{', '.join(missing)}. DMbot is running and uses the default for these. "
        "Copy the lines from .env.example into .env (leave a value blank to keep "
        "the default), then restart."
    )


def find_example() -> Path | None:
    """.env.example in the current folder or the nearest one above it."""
    # usecwd: Docker Compose mounts it at /app/.env.example, core's working folder.
    found = find_dotenv(".env.example", usecwd=True)
    return Path(found) if found else None


def check(env: Mapping[str, str], example: Path | None = None) -> str | None:
    """The warning to log, or None if nothing is missing or .env.example can't be read."""
    path = example or find_example()
    if path is None:
        return None
    try:
        names = example_names(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    missing = missing_settings(names, env)
    return warning(missing) if missing else None
