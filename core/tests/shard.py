"""Splits the test run into parts that CI runs side by side (#1984). Pure, so it is tested without
a database.

A test belongs to exactly one part, chosen by its file's name, so every part sees the same split
whichever machine or xdist worker computes it, and a file's tests stay together (the module-level
fixtures they share are built once per part). Nothing is deselected: the parts add up to the
whole run, which `tests/test_shard.py` proves on the real test files.
"""

from __future__ import annotations

import functools
import json
import os
import pathlib
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


TIMES_FILE = pathlib.Path(__file__).with_name("shard_times.json")
# How long each test file took in CI, in seconds of the tests that ran a second or more (measured
# 2026-11-24, #5228): the parts are dealt out by it so no part is far slower than the others.
# Hashing the file's name dealt the files evenly by count, but a few slow database files landed
# together (one part took 18 minutes while another took 7). A file not in the table (new, or
# quick) is dealt by its name as before, so a new file is always in exactly one part. Refresh the
# table from a CI run's logs with `python -m tests.shard_times <log> ...`.


@functools.cache
def _times() -> dict[str, float]:
    try:
        raw = json.loads(TIMES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): float(v) for k, v in raw.items()}


@functools.cache
def _balanced(total: int) -> dict[str, int]:
    """The measured files dealt into `total` parts, longest first, each to the lightest part so far
    (ties to the lower part). Pure and the same on every machine."""
    loads = [0.0] * total
    out: dict[str, int] = {}
    for name, seconds in sorted(_times().items(), key=lambda kv: (-kv[1], kv[0])):
        lightest = min(range(total), key=lambda i: (loads[i], i))
        loads[lightest] += seconds
        out[name] = lightest + 1
    return out


def part_of(path: str, total: int) -> int:
    """Which part (1..total) the test file at `path` runs in."""
    # (the file's name only: where the run happens does not matter)
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    placed = _balanced(total).get(name)
    if placed is not None:
        return placed
    return zlib.crc32(name.encode()) % total + 1


def wanted() -> tuple[int, int] | None:
    return parse(os.environ.get(ENV))
