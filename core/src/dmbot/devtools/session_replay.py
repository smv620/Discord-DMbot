"""Replays a saved live test through the twin and says what changed (#1020).

    python -m dmbot.devtools.replay --session FOLDER [--transcriber ENGINE] [--no-timing]

A saved session (made by the live test recorder, dmbot.test_recording, #1019) is a folder with
`session.json` and one FLAC file per utterance under `audio/`. Every speaker in it has a
made-up number; there are no Discord ids, names or server ids anywhere in it, and this tool
refuses a folder that has any.

`session.json` (format 1, as the recorder writes it):

    {"format": 1, "started": "2026-10-10 05:10 UTC", "settings": {},
     "speakers": [{"speaker": 1001, "role": "DM", "voice": "v-0123456789ab"}],
     "utterances": [{"file": "audio/0001-1001.flac", "speaker": 1001,
                     "start_ms": 1000, "end_ms": 3400}],
     "consent_events": [{"at_ms": 9000, "speaker": 1002, "event": "stopped saving"}],
     "produced": {"transcript": [{"at_ms": 1000, "speaker": 1001, "text": "..."}],
                  "shown": [{"at_ms": 5000, "speaker": 1001, "kind": "sidebar", "text": "..."}]},
     "gaps": 0, "ended": true}

`kept.json` is written by `test_library keep`: {"name", "note", "kept_at", "incomplete"} (and,
if someone adds it, "expected": {"transcript": [...]} to judge better or worse). A case is
complete when the session ended, lost no utterance (`gaps`), is not marked incomplete, and
every file the manifest names is there.

The replay uses the twin's real speech path (the Segmenter, the pipeline's consent checks,
the configured engine) with each utterance at its recorded time, overlaps and consent stops
as recorded. It reads only the recordings folder it is given.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dmbot.devtools.replay import audio
from dmbot.devtools.replay.run import ConsentChange, Replay, replay
from dmbot.devtools.replay.score import align, words
from dmbot.test_recording import files
from dmbot.transcription.base import Transcriber

# A Discord id is 17 to 20 digits. The saved sessions use made-up ids (1001 and up), so
# anything shaped like a real one means the folder was not made by the recorder: refuse it
# rather than copy it anywhere (the repo is public).
DISCORD_ID = re.compile(r"(?<!\d)\d{17,20}(?!\d)")
# The bot's own container has the bot's token; a replay there would compete with a live
# session for CPU and must not run (same rule as scripts/replay).
BOT_ENV = "DISCORD_TOKEN"


class SessionError(ValueError):
    """The folder is not a usable saved session."""


@dataclass(frozen=True, slots=True)
class Line:
    speaker: int
    start_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class Utterance:
    speaker: int
    start_ms: int
    end_ms: int
    file: str


@dataclass(frozen=True, slots=True)
class Shown:
    """Something DMbot showed or answered in the live session (a sidebar question, say)."""

    speaker: int
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class Produced:
    """What the live session produced: the transcript and what was shown. The recorder saves
    no alerts, so `alerts` stays empty (today's are only listed)."""

    transcript: tuple[Line, ...] = ()
    shown: tuple[Shown, ...] = ()
    alerts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Session:
    folder: Path
    speakers: dict[int, str]
    utterances: tuple[Utterance, ...]
    changes: tuple[ConsentChange, ...]
    produced: Produced
    expected: Produced | None = None  # from kept.json
    name: str = ""
    kept: bool = False
    incomplete: bool = False
    missing: tuple[str, ...] = ()  # files the manifest names that are not there
    ended: bool = True  # the session finished normally
    gaps: int = 0  # utterances the recorder could not save

    @property
    def complete(self) -> bool:
        return (
            self.ended
            and not self.gaps
            and not self.incomplete
            and not self.missing
            and bool(self.utterances)
        )


def _lines(raw: Any) -> tuple[Line, ...]:
    return tuple(Line(int(r["speaker"]), int(r["at_ms"]), str(r["text"])) for r in raw or ())


def _produced(raw: Any) -> Produced:
    raw = raw or {}
    shown = tuple(
        Shown(int(r["speaker"]), str(r.get("kind", "")), str(r["text"]))
        for r in raw.get("shown") or ()
    )
    return Produced(_lines(raw.get("transcript")), shown)


def _consent(raw: Any) -> tuple[ConsentChange, ...]:
    """The recorder writes only "stopped saving" today; a later "agreed" would be a yes."""
    out = []
    for event in raw or ():
        kind = str(event.get("event", ""))
        if kind.startswith("stopped"):
            out.append(ConsentChange(int(event["at_ms"]), int(event["speaker"]), False))
        elif kind.startswith(("agreed", "started")):
            out.append(ConsentChange(int(event["at_ms"]), int(event["speaker"]), True))
    return tuple(out)


def load(folder: Path) -> Session:
    """Read a saved session. Raises SessionError for anything unusable, including a manifest
    holding something shaped like a Discord id."""
    try:
        text = (folder / files.MANIFEST).read_text(encoding="utf-8")
    except OSError as exc:
        raise SessionError(f"{folder.name}: no session.json ({exc.strerror})") from exc
    kept_text = ""
    try:
        if (folder / files.KEPT).is_file():
            kept_text = (folder / files.KEPT).read_text(encoding="utf-8")
    except OSError as exc:
        raise SessionError(f"{folder.name}: kept.json can't be read ({exc.strerror})") from exc
    if DISCORD_ID.search(text) or DISCORD_ID.search(kept_text):
        raise SessionError(f"{folder.name}: the manifest holds a number shaped like a Discord id")
    try:
        data = json.loads(text)
        kept = json.loads(kept_text) if kept_text else {}
        if not isinstance(data, dict) or not isinstance(kept, dict):
            raise SessionError(f"{folder.name}: session.json is not in the saved format")
        if not isinstance(data.get("speakers", []), list):
            raise SessionError(f"{folder.name}: session.json is not in the saved format")
        if data.get("format") != files.FORMAT:
            raise SessionError(f"{folder.name}: unknown manifest format")
        utterances = tuple(
            Utterance(int(u["speaker"]), int(u["start_ms"]), int(u["end_ms"]), str(u["file"]))
            for u in data.get("utterances", ())
        )
        changes = _consent(data.get("consent_events"))
        speakers = {int(x["speaker"]): str(x["role"]) for x in data.get("speakers", ())}
        produced = _produced(data.get("produced"))
        expected = _produced(kept["expected"]) if "expected" in kept else None
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, SessionError):
            raise
        raise SessionError(f"{folder.name}: session.json is not in the saved format") from exc
    for u in utterances:
        # Exactly audio/<plain name>: a file of this session, never a path out of it.
        where, _, leaf = u.file.partition("/")
        if where != files.AUDIO or not leaf or Path(leaf).name != leaf or leaf.startswith("."):
            raise SessionError(f"{folder.name}: {u.file!r} is not a plain file name")
    missing = tuple(u.file for u in utterances if not (folder / u.file).is_file())
    try:
        name, incomplete = str(kept.get("name", "")), bool(kept.get("incomplete", False))
    except AttributeError as exc:  # unreachable once kept is a dict; keeps mypy and readers calm
        raise SessionError(f"{folder.name}: kept.json is not in the saved format") from exc
    return Session(
        folder=folder,
        speakers=speakers,
        utterances=utterances,
        changes=changes,
        produced=produced,
        expected=expected,
        name=name,
        kept=bool(kept),
        incomplete=incomplete,
        missing=missing,
        ended=bool(data.get("ended")),
        gaps=int(data.get("gaps", 0)),
    )


def pieces(session: Session) -> list[audio.Piece]:
    """Each utterance's saved speech as the frames ears would send, at its recorded time:
    overlaps between speakers stay overlaps, because the clock is the session's."""
    out = []
    for u in session.utterances:
        pcm = audio.decode(session.folder / u.file)
        frames = tuple(
            (u.start_ms + i // audio.FRAME_BYTES * audio.FRAME_MS, pcm[i : i + audio.FRAME_BYTES])
            for i in range(0, len(pcm) - audio.FRAME_BYTES + 1, audio.FRAME_BYTES)
        )
        if not frames:  # a file that decodes to nothing is damaged, not silent
            raise audio.DecodeError(f"{u.file} holds no sound")
        out.append(audio.Piece(frames, u.speaker))
    return out


async def run(
    session: Session, transcriber: Transcriber, *, realtime: bool = True, outside: bool = False
) -> Replay:
    """Today's replay of a session: at its original times unless `realtime` is off."""
    return await replay(
        pieces(session),
        transcriber,
        realtime=realtime,
        outside=outside,
        changes=session.changes,
    )


@dataclass(frozen=True, slots=True)
class Changed:
    speaker: int
    start_ms: int
    before: str
    after: str


@dataclass(slots=True)
class Diff:
    """Saved against today, as numbers and the lines that differ. Never printed to the log:
    the lines are a player's words."""

    same: int = 0
    changed: list[Changed] = field(default_factory=list)
    added: list[Line] = field(default_factory=list)  # heard today, not then
    lost: list[Line] = field(default_factory=list)  # heard then, not today
    alerts_gained: list[str] = field(default_factory=list)
    alerts_lost: list[str] = field(default_factory=list)
    errors_then: int | None = None  # against kept.json's expected transcript
    errors_now: int | None = None
    verdict: str = "same"  # same, better, worse or changed


def word_errors(reference: str, heard: str) -> int:
    """Words wrong, missing or added: the twin's own word comparison."""
    return sum(s.kind != "ok" for s in align(words(reference), words(heard)))


def _by_speaker(lines: Sequence[Line]) -> dict[int, list[Line]]:
    out: dict[int, list[Line]] = {}
    for line in sorted(lines, key=lambda x: x.start_ms):
        out.setdefault(line.speaker, []).append(line)
    return out


def _delta(then: Sequence[str], now: Sequence[str]) -> tuple[list[str], list[str]]:
    """What is in `now` and not in `then` (gained), and the other way (lost), by words."""
    before = Counter(" ".join(words(t)) for t in then)
    after = Counter(" ".join(words(t)) for t in now)
    return list((after - before).elements()), list((before - after).elements())


def compare(
    saved: Produced,
    today: Sequence[Line],
    today_alerts: Sequence[str],
    expected: Produced | None = None,
) -> Diff:
    """Each speaker's saved lines against today's, in order: a line is the same, changed,
    or (when the counts differ) lost or added. Alerts are compared as a set of texts.
    With `expected` (kept.json), a verdict of better or worse: fewer or more wrong words
    than the saved transcript had, against what the tester said was right."""
    diff = Diff()
    then, now = _by_speaker(saved.transcript), _by_speaker(today)
    for speaker in sorted(then.keys() | now.keys()):
        a, b = then.get(speaker, []), now.get(speaker, [])
        for old, new in zip(a, b, strict=False):
            if words(old.text) == words(new.text):
                diff.same += 1
            else:
                diff.changed.append(Changed(speaker, new.start_ms, old.text, new.text))
        diff.lost += a[len(b) :]
        diff.added += b[len(a) :]
    diff.alerts_gained, diff.alerts_lost = _delta(saved.alerts, today_alerts)
    if expected is not None and expected.transcript:
        diff.errors_then = _errors(expected.transcript, saved.transcript)
        diff.errors_now = _errors(expected.transcript, today)
    return _with_verdict(diff)


def _errors(expected: Sequence[Line], heard: Sequence[Line]) -> int:
    total = 0
    exp, got = _by_speaker(expected), _by_speaker(heard)
    for speaker in exp.keys() | got.keys():
        say = " ".join(x.text for x in exp.get(speaker, []))
        heard_text = " ".join(x.text for x in got.get(speaker, []))
        total += word_errors(say, heard_text)
    return total


def _with_verdict(diff: Diff) -> Diff:
    if diff.errors_then is not None and diff.errors_now is not None:
        if diff.errors_now < diff.errors_then:
            diff.verdict = "better"
        elif diff.errors_now > diff.errors_then:
            diff.verdict = "worse"
        elif diff.changed or diff.added or diff.lost:
            diff.verdict = "changed"
        return diff
    if diff.changed or diff.added or diff.lost:
        diff.verdict = "changed"
    return diff


def today_lines(result: Replay) -> list[Line]:
    return [Line(h.speaker, h.start_ms, h.text) for h in result.heard if h.text]


def summary(diff: Diff) -> str:
    """One line of numbers: safe to log."""
    line = (
        f"{diff.verdict}: {diff.same} same, {len(diff.changed)} changed, "
        f"{len(diff.lost)} lost, {len(diff.added)} added"
    )
    line += f"; alerts +{len(diff.alerts_gained)} -{len(diff.alerts_lost)}"
    if diff.errors_then is not None:
        line += f"; wrong words {diff.errors_then} then, {diff.errors_now} now"
    return line


def report(diff: Diff, session: Session) -> list[str]:
    """The short diff for the screen (it shows the words, so it never goes in the log)."""
    names = session.speakers
    out = [summary(diff)]
    for c in diff.changed:
        out.append(f"  changed {names.get(c.speaker, c.speaker)} at {c.start_ms / 1000:.1f} s:")
        out.append(f"    then: {c.before}")
        out.append(f"    now:  {c.after}")
    for lost in diff.lost:
        out.append(f"  lost {names.get(lost.speaker, lost.speaker)}: {lost.text}")
    for added in diff.added:
        out.append(f"  added {names.get(added.speaker, added.speaker)}: {added.text}")
    # Alerts are shown, not judged: the twin makes only the pipeline's own (a failed
    # transcription), never the rules engine's, so a live session's rules alerts always
    # read as lost.
    out += [f"  alert gained: {a}" for a in diff.alerts_gained]
    out += [f"  alert lost (rules alerts are not replayed): {a}" for a in diff.alerts_lost]
    if session.produced.shown:
        out.append("  (Sidebar answers and rules cards are not replayed, so not compared.)")
    return out


def refuse_in_bot(env: dict[str, str] | None = None) -> str | None:
    """Why a replay must not run here, or None. The bot's container has its token."""
    if (env if env is not None else os.environ).get(BOT_ENV):
        return (
            f"{BOT_ENV} is set, so this looks like the bot's own container. Replays never run "
            "there or during a live session: use the host's venv or a throwaway container."
        )
    return None


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="replay --session", description="Replay a saved live test and diff it."
    )
    parser.add_argument("--session", type=Path, required=True, help="a saved session's folder")
    parser.add_argument("--transcriber", help="overrides TRANSCRIBER")
    parser.add_argument(
        "--no-timing",
        action="store_true",
        help="queue everything at once instead of at the recorded times (the diff is the same)",
    )
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    from dmbot.transcription.base import TranscriberUnavailable
    from dmbot.transcription.config import TranscriptionConfigError, load_transcription_settings
    from dmbot.transcription.factory import build_transcriber

    if why := refuse_in_bot():
        print(f"replay: {why}", file=sys.stderr)
        return 2
    env = dict(os.environ)
    if args.transcriber:
        env["TRANSCRIBER"] = args.transcriber
    try:
        settings = load_transcription_settings(env)
        session = load(args.session)
    except (TranscriptionConfigError, SessionError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    if settings.sends_audio_out and args.transcriber != settings.engine:
        print(
            f"replay: TRANSCRIBER={settings.engine} sends audio to an outside company and "
            f"costs money; say so with --transcriber {settings.engine}",
            file=sys.stderr,
        )
        return 2
    if not session.complete:
        from dmbot.devtools.test_library import why_incomplete

        print(
            f"replay: {session.folder.name} can't be replayed: {why_incomplete(session)}",
            file=sys.stderr,
        )
        return 2
    try:
        result = await run(
            session,
            build_transcriber(settings),
            realtime=not args.no_timing,
            outside=settings.sends_audio_out,
        )
    except (TranscriberUnavailable, audio.DecodeError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    diff = compare(session.produced, today_lines(result), result.alerts, session.expected)
    print("\n".join(report(diff, session)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
