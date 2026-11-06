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
  `still_agreed()` is asked again and any scene that held a person who has since stopped (as a
  line or as context) is left out whole, because the AI saw their words. The store re-checks that
  the source lines still exist, and its triggers remove rows when lines go.
- **No id ever goes to the AI**: speakers are labelled by their display name, or "Speaker 2".
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
STAGE_STOPPED = "stopped"  # a part could not be read: nothing was kept, the old script stands


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
    ask: Ask,
    scene: th.Scene,
    known: Sequence[str],
    counts: Counter[str],
    depth: int = 0,
    again: bool = True,
) -> list[th.Recast] | None:
    """The checked script lines of one scene, or None when it cannot be read.

    A blank answer means the scene had nothing to say (fillers): an empty success. A cut-off
    answer, or one that is not blank but cannot be read, is retried with the scene halved (the
    second half reads the end of the first as its context). A single line that still cannot be read
    is asked once more and then counted (`lost_line`) and left out: one line never fails the run."""
    instructions, question = th.instructions(known), th.render(scene)
    try:
        reply = await ask(instructions, question)
    except Exception as exc:  # (the client's own errors: out of funds, timeouts)
        log.warning("theatre: a scene's call failed (%s)", type(exc).__name__)
        counts["call_failed"] += 1
        return None
    text = str(getattr(reply, "text", reply) or "")
    if not bool(getattr(reply, "cut", False)):
        if not text.strip():
            return []
        mine = Counter[str]()
        rows = th.parse(text, scene, known, mine)
        if rows:
            counts.update(mine)
            return rows
        if len(scene.lines) < 2:
            if again:
                return await read_scene(ask, scene, known, counts, depth, again=False)
            counts["lost_line"] += 1
            return []
    if depth >= MAX_SPLITS or len(scene.lines) < 2:
        counts["stopped_early"] += 1
        return None
    half = len(scene.lines) // 2
    first = th.Scene(scene.context, scene.lines[:half])
    before = (*scene.context, *scene.lines[:half])
    second = th.Scene(
        before[-th.OVERLAP_LINES :] if th.OVERLAP_LINES > 0 else (), scene.lines[half:]
    )
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
            text = r.text
            if all(said[n].typed for n in r.sources) and not text.startswith("(typed)"):
                text = f"(typed) {text}"  # typed in the chat, not said: the DM sees which
            rows.append(ts.Row(who, text, tuple(r.sources)))
    return rows


def speaker_labels(
    raw: Sequence[tuple[int, int, str, bool]], names: Mapping[int, str]
) -> dict[int, str]:
    """A distinct label for each speaker, in the order they first speak: their display name, or
    "Speaker 2" when there is none (never a Discord id), and "Alex 2" for a second person with the
    same name, so a script line can always be traced to one person."""
    labels: dict[int, str] = {}
    used: set[str] = set()
    for _, user_id, _, _ in raw:
        if user_id in labels:
            continue
        base = " ".join((names.get(user_id) or "").split()) or f"Speaker {len(labels) + 1}"
        label, n = base, 1
        while label.casefold() in used:
            n += 1
            label = f"{base} {n}"
        labels[user_id] = label
        used.add(label.casefold())
    return labels


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
    scene_size: int = th.SCENE_LINES,
) -> Report:
    """Make and keep the session's script. See the module docstring for what it promises.

    `known` is the table's list (never the DM's hidden names); the speakers' own labels are added
    to what the AI may bracket. `still_agreed`, the script input and the save raise to the caller;
    a failing `progress` callback is counted and the run goes on."""
    started = clock()
    report = Report()
    raw = await ts.script_input(db, guild_id, session_id, agreed)
    labels = speaker_labels(raw, names)
    said = [th.Said(i, u, labels[u], text, typed) for i, u, text, typed in raw]
    report.lines = len(said)
    scenes = th.scenes(said, agreed, size=scene_size)
    report.parts = len(scenes)
    by_number = {x.number: x for x in said}
    table = [*known, *(x for x in labels.values() if x not in known)]

    async def tell(stage: str, part: int) -> None:
        if progress is None:
            return
        now = clock()
        try:
            await progress(Progress(stage, part, len(scenes), now - started, now))
        except Exception:  # (a failed progress edit must not throw away the spend)
            report.counts["progress_failed"] += 1

    await tell(STAGE_READING, 0)
    parts: list[tuple[list[th.Recast], set[int]]] = []  # (script lines, everyone the scene held)
    for n, scene in enumerate(scenes, 1):
        people = {x.user_id for x in (*scene.context, *scene.lines)}
        # Consent is asked again before a scene is sent and after the AI answered (Supervisor,
        # #3244): a person who stopped while an earlier scene was away is never sent, and one who
        # stopped during this call has the whole scene left out.
        if people & (set(agreed) - set(await still_agreed())):
            report.counts["scene_left_out_no_longer_agreed"] += 1
            await tell(STAGE_READING, n)
            continue
        got = await read_scene(ask, scene, table, report.counts)
        if got is None:
            report.failed_parts += 1
            break  # the result can never be saved: no more spend
        if people & (set(agreed) - set(await still_agreed())):
            report.counts["scene_left_out_no_longer_agreed"] += 1
            await tell(STAGE_READING, n)
            continue
        parts.append((got, people))
        await tell(STAGE_READING, n)
    if report.failed_parts or not any(got for got, _ in parts):
        await tell(STAGE_STOPPED if report.failed_parts else STAGE_DONE, len(parts))
        return report  # a half script never replaces a whole one
    await tell(STAGE_SAVING, len(scenes))
    stopped = set(agreed) - set(await still_agreed())
    recasts: list[th.Recast] = []
    for got, people in parts:
        if people & stopped:
            # A scene that held a stopped person's words is left out whole: the AI saw them as
            # context and may have woven them into any line, whatever the line cites.
            report.counts["scene_left_out_no_longer_agreed"] += 1
            continue
        recasts.extend(got)
    rows = sorted(rows_of(recasts, by_number), key=lambda r: min(r.source_ids))
    saved = await ts.TheatreStore(db).replace(guild_id, session_id, rows)
    report.kept, report.dropped, report.too_big = saved.kept, saved.dropped, saved.too_big
    report.saved = saved.kept > 0
    await tell(STAGE_DONE, len(scenes))
    return report
