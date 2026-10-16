"""How often the Transcript Cleaner lands near its likeness thresholds on a kept session (#1577):

    python -m dmbot.devtools.cleaner_close_calls --names NAMES_FILE SOURCE

`SOURCE` is a transcript file (one spoken line per row) or a kept test session folder from the
test library (`dmbot.devtools.test_library`; its produced transcript lines are read). The names
file is the campaign's names in the Add many format, built into the same in-memory lookup the bot
uses, so the real Cleaner runs exactly as at a table (the session's vocabulary and scene are built
up line by line). It prints the same counts the bot logs at a session's end, the people counted
apart, and the ten nearest misses as heard-word / candidate / score, for dev1 to read on the
owner's recordings. Run where the recordings are; never put its output in GitHub: the misses are
words players said. Not in the bot's own container (same rule as the replay tools).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from dmbot.devtools.replay.names import SPEAKER, Known, known_from_text
from dmbot.devtools.session_replay import SessionError, load, refuse_in_bot
from dmbot.memory.scene import SceneTracker, mentions
from dmbot.transcript.cleaner import Vocabulary, clean
from dmbot.transcript.close_calls import CloseCalls


def read_lines(source: Path) -> list[str]:
    """The spoken lines of a transcript file, or of a kept session folder's produced transcript."""
    if source.is_dir():
        return [line.text for line in load(source).produced.transcript]
    return [row.strip() for row in source.read_text(encoding="utf-8").splitlines() if row.strip()]


def count(known: Known, lines: Sequence[str], *, seconds_apart: float = 5.0) -> CloseCalls:
    """Run the Cleaner over `lines`, a few seconds apart, and count the close calls with the
    words kept for the ten nearest misses."""
    calls = CloseCalls(keep_words=True)
    vocabulary = Vocabulary()
    tracker = SceneTracker()
    for index, text in enumerate(lines):
        now = index * seconds_apart
        cleaned = clean(
            known.lookup,
            text,
            vocabulary=vocabulary,
            scene=tracker.scene(now).keys(),
            close_calls=calls,
        ).text
        tracker.note(mentions(known.lookup, cleaned), SPEAKER, now)
        vocabulary.note(SPEAKER, text)  # after cleaning: a line is never evidence about itself
    return calls


def render(calls: CloseCalls) -> str:
    out = [calls.report()]
    if calls.nearest:
        out.append("Nearest misses (heard / candidate / score, needed):")
        out += [
            f"  {m.heard} / {m.candidate} / {m.score:.2f}, {m.threshold:.2f}" for m in calls.nearest
        ]
    else:
        out.append("No misses within 0.05 of a threshold.")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cleaner_close_calls", description=__doc__)
    parser.add_argument("--names", required=True, type=Path, help="the campaign's names file")
    parser.add_argument("source", type=Path, help="a transcript file or a kept session folder")
    args = parser.parse_args(argv)
    why = refuse_in_bot()
    if why is not None:
        print(why, file=sys.stderr)
        return 2
    try:
        known = known_from_text(args.names.read_text(encoding="utf-8"))
        lines = read_lines(args.source)
    except (OSError, ValueError, SessionError) as exc:
        print(f"Can't read it: {exc}", file=sys.stderr)
        return 1
    calls = count(known, lines)
    if calls.nearest:
        print("Contains words players said: do not paste this into GitHub.", file=sys.stderr)
    print(render(calls))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
