"""The session reader's new story threads (#4522, quests slice 5a; design `docs/design/quests.md`
§9.2, §9.5 to §9.7 and Supervisor's ruling on #4522): after a session, never during play, the
quests, pursuits and hooks the table's talk starts, for the DM to keep or turn down on the 📜 card.

The lore reader's rules (`thread_read`) with a transcript in place of a text:
- **Only lines of people who agree,** never a DM sidebar, a table question, DMbot's own or an
  off-topic line (`extractor.readable`), and never last time's recap read aloud. Consent is asked
  again before each call and after it: a suggestion whose speaker stopped meanwhile is dropped.
- **Windows of lines, each line read once.** A window's last lines go with the next one as
  context only (`<already_read>`), so a thread whose words straddle two windows is still seen; a
  thread quoted only in the context is dropped by code, since the earlier window read it.
- **Code decides "already there"** (§9.5): the campaign's threads and every suggestion made before
  (`have`, as the lore reader's) by name or by the same people. The model is told the open threads
  so it leaves them out, but its leaving out is only a hint; a thread the campaign has is never
  suggested twice. Progress on it is slice 5b's.
- **No words of the transcript are kept:** a suggestion holds the numbers of the lines it quotes
  and who said them, and its quotes are emptied. The card reads those lines back when shown.
- **Within the extractor's limits** (§9.6, Supervisor's E): at most `max_calls` calls, each counted
  before it is made; stopped by a limit, the report says so ("stopped early"), never "nothing".

Pure apart from `run` (its calls are the injected AI, consent and counter). Counts only in the
log, never a word of the story.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from dmbot.memory import session_progress, thread_progress, thread_read
from dmbot.memory.extractor import Said as Line
from dmbot.memory.extractor import quoted, transcript_of
from dmbot.memory.models import name_key
from dmbot.memory.thread_read import ReadBranch, ReadPart, Suggestion

log = logging.getLogger(__name__)

WINDOW = 200  # lines a call reads: a scene or two, well inside the model's reach
CONTEXT = 20  # the window before's last lines, sent again as context only, never read for threads
RUN_MAX = 3  # a quote may run over this many lines one after another ("…" and the reply)
KNOWN_MAX = 60  # open threads named to the model; more are still left out by code
CALL_TIMEOUT_S = (
    600  # a whole answer at the generous output floor can be slow; never cut a slow one
)

SESSION_HEAD = """You read the transcript of one session of a tabletop roleplaying game and list \
the NEW story threads the table's talk starts: the quests and the leads that could become one. You \
never invent: every thread must be said in the transcript, and you copy a short quote, exact \
words from one line or from lines next to each other, that shows it.

The transcript is numbered lines, "number speaker: words". The speaker is "DM" (who speaks for \
every character the players don't play) or "Player" (who speaks as their character). One \
character agreeing in front of the others takes a job for the party.

<known_threads> lists the campaign's threads already known: never list one of them again, even \
with new details. <already_read> holds lines read before, given only so the talk makes sense: \
list nothing that is said only there."""

# (the lore reader's shapes, fields and "never" list, word for word, with "the text" made "the
# transcript": one definition of a thread for both readers, §9.6)
SESSION_INSTRUCTIONS = (
    SESSION_HEAD
    + "\n\n"
    + thread_read.INSTRUCTIONS.split("\n\n", 1)[1]
    .replace("the text", "the transcript")
    .replace("The text", "The transcript")
)


def prompt(
    window: Sequence[tuple[str, str, str]],
    context: Sequence[tuple[str, str, str]],
    known: Sequence[str],
    progress: Sequence[session_progress.KnownThread] = (),
) -> str:
    """The user message: the known threads (with their labels and standing parts when progress
    is read, #4522 5b), the context lines and the window, all as data (`extractor.quoted`: no
    line can close its block or pass for another)."""
    names = (
        session_progress.known_block(progress)
        if progress
        else "\n".join(f"- {quoted(n)}" for n in known[:KNOWN_MAX]) or "(none)"
    )
    before = transcript_of(context).replace("transcript>", "already_read>") if context else ""
    return f"<known_threads>\n{names}\n</known_threads>\n{before}\n{transcript_of(window)}".strip()


def windows(lines: Sequence[Line]) -> list[tuple[list[Line], list[Line]]]:
    """The lines in windows of `WINDOW`, each with the `CONTEXT` lines before it (read already)."""
    out: list[tuple[list[Line], list[Line]]] = []
    for start in range(0, len(lines), WINDOW):
        out.append(
            (list(lines[start : start + WINDOW]), list(lines[max(0, start - CONTEXT) : start]))
        )
    return out


def where(quote: str, lines: Sequence[Line]) -> tuple[int, ...]:
    """The numbers of the lines a quote stands in: one line holding it, else the shortest run of
    up to `RUN_MAX` lines next to each other that does. () when it stands nowhere."""
    want = thread_read.plain(quote)
    if not want:
        return ()
    for run in range(1, RUN_MAX + 1):
        for i in range(len(lines) - run + 1):
            chunk = lines[i : i + run]
            if want in thread_read.plain(" ".join(s.text for s in chunk)):
                return tuple(s.number for s in chunk)
    return ()


def _no_words(s: Suggestion, starts: tuple[int, ...], speakers: tuple[int, ...]) -> Suggestion:
    """The suggestion as stored from a session: when its lines began and who said them, and no
    quote."""
    return replace(
        s,
        quote="",
        reads=tuple(replace(p, quote="") for p in s.reads),
        branches=tuple(replace(b, quote="") for b in s.branches),
        clues=(),  # (a session's clues are quests slice 6c's: who found one, with consent, #4624)
        line_starts=starts,
        speaker_ids=speakers,
    )


def check_window(
    said: Iterable[thread_read.Said],
    window: Sequence[Line],
    entries: Mapping[str, Collection[str]],
    names: Mapping[str, str],
    *,
    have: list[tuple[str, frozenset[str]]],
) -> tuple[list[Suggestion], dict[str, int]]:
    """One window's threads, checked by code (pure): the lore reader's checks against the window's
    own lines (a quote found only in the context lines is `no_quote`), then each kept suggestion's
    lines and speakers from its quotes. `have` grows with what is kept, so a later window never
    suggests the same thread again. A quote that stands in several lines points at the first of
    them: its speaker is that line's (good enough for evidence; the DM sees the line itself)."""
    flat = "\n".join(s.text for s in window)
    found, counts = thread_read.check(said, flat, entries, names, have=have)
    # (a session's clues wait for slice 6c, #4624: their drops would only mislead this report)
    counts = {k: n for k, n in counts.items() if not k.startswith("clue_")}
    by_number = {s.number: s for s in window}
    out: list[Suggestion] = []
    for s in found:
        main = where(s.quote, window)
        if not main:  # (the quote ran over more lines than a run: no line to point at)
            counts["no_line"] = counts.get("no_line", 0) + 1
            continue
        numbers = set(main)
        for quote in (*(p.quote for p in s.reads), *(b.quote for b in s.branches)):
            numbers.update(where(quote, window))
        speakers = tuple(sorted({by_number[n].user_id for n in numbers}))
        starts = tuple(sorted({by_number[n].started_ms for n in numbers}))
        out.append(_no_words(s, starts, speakers))
        have.append((name_key(s.name), s.people))
        have.extend((name_key(b.name), frozenset()) for b in s.branches)
    return out, counts


@dataclass(slots=True)
class Report:
    """Counts only: how many windows, calls and suggestions, and why any stopped short."""

    windows: int = 0
    calls: int = 0
    found: int = 0
    stopped: str = ""  # "call limit", "out of funds", "cut", "failed", or "" when whole
    dropped: dict[str, int] = field(default_factory=dict)
    # Progress on the known threads (#4522 5b): (thread id, change, line starts, speakers). Ids and
    # the reader's few words only, never in the log line.
    progress: list[tuple[str, thread_progress.Change, tuple[int, ...], tuple[int, ...]]] = field(
        default_factory=list, repr=False
    )

    def line(self) -> str:
        why = ", ".join(f"{k} {v}" for k, v in sorted(self.dropped.items())) or "none"
        return (
            f"Session story threads: {self.found} suggested from {self.windows} windows in"
            f" {self.calls} calls, {len(self.progress)} progress lines (dropped: {why})"
            + (f"; stopped early: {self.stopped}" if self.stopped else "")
        )


async def run(
    ai: Any,
    lines: Sequence[Line],
    *,
    dm_ids: Collection[int],
    entries: Mapping[str, Collection[str]],
    names: Mapping[str, str],
    have: Iterable[tuple[str, frozenset[str]]],
    known: Sequence[str],
    consenting: Callable[[], Awaitable[Collection[int]]],
    max_calls: int,
    max_tokens: int,
    count_call: Callable[[], Awaitable[None]] | None = None,
    progress: Sequence[session_progress.KnownThread] = (),
    finders: session_progress.Finders | None = None,
    step: Callable[[int, int], Awaitable[None]] | None = None,
) -> tuple[list[Suggestion], Report]:
    """Read the session's readable lines (`extractor.readable`, recap left out by the caller) for
    new threads, window by window. With `progress` (the open threads and their standing parts,
    #4522 5b), the same calls also read what the talk changed on them (`session_progress`), kept
    in `Report.progress`. `finders` (#4624 6c): who may have found a clue, for a "clue found"
    line. `step` is awaited with (window n, of m) before each window's call, so a live session
    can show where the read is (#5124). Never raises: out of funds is reported as a stop."""
    from dmbot.ai import AIOutOfFunds

    report = Report()
    seen = list(have)
    moved: set[tuple[str, str]] = set()  # (the progress lines kept so far this run)
    instructions = SESSION_INSTRUCTIONS + (session_progress.INSTRUCTIONS if progress else "")
    schema = session_progress.schema(thread_read.SCHEMA) if progress else thread_read.SCHEMA
    out: list[Suggestion] = []
    every = windows(lines)
    for n, (window, context) in enumerate(every, 1):
        if report.calls >= max_calls:
            report.stopped = "call limit"
            break
        if step is not None:
            await step(n, len(every))
        agreed = await consenting()
        window = [s for s in window if s.user_id in agreed]  # (a stop removes the lines for good)
        context = [s for s in context if s.user_id in agreed]
        if not window:
            continue
        report.windows += 1

        def rows(part: Sequence[Line]) -> list[tuple[str, str, str]]:
            return [
                (str(s.number), "DM" if s.user_id in dm_ids else "Player", s.text) for s in part
            ]

        report.calls += 1
        if (
            count_call is not None
        ):  # (counted before the call: a crash can't hide it from the limits)
            try:
                await count_call()
            except Exception as exc:
                log.warning("Session threads: couldn't count a call (%s)", type(exc).__name__)
        raw: list[str] = []
        try:
            said, short = await asyncio.wait_for(
                thread_read.read_threads(
                    ai, prompt(rows(window), rows(context), known, progress),
                    max_tokens=max_tokens, instructions=instructions, schema=schema, raw=raw,
                ),
                CALL_TIMEOUT_S,
            )  # fmt: skip
        except AIOutOfFunds:
            report.stopped = "out of funds"
            break
        except Exception as exc:  # (the timeout or the network: this window is lost, never fatal)
            log.warning("Session threads: a call failed (%s)", type(exc).__name__)
            report.stopped = thread_read.FAILED
            continue
        if short:
            report.stopped = "cut" if short == thread_read.STOPPED else short
        found, dropped = check_window(said, window, entries, names, have=seen)
        for k, v in dropped.items():
            report.dropped[k] = report.dropped.get(k, 0) + v
        lines_moved: list[tuple[str, thread_progress.Change, tuple[int, ...], tuple[int, ...]]] = []
        if progress and raw:
            lines_moved, why = session_progress.check(
                session_progress.parse(raw[0]), window, progress, where=where, seen=moved,
                finders=finders,
            )  # fmt: skip
            for k, v in why.items():
                report.dropped[f"progress_{k}"] = report.dropped.get(f"progress_{k}", 0) + v
        agreed = set(await consenting())  # (and after the answer: who stopped meanwhile drops it)
        kept = [s for s in found if set(s.speaker_ids) <= agreed]
        moves = [m for m in lines_moved if set(m[3]) <= agreed]
        if len(kept) + len(moves) < len(found) + len(lines_moved):
            report.dropped["stopped_recording"] = (
                report.dropped.get("stopped_recording", 0)
                + len(found)
                + len(lines_moved)
                - len(kept)
                - len(moves)
            )
        out.extend(kept)
        report.progress.extend(moves)
        moved.update((m[0], m[1].key(m[0])) for m in moves)  # (only what passed consent)
    report.found = len(out)
    log.info(report.line())
    return out, report


__all__ = [
    "CONTEXT",
    "SESSION_INSTRUCTIONS",
    "WINDOW",
    "ReadBranch",
    "ReadPart",
    "Report",
    "check_window",
    "prompt",
    "run",
    "where",
    "windows",
]  # fmt: skip
