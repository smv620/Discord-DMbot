"""Splits the test run into parts that CI runs side by side (#1984). Pure, so it is tested without
a database.

A test belongs to exactly one part, chosen by its file's name, so every part sees the same split
whichever machine or xdist worker computes it, and a file's tests stay together (the module-level
fixtures they share are built once per part). Nothing is deselected: the parts add up to the
whole run, which `tests/test_shard.py` proves on the real test files.
"""

from __future__ import annotations

import os
import zlib

ENV = "DMBOT_TEST_SHARD"  # "2/3": the second of four parts; unset means the whole run


def parse(value: str | None) -> tuple[int, int] | None:
    """`"2/3"` -> (2, 3). Unset or empty -> None (the whole run). Anything else is an error, so a
    typo in the workflow can't quietly run a smaller set."""
    if not value:
        return None
    try:
        part_text, _, total_text = value.partition("/")
        part, total = int(part_text), int(total_text)
    except ValueError:
        raise ValueError(f"{ENV} must look like 2/3, got {value!r}") from None
    if not 1 <= part <= total:
        raise ValueError(f"{ENV} must look like 2/3 (part, then total), got {value!r}")
    return part, total


def part_of(path: str, total: int) -> int:
    """Which part (1..total) the test file at `path` runs in."""
    return zlib.crc32(path.replace("\\", "/").encode()) % total + 1


def wanted() -> tuple[int, int] | None:
    return parse(os.environ.get(ENV))
