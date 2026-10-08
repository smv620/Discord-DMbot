"""The twin's report: the same record a tester writes for a live run
(docs/test-scripts/README.md, "Record"), plus what was heard."""

from __future__ import annotations

import datetime as dt
import statistics

from dmbot.devtools.replay.bakeoff import BakeoffScore, bakeoff_record
from dmbot.devtools.replay.run import Replay
from dmbot.devtools.replay.score import Score
from dmbot.devtools.replay.script import Part, Script
from dmbot.transcription.base import MIN_UTTERANCE_S


def _header(
    name: str, recording: str, engine: str, commit: str, result: Replay, cut: str
) -> list[str]:
    sent = [h for h in result.heard if h.written_down]
    longest = max((h.seconds for h in sent), default=0.0)
    pieces = f"pieces of speech: {len(sent)} (longest {longest:.1f} s)"
    if (short := len(result.heard) - len(sent)) > 0:
        pieces += f", plus {short} under {MIN_UTTERANCE_S:g} s (not written down)"
    lines = [
        f"script: {name}   recording: {recording}   commit: {commit}",
        f"engine: {engine}   name hints: {result.hints}",
        "audio: replayed without Discord or ears, so none was lost",
    ]
    if cut:
        lines.append(f"cut: {cut}")
    return [*lines, pieces]


def speech_share(recording_s: float, sent_s: float) -> str:
    """How much of the recording went to the engine as speech, against its length: what an
    outside engine charges for, against the listening time the DM is billed for (#523;
    docs/PLAN.md prices on 36-60 speech-minutes an hour, 60-100%). Overlapping lead-ins
    send some audio twice, so it can pass 100%."""
    share = sent_s / recording_s * 100 if recording_s else 0.0
    return f"speech sent: {sent_s:.1f} s of a {recording_s:.1f} s recording ({share:.0f}%)"


def _footer(result: Replay, recording_s: float) -> list[str]:
    sent = [h for h in result.heard if h.written_down]
    lines = [f"failed: {result.failed}   skipped: {result.skipped}   dropped: {result.dropped}"]
    if recording_s:
        lines.append(speech_share(recording_s, result.sent_s))
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


def record(
    *,
    script: Script,
    recording: str,
    engine: str,
    commit: str,
    result: Replay,
    score: Score,
    cut: str = "",
    recording_s: float = 0.0,
) -> list[str]:
    """The README's record block, filled in. Numbers only: no names, nothing heard."""
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
    body = [
        f"part 1: {part1.wrong} wrong, {part1.missing} missing, {part1.added} added "
        f"(of {script.count(Part.ONE)}, whisper not included)",
        f"part 2: {terms}",
        f"whisper: {score.whisper}",
        pauses,
    ]
    return [
        *_header(script.name, recording, engine, commit, result, cut),
        *body,
        *_footer(result, recording_s),
    ]


def record_bakeoff(
    *,
    name: str,
    recording: str,
    engine: str,
    commit: str,
    result: Replay,
    score: BakeoffScore,
    cut: str = "",
    recording_s: float = 0.0,
) -> list[str]:
    """The record for the bake-off script: names, the nickname, rules words, false names
    and the everyday lines' word error rate."""
    head = _header(name, recording, engine, commit, result, cut)
    return [*head, *bakeoff_record(score), *_footer(result, recording_s)]


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
