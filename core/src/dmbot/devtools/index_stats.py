"""What the rules index costs to build and to ask (#1953): build time, entries and keys, memory
and the time of a sidebar question, so a growth in the data shows up as a number.

    python -m dmbot.devtools.index_stats [--runs 5] [--questions 1000]

The bot builds the index once when it starts and asks it on every rules lookup, on a small
server beside Postgres and Whisper, so these numbers are worth keeping an eye on. The CI budgets
in tests/test_index_budget.py use the same functions. Nothing here touches a database or Discord.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import tracemalloc
from collections import Counter
from pathlib import Path

from dmbot.rules import index
from dmbot.sidebar import context

GOLDEN = Path(__file__).resolve().parents[3] / "tests" / "golden" / "rules.json"
# Used when the golden file isn't there (an installed package): a spread of kinds, both editions.
FALLBACK_QUESTIONS = (
    ("how much AC does shield give", "2024", "2014"),
    ("how does grappling work", "2014", "2024"),
    ("what's the damage of a longsword", "2024", "2014"),
    ("the dwarf bartender", "2024", "2014"),
)


def build_seconds(runs: int = 5) -> float:
    """The median seconds to build the index, over `runs` builds (not the cached one)."""
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        index.build_srd()
        times.append(time.perf_counter() - start)
    return statistics.median(times)


def peak_megabytes() -> float:
    """Python's peak allocation, in megabytes, while building the index once (tracemalloc)."""
    already = tracemalloc.is_tracing()  # (under -X tracemalloc, leave the caller's trace running)
    if not already:
        tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        built = index.build_srd()
        _, peak = tracemalloc.get_traced_memory()
        del built
    finally:
        if not already:
            tracemalloc.stop()
    return peak / 1_000_000


def questions() -> list[tuple[str, str, str]]:
    """(question, target, fallback) for each golden case, or a short built-in list."""
    try:
        cases = json.loads(GOLDEN.read_text("utf-8"))["cases"]
    except OSError:
        return list(FALLBACK_QUESTIONS)
    return [(c["question"], c["target"], c["fallback"]) for c in cases if c["mode"] == "dm"]


def milliseconds_per_question(rules: index.Index, count: int = 1000) -> float:
    """The mean milliseconds of `mentioned_rules` over `count` calls, cycling the questions."""
    asked = questions()
    if not asked:
        raise ValueError("no questions to ask")
    start = time.perf_counter()
    for i in range(count):
        question, target, fallback = asked[i % len(asked)]
        context.mentioned_rules(question, rules, target, fallback)
    return (time.perf_counter() - start) * 1000 / count


def counts(rules: index.Index) -> Counter[tuple[str, str]]:
    """Entries per (edition, kind)."""
    return Counter((e.edition, e.kind) for e in rules.entries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--questions", type=int, default=1000)
    args = parser.parse_args()
    rules = index.srd()
    print(f"build time (median of {args.runs}): {build_seconds(args.runs) * 1000:.0f} ms")
    print(f"peak memory while building: {peak_megabytes():.1f} MB")
    print(f"sidebar question: {milliseconds_per_question(rules, args.questions):.3f} ms each")
    keys = rules.key_counts()
    per_kind = counts(rules)
    for edition in sorted(keys):
        print(f"edition {edition}: {keys[edition]} names and spellings")
        for (found, kind), n in sorted(per_kind.items()):
            if found == edition:
                print(f"  {kind}: {n}")


if __name__ == "__main__":
    main()
