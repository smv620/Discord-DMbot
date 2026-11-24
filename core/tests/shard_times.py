"""Refresh `shard_times.json` from CI logs (#5228): `python -m tests.shard_times part1.log ...`.

Reads the "slowest durations" lines each part prints (`--durations=0 --durations-min=1.0`) and sums
the seconds for each test file; the file is rewritten sorted, one number for each file, so a refresh
is a small diff. Pure text in, one JSON file out.
"""

from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

from tests import shard

LINE = re.compile(r"(\d+\.\d+)s (?:call|setup|teardown)\s+tests/(?:[\w/]+/)?(\w+\.py)::")


def sum_logs(texts: list[str]) -> dict[str, float]:
    per: collections.defaultdict[str, float] = collections.defaultdict(float)
    for text in texts:
        for line in text.splitlines():
            if match := LINE.search(line):
                per[match[2]] += float(match[1])
    return {name: round(seconds, 1) for name, seconds in sorted(per.items())}


def main(paths: list[str]) -> None:
    texts = [pathlib.Path(p).read_text(encoding="utf-8", errors="ignore") for p in paths]
    shard.TIMES_FILE.write_text(json.dumps(sum_logs(texts), indent=0) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
