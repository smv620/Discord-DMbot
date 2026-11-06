"""Reading one session's relationships from its stored script (#3244 slice 6, stage 1 of #3242):
cut the script into windows, ask the reader (`memory.rel_read`) for one row per tie, check every
row, choose each tie's type by meaning (`memory.rel_types`) and keep the result in `rel_ties`
(`rel_store`) for dev1's comparison against the old reader. The AI and the embedder are given by
the caller, so this is testable with stand-ins.

Nothing the DM sees changes and nothing reads the result but the comparison. The caller starts it
only with the switch on (`DMBOT_REL_READ_ON`, off by default; dev1 turns it on on the test server
only). Storing real connections from these ties is stage 2, a separate decision.

- **Party knowledge only.** `known` is the table's list of names (never the DM's hidden ones); a
  row naming anyone else is dropped by `rel_read.parse`.
- **Consent three times.** A window is sent only if everyone in it still agrees (asked before the
  call and again after the answer); before saving, `still_agreed()` is asked once more, a window
  holding a person who stopped is left out whole (the AI saw their words), and the store re-checks
  the source lines and speakers inside the saving transaction.
- **No id ever goes to the AI**: speakers are labelled by display name or "Speaker N".
- **A cut answer is retried in halves**, never taken as "nothing found". If a window still cannot
  be read the ties already kept are left alone and the report says so: a half result never
  replaces a whole one.
- **A failed embedding is not a failed run**: the reader's own type stands (`embed_failed`).
- **The waiting rule.** After every window `progress` is told the stage, window n of m, how long
  it has run and when it last moved: counts and stages, never a word.
Counts only leave this module, and nothing here logs a word of the session.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from dmbot.db import Database
from dmbot.memory import rel_read, rel_types
from dmbot.memory.rel_type_texts import TYPES
from dmbot.transcript import rel_store, theatre_run, theatre_store

log = logging.getLogger(__name__)

Ask = Callable[[str, str], Awaitable[Any]]  # (instructions, script window) -> reply: .text, .cut
Embed = Callable[[Sequence[str], bool], Awaitable[list[list[float]]]]  # (texts, as_query)
Clock = Callable[[], float]
Progress = theatre_run.Progress
WINDOW_LINES = 120  # script lines for one call: a starting size, set by dev1's measured run
MAX_SPLITS = 3
STAGE_READING = theatre_run.STAGE_READING
STAGE_SAVING = theatre_run.STAGE_SAVING
STAGE_DONE = theatre_run.STAGE_DONE
STAGE_STOPPED = theatre_run.STAGE_STOPPED


@dataclass(slots=True)
class Report:
    """What a run did, as counts only."""

    lines: int = 0
    windows: int = 0
    failed_windows: int = 0
    ties: int = 0  # read and checked
    kept: int = 0
    dropped: int = 0
    saved: bool = False
    counts: Counter[str] = field(default_factory=Counter)


def render(rows: Sequence[theatre_store.Stored], labels: Mapping[int, str]) -> str:
    """`number | speaker: words`, numbered by script position + 1."""
    return "\n".join(f"{r.position + 1} | {labels[r.user_id]}: {r.text}" for r in rows)


async def read_window(
    ask: Ask,
    rows: Sequence[theatre_store.Stored],
    labels: Mapping[int, str],
    known: Sequence[str],
    counts: Counter[str],
    depth: int = 0,
) -> list[rel_read.Tie] | None:
    """The checked ties of one window, or None when it cannot be read. A blank answer is an empty
    success (no ties there). A cut or unreadable answer is retried with the window halved."""
    sources = {r.position + 1 for r in rows}
    try:
        reply = await ask(rel_read.instructions(known), render(rows, labels))
    except Exception as exc:  # (the client's own errors: out of funds, timeouts)
        log.warning("relationships: a window's call failed (%s)", type(exc).__name__)
        counts["call_failed"] += 1
        return None
    text = str(getattr(reply, "text", reply) or "")
    if not bool(getattr(reply, "cut", False)):
        if not text.strip():
            return []
        mine = Counter[str]()
        ties = rel_read.parse(text, sources, known, mine)
        counts.update(mine)
        if not ties and mine["unreadable"] >= len([x for x in text.splitlines() if x.strip()]):
            counts["unreadable_answer"] += 1  # (chatter such as "no ties here": nothing found)
        return ties
    if depth >= MAX_SPLITS or len(rows) < 2:
        counts["stopped_early"] += 1
        return None
    half = len(rows) // 2
    counts["split"] += 1
    out: list[rel_read.Tie] = []
    for part in (rows[:half], rows[half:]):
        got = await read_window(ask, part, labels, known, counts, depth + 1)
        if got is None:
            return None
        out.extend(got)
    return out


async def decide_kinds(
    embed: Embed, ties: Sequence[rel_read.Tie], counts: Counter[str]
) -> list[rel_types.Decision]:
    """The type for each tie: the closest type by meaning when it is close enough and clearly
    ahead, otherwise the reader's own (`rel_types.decide`). Any trouble with the embedder leaves
    every tie on the reader's own type."""
    if not ties:
        return []
    try:
        type_vectors = await embed([rel_types.embed_text(t) for t in TYPES], False)
        tie_vectors = await embed([t.sentence for t in ties], True)
        if len(type_vectors) != len(TYPES) or len(tie_vectors) != len(ties):
            raise ValueError("embedding count")
        vectors = {t.key: v for t, v in zip(TYPES, type_vectors, strict=True)}
    except Exception as exc:
        log.warning("relationships: the embedding failed (%s)", type(exc).__name__)
        counts["embed_failed"] += 1
        return [rel_types.decide(t.kind, None) for t in ties]
    out = [
        rel_types.decide(t.kind, rel_types.nearest(v, vectors))
        for t, v in zip(ties, tie_vectors, strict=True)
    ]
    for d in out:
        counts[f"chosen_{d.source}"] += 1
    return out


async def run(
    db: Database,
    ask: Ask,
    embed: Embed,
    guild_id: int,
    session_id: str,
    *,
    agreed: Collection[int],
    names: Mapping[int, str],
    known: Sequence[str],
    still_agreed: Callable[[], Awaitable[Collection[int]]],
    progress: Callable[[Progress], Awaitable[None]] | None = None,
    clock: Clock = time.monotonic,
    window: int = WINDOW_LINES,
) -> Report:
    """Read and keep the session's ties. See the module docstring for what it promises."""
    started = clock()
    report = Report()
    script = await theatre_store.TheatreStore(db).read(guild_id, session_id)
    everyone = set(agreed)
    rows = [r for r in script if set(r.user_ids) <= everyone]  # (every co-author agrees)
    report.lines = len(rows)
    labels = theatre_run.speaker_labels(
        [(0, r.user_id, "", False) for r in rows], names
    )  # (display names, else "Speaker N": never an id)
    chunks = [rows[i : i + window] for i in range(0, len(rows), window)]
    report.windows = len(chunks)
    by_number = {r.position + 1: r for r in rows}

    async def tell(stage: str, part: int) -> None:
        if progress is None:
            return
        now = clock()
        try:
            await progress(Progress(stage, part, len(chunks), now - started, now))
        except Exception:  # (a failed progress edit must not throw away the spend)
            report.counts["progress_failed"] += 1

    def people_of(chunk: Sequence[theatre_store.Stored]) -> set[int]:
        return {u for r in chunk for u in r.user_ids}

    await tell(STAGE_READING, 0)
    parts: list[tuple[list[rel_read.Tie], set[int]]] = []
    for n, chunk in enumerate(chunks, 1):
        people = people_of(chunk)
        if people & (everyone - set(await still_agreed())):
            report.counts["window_left_out_no_longer_agreed"] += 1
            await tell(STAGE_READING, n)
            continue
        got = await read_window(ask, chunk, labels, known, report.counts)
        if got is None:
            report.failed_windows += 1
            break  # the result can never be saved: no more spend
        if people & (everyone - set(await still_agreed())):
            report.counts["window_left_out_no_longer_agreed"] += 1
            await tell(STAGE_READING, n)
            continue
        parts.append((got, people))
        await tell(STAGE_READING, n)
    if report.failed_windows or not any(got for got, _ in parts):
        await tell(STAGE_STOPPED if report.failed_windows else STAGE_DONE, len(parts))
        return report  # a half result never replaces a whole one
    await tell(STAGE_SAVING, len(chunks))
    fresh = set(await still_agreed())
    stopped = everyone - fresh
    ties: list[rel_read.Tie] = []
    for got, people in parts:
        if people & stopped:
            report.counts["window_left_out_no_longer_agreed"] += 1
            continue  # the AI saw a stopped person's words: nothing from this window is kept
        ties.extend(got)
    report.ties = len(ties)
    decisions = await decide_kinds(embed, ties, report.counts)
    keep: list[rel_store.TieRow] = []
    for tie, d in zip(ties, decisions, strict=True):
        if not d.stored:
            report.counts["form_of_address_not_stored"] += 1
            continue
        sources = tuple(sorted({s for n in tie.sources for s in by_number[n].source_ids}))
        keep.append(
            rel_store.TieRow(
                tie.a, tie.b, d.kind, tie.kind, d.source, tie.word, tie.sentence, sources
            )
        )
    saved = await rel_store.RelStore(db).replace(guild_id, session_id, keep, fresh)
    report.kept, report.dropped = saved.kept, saved.dropped
    report.saved = saved.kept > 0
    await tell(STAGE_DONE, len(chunks))
    return report
