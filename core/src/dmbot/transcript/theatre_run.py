"""Making one session's theatrical transcript (#3244, slice 3): read the cleaned lines of people
who agree, cut them into scenes, ask the AI for each scene's script, check every answer
(`memory.theatre`), and keep the result (`theatre_store`). The AI is `ask`, given by the caller
(the bot's own client, or dev1's tool), so this is testable with a stand-in.

It is a DM-only working copy and always optional: nothing reads it but the DM's page and download,
and a session with no script is exactly as it was. The caller starts it only with the switch on
(`THEATRE_ON`, off by default).

- **Party knowledge only.** `known` is the list of names and things the *table* knows; the caller
  builds it from the party's view (`gate.party_view`, names through `gate.display_name`). The AI
  sees nothing else, and every bracket it writes is checked against it afterwards.
- **Consent twice.** Scenes are cut from the lines of people in `agreed`; before saving,
  `still_agreed()` is asked again and any script line that holds the words of someone who has
  since stopped is left out. The store re-checks that its source lines still exist.
- **A cut answer is retried in smaller parts**, never taken as "nothing found". If any part still
  cannot be read, the script already kept is left alone and the report says so: a half script
  never replaces a whole one.
- **The waiting rule.** After every part `progress` is told the stage, part n of m, how long it has
  been running and when it last moved: counts and stages, never a word of a transcript.
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
from dmbot.memory import theatre as th
from dmbot.transcript import theatre_store as ts

log = logging.getLogger(__name__)

Ask = Callable[[str, str], Awaitable[Any]]  # (instructions, scene) -> reply with .text and .cut
Clock = Callable[[], float]
MAX_SPLITS = 3  # a scene that is cut off is halved up to this many times, then reported
STAGE_READING = "reading"
STAGE_SAVING = "saving"
STAGE_DONE = "done"


@dataclass(frozen=True, slots=True)
class Progress:
    """What the DM is shown while it runs: counts and a stage."""

    stage: str
    part: int
    of: int
    elapsed: float  # seconds since the run began
    moved_at: float  # the clock's reading when it last moved forward (the caller shows its age)


@dataclass(slots=True)
class Report:
    """What a run did, as counts only."""

    lines: int = 0
    parts: int = 0
    failed_parts: int = 0
    kept: int = 0
    dropped: int = 0
    too_big: int = 0
    saved: bool = False
    counts: Counter[str] = field(default_factory=Counter)


async def read_scene(
    ask: Ask, scene: th.Scene, known: Sequence[str], counts: Counter[str], depth: int = 0
) -> list[th.Recast] | None:
    """The checked script lines of one scene, or None when it cannot be read. A reply that was
    cut off, or that said nothing readable for a scene of several lines, is retried with the scene
    halved (the second half reads the end of the first as its context)."""
    try:
        reply = await ask(th.instructions(known), th.render(scene))
    except Exception:  # (the client's own errors: out of funds, timeouts: the part is just unread)
        log.warning("theatre: a scene's call failed")
        counts["call_failed"] += 1
        return None
    text = str(getattr(reply, "text", reply) or "")
    cut = bool(getattr(reply, "cut", False))
    if not cut:
        mine = Counter[str]()
        rows = th.parse(text, scene, known, mine)
        if rows or len(scene.lines) < 2:  # (a one-line scene may truly have nothing to say)
            counts.update(mine)
            return rows
    if depth >= MAX_SPLITS or len(scene.lines) < 2:
        counts["stopped_early"] += 1
        return None
    half = len(scene.lines) // 2
    first = th.Scene(scene.context, scene.lines[:half])
    context = (*scene.context, *scene.lines[:half])[-th.OVERLAP_LINES :]
    second = th.Scene(tuple(context), scene.lines[half:])
    counts["split"] += 1
    out: list[th.Recast] = []
    for part in (first, second):
        got = await read_scene(ask, part, known, counts, depth + 1)
        if got is None:
            return None
        out.extend(got)
    return out


def rows_of(recasts: Sequence[th.Recast], said: Mapping[int, th.Said]) -> list[ts.Row]:
    """The store's rows: each line's speaker as the user id of the source line he is named in."""
    rows: list[ts.Row] = []
    for r in recasts:
        who = next(
            (
                said[n].user_id
                for n in r.sources
                if n in said and said[n].speaker.casefold() == r.speaker.casefold()
            ),
            None,
        )
        if who is not None:
            rows.append(ts.Row(who, r.text, tuple(r.sources)))
    return rows


async def run(
    db: Database,
    ask: Ask,
    guild_id: int,
    session_id: str,
    *,
    agreed: Collection[int],
    names: Mapping[int, str],
    known: Sequence[str],
    still_agreed: Callable[[], Awaitable[Collection[int]]],
    progress: Callable[[Progress], Awaitable[None]] | None = None,
    clock: Clock = time.monotonic,
) -> Report:
    """Make and keep the session's script. See the module docstring for what it promises."""
    started = clock()
    report = Report()
    raw = await ts.script_input(db, guild_id, session_id, agreed)
    said = [
        th.Said(line_id, user_id, names.get(user_id) or f"Player {user_id}", text)
        for line_id, user_id, text in raw
    ]
    report.lines = len(said)
    scenes = th.scenes(said, agreed)
    report.parts = len(scenes)
    by_number = {x.number: x for x in said}
    moved = started

    async def tell(stage: str, part: int) -> None:
        nonlocal moved
        moved = clock()
        if progress is not None:
            await progress(Progress(stage, part, len(scenes), moved - started, moved))

    recasts: list[th.Recast] = []
    for n, scene in enumerate(scenes, 1):
        got = await read_scene(ask, scene, known, report.counts)
        if got is None:
            report.failed_parts += 1
        else:
            recasts.extend(got)
        await tell(STAGE_READING, n)
    if report.failed_parts or not recasts:
        await tell(STAGE_DONE, len(scenes))
        return report  # a half script never replaces a whole one
    await tell(STAGE_SAVING, len(scenes))
    now_agreed = set(await still_agreed())
    rows = [
        r
        for r in rows_of(recasts, by_number)
        if all(by_number[n].user_id in now_agreed for n in r.source_ids)
    ]
    report.counts["left_out_no_longer_agreed"] = len(recasts) - len(rows)
    saved = await ts.TheatreStore(db).replace(guild_id, session_id, rows)
    report.kept, report.dropped, report.too_big = saved.kept, saved.dropped, saved.too_big
    report.saved = saved.kept > 0
    await tell(STAGE_DONE, len(scenes))
    return report
