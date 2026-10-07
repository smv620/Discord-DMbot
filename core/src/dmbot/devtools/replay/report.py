"""The twin's report: the same record a tester writes for a live run
(docs/test-scripts/README.md, "Record"), plus what was heard."""

from __future__ import annotations

import datetime as dt
import statistics

from dmbot.devtools.replay.run import Replay
from dmbot.devtools.replay.score import Score
from dmbot.devtools.replay.script import Part, Script
from dmbot.transcription.base import MIN_UTTERANCE_S


def record(
    *,
    script: Script,
    recording: str,
    engine: str,
    commit: str,
    result: Replay,
    score: Score,
) -> list[str]:
    """The README's record block, filled in. Numbers only: no names, nothing heard."""
    sent = [h for h in result.heard if h.written_down]
    longest = max((h.seconds for h in sent), default=0.0)
    pieces = f"pieces of speech: {len(sent)} (longest {longest:.1f} s)"
    if (short := len(result.heard) - len(sent)) > 0:
        pieces += f", plus {short} under {MIN_UTTERANCE_S:g} s (not written down)"
    part1 = score.part1
    terms = f"{len(score.terms_right)} of {score.terms_total} right"
    if score.terms_missed:
        terms += f" (missed: {', '.join(score.terms_missed)})"
    first = (
        "none"
        if not score.first_word_errors
        else ", ".join(f'"{word}"' for word in score.first_word_errors)
    )
    if any(h.text for h in result.heard):
        pauses = (
            f"dramatic pauses: {score.pauses_split} of {score.pauses} split the speech; "
            f"first-word errors after them: {first} (a recording's pauses are quiet, "
            "not muted)"
        )
    else:
        pauses = f"dramatic pauses: {score.pauses} (no text, so splits can't be checked)"
    lines = [
        f"script: {script.name}   recording: {recording}   commit: {commit}",
        f"engine: {engine}   name hints: {result.hints}",
        "audio: replayed without Discord or ears, so none was lost",
        pieces,
        f"part 1: {part1.wrong} wrong, {part1.missing} missing, {part1.added} added "
        f"(of {script.count(Part.ONE)}, whisper not included)",
        f"part 2: {terms}",
        f"whisper: {score.whisper}",
        pauses,
        f"failed: {result.failed}   skipped: {result.skipped}   dropped: {result.dropped}",
    ]
    if not result.finished:
        lines.append("NOT FINISHED: some pieces weren't written down in time; scores are low")
    if result.realtime:
        waits = sorted(h.wait_s for h in sent if h.text is not None)
        if waits:
            lines.append(
                f"delay: about {statistics.median(waits):.1f} s from the end of a piece to "
                f"its text (longest {waits[-1]:.1f} s)"
            )
    else:
        speed = result.speech_s / result.took_s if result.took_s else 0.0
        lines.append(
            f"time: {result.took_s:.1f} s to write down {result.speech_s:.1f} s of speech "
            f"({speed:.1f}x; use --realtime for the delay a table would see)"
        )
    return lines


def heard_lines(result: Replay) -> list[str]:
    return [
        f"{h.start_ms / 1000:6.1f}-{h.end_ms / 1000:5.1f} s  {h.text or '(no text)'}"
        for h in result.heard
    ]


def history_entry(lines: list[str], *, title: str, today: dt.date | None = None) -> str:
    """An entry for docs/testing-history.log, in its usual layout."""
    day = (today or dt.date.today()).isoformat()
    rule = "=" * 78
    body = "\n".join(f"  {line}" for line in lines)
    return f"\n{rule}\n{day}  Twin run: {title}\n{rule}\n{body}\n"
