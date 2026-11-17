"""What a session's talk changes on story threads the campaign already has (#4522, quests slice
5b; design `docs/design/quests.md` §9.5; Supervisor's ruling on #4522): the session reader's
"progress" lines, read in the same call as its new threads (`session_threads`, ruling E: no extra
call), for the DM to keep or turn down on the 📜 card (`thread_progress`).

The model is shown the open threads and their standing parts by label ("T1", "T1.P2"), never by
id, and says which one changed and how, with a short quote. Everything after that is code:
- the label must be one it was shown, and a part's new state one of that part's kind's;
- the quote must stand in the window's own lines (`session_threads.where`): its lines and their
  speakers are the evidence, and no words of the transcript are kept;
- a thread's own state may only go on to "Happening now", finished or dropped (a hint or "not
  started" is never read from play);
- a time limit is words only: its "when" is never made a date here.

Pure: the AI call and the store are the caller's. Counts only in the log.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory import thread_parts as tp
from dmbot.memory import thread_progress as prog
from dmbot.memory.extractor import Said as Line
from dmbot.memory.extractor import quoted
from dmbot.memory.models import ABANDONED, GOING, RESOLVED, name_key

log = logging.getLogger(__name__)

KNOWN_MAX = 60  # open threads shown to the model, as `session_threads.KNOWN_MAX`
PARTS_SHOWN = 12  # standing parts shown under each thread
PROGRESS_MAX = 30  # lines from one window at most: a flood costs the DM's trust
CLUES_SHOWN = 12  # tied clues shown under each thread (#4624 6c)
NEW_PART, PART_MOVES, THREAD_MOVES = "new_part", "part_moves", "thread_moves"
CLUE_FOUND = "clue_found"  # (#4624, quests slice 6c: the table found a clue to a thread)
THREAD_WORDS = {"happening_now": GOING, "finished": RESOLVED, "dropped": ABANDONED}

INSTRUCTIONS = """

Also list PROGRESS: what the talk changes on a thread in <known_threads>, each thread shown with \
a label ("T1"), its standing parts ("T1.P2 objective: find the map (to_do)") and its clues \
("T1.C1 clue: the torn letter"). Only what the transcript says HAPPENED, never a plan or a guess. \
For each:
- "thread": the thread's label;
- "change": "new_part" (something new: an objective to do, a complication now in the way, a \
reward handed over, help given or lent, a deadline said), "part_moves" (a part shown moved on: \
an objective done or failed, a complication overcome, help returned, a reward settled or turned \
down), "thread_moves" (the whole thread: "happening_now" when a lead is taken up, "finished" \
when it is done, "dropped" when the table gives it up) or "clue_found" (a character found, read, \
or was told a thing or piece of information that bears on the thread's question or aim);
- "part_kind": for a new part, "objective", "complication", "reward_given", "help" or \
"time_limit", else "";
- "part": for "part_moves", the part's label ("T1.P2"), else "";
- "state": for "part_moves", the part's new state (one of: done, failed, not_needed, overcome, \
no_longer_matters, settled, wont_be_given, turned_down, lent, returned, not_returned, met, \
lifted); for a new help, "lent" when it must come back, else ""; for "thread_moves", \
"happening_now", "finished" or "dropped";
- "words": a few words of what it is ("two dog sleds", "the bridge is out"; for a clue not \
shown, the clue: "the torn letter");
- "clue": for "clue_found", the clue's label ("T1.C1") when it is one shown, else "";
- "who": for "clue_found", the names of the characters who found it, as the transcript gives \
them, else [] (never a guess at its meaning, and never the DM's own characters);
- "due": for a time limit, the words of when ("before the full moon"), else "";
- "quote": the shortest exact words from the transcript that show it.
None is fine."""

_PROGRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thread": {"type": "string"},
        "change": {"type": "string", "enum": [NEW_PART, PART_MOVES, THREAD_MOVES, CLUE_FOUND]},
        "part_kind": {"type": "string", "enum": ["", *prog.PART_KINDS]},
        "part": {"type": "string"},
        "state": {"type": "string"},
        "words": {"type": "string"},
        "due": {"type": "string"},
        "clue": {"type": "string"},
        "who": {"type": "array", "items": {"type": "string"}},
        "quote": {"type": "string"},
    },
    "required": [
        "thread",
        "change",
        "part_kind",
        "part",
        "state",
        "words",
        "due",
        "clue",
        "who",
        "quote",
    ],
    "additionalProperties": False,
}


def schema(base: Mapping[str, Any]) -> dict[str, Any]:
    """The reader's own schema (`thread_read.SCHEMA`) with "progress" beside "threads"."""
    out: dict[str, Any] = json.loads(json.dumps(base))
    out["properties"]["progress"] = {"type": "array", "items": _PROGRESS_SCHEMA}
    out["required"] = [*out["required"], "progress"]
    return out


@dataclass(frozen=True, slots=True)
class KnownPart:
    id: str
    kind: str
    words: str
    state: str


@dataclass(frozen=True, slots=True)
class KnownClue:
    """A clue tied to an open thread (#4624 6c), shown to the model by label."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class KnownThread:
    """An open thread as the model is shown it: its id stays with code."""

    id: str
    name: str
    parts: tuple[KnownPart, ...] = ()
    state: str = ""  # its condition now, so "happening now" is never offered for one that is
    clues: tuple[KnownClue, ...] = ()


@dataclass(frozen=True, slots=True)
class Finders:
    """Who may have found a clue (#4624 6c): the campaign's player characters by name (`name_key`
    of each name and other name, to the entry id; a name two share is left out), and each player's
    character by their Discord id, for a clue found with no name said (the speaker found it)."""

    by_name: Mapping[str, str]
    by_player: Mapping[int, str]


@dataclass(frozen=True, slots=True)
class SaidProgress:
    thread: str
    change: str
    part_kind: str
    part: str
    state: str
    words: str
    due: str
    quote: str
    clue: str = ""
    who: tuple[str, ...] = ()


def known_block(known: Sequence[KnownThread]) -> str:
    """The <known_threads> block's lines: each thread and its standing parts by label, all as
    data (`extractor.quoted`)."""
    lines: list[str] = []
    for i, t in enumerate(known[:KNOWN_MAX], start=1):
        now = f" ({t.state})" if t.state else ""
        lines.append(f"T{i}. {quoted(t.name)}{now}")
        for j, p in enumerate(t.parts[:PARTS_SHOWN], start=1):
            lines.append(f"  T{i}.P{j} {p.kind}: {quoted(p.words)} ({p.state})")
        for j, c in enumerate(t.clues[:CLUES_SHOWN], start=1):
            lines.append(f"  T{i}.C{j} clue: {quoted(c.name)}")
    return "\n".join(lines) or "(none)"


def labels(known: Sequence[KnownThread]) -> tuple[dict[str, KnownThread], dict[str, KnownPart]]:
    """The labels `known_block` gave, back to the threads and parts they stand for."""
    threads: dict[str, KnownThread] = {}
    parts: dict[str, KnownPart] = {}
    for i, t in enumerate(known[:KNOWN_MAX], start=1):
        threads[f"T{i}"] = t
        for j, p in enumerate(t.parts[:PARTS_SHOWN], start=1):
            parts[f"T{i}.P{j}"] = p
    return threads, parts


def clue_labels(known: Sequence[KnownThread]) -> dict[str, tuple[str, KnownClue]]:
    """The clue labels `known_block` gave, back to (their thread's id, the clue)."""
    out: dict[str, tuple[str, KnownClue]] = {}
    for i, t in enumerate(known[:KNOWN_MAX], start=1):
        for j, c in enumerate(t.clues[:CLUES_SHOWN], start=1):
            out[f"T{i}.C{j}"] = (t.id, c)
    return out


def parse(text: str) -> list[SaidProgress]:
    """The AI's "progress" list; nothing for an answer that isn't the shape asked for."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    rows = data.get("progress") if isinstance(data, dict) else None
    fields = ("thread", "change", "part_kind", "part", "state", "words", "due", "quote", "clue")
    out: list[SaidProgress] = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict):
            raw_who = r.get("who")
            who = tuple(
                " ".join(str(w).split()) for w in (raw_who if isinstance(raw_who, list) else [])
                if isinstance(w, str) and w.strip()
            )  # fmt: skip
            got = {k: str(r.get(k) or "").strip() for k in fields}
            out.append(SaidProgress(**got, who=who))
    return out[:PROGRESS_MAX]


def _found_by(
    said: SaidProgress, speakers: Collection[int], finders: Finders | None
) -> tuple[str, ...] | str:
    """Who found a clue (#4624 6c, the ruled plan): the characters the line names, else the
    characters of the quoted lines' speakers; a reason when nobody is found. A name that is not a
    player character of the campaign is never kept (an NPC holding a clue has not found it, ruling
    2): with names said and none a player character, nobody found it."""
    if finders is None:
        return "no_finder"
    if said.who:
        named = [finders.by_name.get(name_key(n)) for n in said.who]
        found = tuple(dict.fromkeys(f for f in named if f))
    else:
        found = tuple(dict.fromkeys(f for u in speakers if (f := finders.by_player.get(u))))
    return found or "no_finder"


def _change(
    said: SaidProgress,
    thread: KnownThread,
    parts: Mapping[str, KnownPart],
    clues: Mapping[str, tuple[str, KnownClue]] | None = None,
    found_by: tuple[str, ...] | str = "no_finder",
) -> prog.Change | str:
    """The `thread_progress.Change` a line says, or a reason it can't be one."""
    if said.change == CLUE_FOUND:
        if isinstance(found_by, str):
            return found_by
        clue_id: str | None = None
        if said.clue:
            known = (clues or {}).get(said.clue)
            if known is None:
                return "no_clue"
            if known[0] != thread.id:
                return "other_thread"  # (a clue of another thread than the one named)
            clue_id = known[1].id
        return (
            prog.check(prog.Change(prog.CLUE, "", said.words, prog.FOUND, None, "", clue_id,
                                   found_by))
            or "not_a_clue"
        )  # fmt: skip
    if said.change == NEW_PART:
        return (
            prog.check(
                prog.Change(prog.PART, said.part_kind, said.words, said.state, None, said.due)
            )
            or "not_a_part"
        )
    if said.change == PART_MOVES:
        part = parts.get(said.part)
        if part is None:
            return "no_part"
        if part.id not in {p.id for p in thread.parts}:
            return "other_thread"  # (a part of another thread than the one named)
        if said.state not in tp.STATES.get(part.kind, ()) or said.state == part.state:
            return "not_a_state"
        return (
            prog.check(prog.Change(prog.PART_STATE, "", said.words, said.state, part.id))
            or "not_a_state"
        )
    if said.change == THREAD_MOVES:
        state = THREAD_WORDS.get(said.state)
        if state is None or state == thread.state:
            return "not_a_state"
        return prog.check(prog.Change(prog.THREAD_STATE, "", said.words, state)) or "not_a_state"
    return "not_a_change"


def check(
    said: Iterable[SaidProgress],
    window: Sequence[Line],
    known: Sequence[KnownThread],
    *,
    where: Callable[[str, Sequence[Line]], tuple[int, ...]],
    seen: Collection[tuple[str, str]],
    finders: Finders | None = None,
) -> tuple[list[tuple[str, prog.Change, tuple[int, ...], tuple[int, ...]]], dict[str, int]]:
    """One window's progress lines checked by code: (thread id, change, line starts, speakers)
    for each that holds, with counts of what was dropped and why. `where` finds a quote's lines
    in the window (`session_threads.where`); `seen` holds the (thread, change key) pairs the run
    kept from earlier windows. It is only read here: the run adds a line's pair once the line
    passes the consent re-check, so a line dropped for consent never blocks the same change
    said again by someone who agrees. `finders` (#4624 6c): who may have found a clue; with none,
    no "clue found" line is kept."""
    threads, parts = labels(known)
    clues = clue_labels(known)
    by_number = {s.number: s for s in window}
    out: list[tuple[str, prog.Change, tuple[int, ...], tuple[int, ...]]] = []
    dropped: dict[str, int] = {}
    here: set[tuple[str, str]] = set()  # (this window's own doubles)

    def drop(why: str) -> None:
        dropped[why] = dropped.get(why, 0) + 1

    for s in said:
        thread = threads.get(s.thread)
        if thread is None:
            drop("no_thread")
            continue
        numbers = where(s.quote, window)
        if not numbers:
            drop("no_quote")
            continue
        speakers_here = {by_number[n].user_id for n in numbers}
        found_by = _found_by(s, speakers_here, finders) if s.change == CLUE_FOUND else ""
        change = _change(s, thread, parts, clues, found_by)
        if isinstance(change, str):
            drop(change)
            continue
        key = (thread.id, change.key(thread.id))
        if key in seen or key in here:
            drop("double")
            continue
        here.add(key)
        speakers = tuple(sorted({by_number[n].user_id for n in numbers}))
        starts = tuple(sorted({by_number[n].started_ms for n in numbers}))
        out.append((thread.id, change, starts, speakers))
    return out, dropped


__all__ = [
    "CLUE_FOUND",
    "INSTRUCTIONS",
    "Finders",
    "KnownClue",
    "KnownPart",
    "KnownThread",
    "SaidProgress",
    "check",
    "clue_labels",
    "known_block",
    "labels",
    "parse",
    "schema",
]
