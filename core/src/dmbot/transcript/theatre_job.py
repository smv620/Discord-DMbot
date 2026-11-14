"""The DM's "Make a script of this session" job (#3244, slice 5b): claim the session (only one run
at a time, a database row), make the script (`theatre_run`), show the wait on one card that keeps
itself current, and end the card with what happened. The AI, the card's edit and the consent check
are given by the caller, so the bot's button handler is one call and this is testable with
stand-ins.

Rules from Supervisor's ruling on #3244:
- A second press while a run is going changes nothing and says so.
- The card shows the stage, "n of m", how long it has run and when it last moved, and after five
  minutes without a change it says it is still working. Counts and stages only, never a word of the
  session.
- A failure keeps the old script and says so; the DM presses the button to try again.
- Consent is asked again before each scene is sent, after each answer and before saving
  (`theatre_run`). The script is for the DM only and never goes to the table's chat or players.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot import credits
from dmbot.db import Database
from dmbot.memory import theatre as th
from dmbot.transcript import theatre_run as tr
from dmbot.transcript.theatre_runs import TheatreRuns
from dmbot.waiting import WaitingMessage, message_is_gone

log = logging.getLogger(__name__)

TITLE = "📜 Making a script of this session…"
PRIVATE_NOTE = "It makes a private copy only the DMs can read."
ALREADY = "A script is already being made for this session."
FAILED = "Nothing was changed; press the button to try again."
STILL_WORKING = "Still working."
STALL_AFTER_S = 5 * 60  # no change for this long is said out loud
_STAGE = {
    tr.STAGE_READING: "Reading the session",
    tr.STAGE_SAVING: "Saving the script",
    tr.STAGE_DONE: "Done",
    tr.STAGE_STOPPED: "Stopped",
}


def estimate_calls(lines: int, scene_size: int = th.SCENE_LINES) -> int:
    """About how many AI calls a session of `lines` lines needs: one for each scene (a cut answer
    is asked again in halves, so it can be more). Shown on the button's confirm step."""
    return max(1, -(-lines // scene_size))


def estimate_line(lines: int, scene_size: int = th.SCENE_LINES) -> str:
    n = estimate_calls(lines, scene_size)
    return f"This will take about {n} AI call{'' if n == 1 else 's'}."


def credit_line(imported: bool) -> str:
    """What the script costs in AI credits, for an imported session only (#4005): its script is paid
    with credits, while a live session's is paid by the hours and says nothing about credits."""
    if not imported:
        return ""
    # The cost only: the "left this month" sentence needs the DM's own allowance, which this
    # question does not hold, so it is never built here (a made-up balance would be shown).
    est = credits.estimate("script", 1, allowance=0, used=0)
    unit = "credit" if est.credits == 1 else "credits"
    return f"About {est.credits:,} AI {unit} for the script."


def stopped_text(part: int, of: int) -> str:
    """What a failure says: where it stopped (when it got anywhere) and what to do next."""
    where = f"Stopped at part {part} of {of}. " if of else ""
    return f"{where}{FAILED}"


def done_text(report: tr.Report, link: str = "") -> str:
    """What the card ends on after a run that reached the end. Counts only. `link`: the session's
    page on the website, where a game master reads and saves it (empty when there is no site)."""
    if report.saved:
        scenes = f"{report.parts} scene{'' if report.parts == 1 else 's'}"
        where = f"\nRead or save it here: {link}" if link else "\nIt is saved with the session."
        return f"Script ready: {report.kept} lines from {scenes}. Only the DMs can read it.{where}"
    if report.parts == 0 or report.lines == 0:
        return "There was nothing to put in a script. Nothing was changed."
    return "No lines were kept. Nothing was changed."


class RunTakenOver(Exception):
    """The run's row is another run's now (it looked dead and was taken over): stop spending."""


@dataclass(frozen=True, slots=True)
class Outcome:
    text: str  # the last word on the card
    started: bool  # False: another run was going
    ok: bool = False
    taken_over: bool = False  # another run owns the card now: say nothing more on it


async def make_script(
    db: Database,
    ask: tr.Ask,
    guild_id: int,
    session_id: str,
    *,
    agreed: Collection[int],
    names: Mapping[int, str],
    known: Sequence[str],
    still_agreed: Callable[[], Awaitable[Collection[int]]],
    edit: Callable[[str], Awaitable[None]],
    link: str = "",
    clock: Callable[[], float] = time.monotonic,
    scene_size: int = th.SCENE_LINES,
    every: float | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> Outcome:
    """Make the session's script and end the card. Never raises for a failed run: the outcome's
    text says what happened. `edit` shows a text on the DM's card; `every` and `sleep` are for
    tests (the waiting message's own refresh)."""
    runs = TheatreRuns(db)
    token = await runs.begin(guild_id, session_id)
    if token is None:
        return Outcome(ALREADY, started=False)
    extra: dict[str, Any] = {}
    if every is not None:
        extra["every"] = every
    if sleep is not None:
        extra["sleep"] = sleep
    card = WaitingMessage(
        edit,
        TITLE,
        stall_after_s=STALL_AFTER_S,
        stall_note=STILL_WORKING,
        gone=message_is_gone,
        clock=clock,
        **extra,
    )
    lost: list[bool] = []
    where = [0, 0]  # the part the run last got to, for a failure to say

    async def progress(p: tr.Progress) -> None:
        card.step(_STAGE.get(p.stage, p.stage), p.part, p.of)
        where[:] = [p.part, p.of]
        if not await runs.moved(guild_id, session_id, token, p.part, p.of):
            lost.append(True)

    async def consent() -> Collection[int]:
        # `theatre_run` asks before each scene, after each answer and before saving, so this is
        # where a run that lost its row stops: no more spend and no save over the new run's script.
        if lost:
            raise RunTakenOver
        return await still_agreed()

    ok = False
    text = FAILED
    try:
        async with card:
            report = await tr.run(
                db, ask, guild_id, session_id, agreed=agreed, names=names, known=known,
                still_agreed=consent, progress=progress, clock=clock, scene_size=scene_size,
            )  # fmt: skip
        ok = not report.failed_parts
        text = done_text(report, link) if ok else stopped_text(*where)
    except RunTakenOver:
        return Outcome("", started=True, taken_over=True)
    except asyncio.CancelledError:
        # (the bot is stopping: free the session at once, not after the stale wait)
        await asyncio.shield(_finish(runs, guild_id, session_id, token, ok=False))
        raise
    except Exception as exc:  # (the type only: a message could carry words of the session)
        log.warning("A script run failed (%s)", type(exc).__name__)
        text = stopped_text(*where)
    await _finish(runs, guild_id, session_id, token, ok=ok)
    try:
        await edit(text)
    except Exception as exc:
        if not message_is_gone(exc):
            log.warning("Could not end the script card (%s)", type(exc).__name__)
    return Outcome(text, started=True, ok=ok)


async def _finish(
    runs: TheatreRuns, guild_id: int, session_id: str, token: str, *, ok: bool
) -> None:
    try:
        await runs.finish(guild_id, session_id, token, ok=ok)
    except Exception as exc:  # (the card still ends; the stale wait frees the session)
        log.warning("Could not end the script run (%s)", type(exc).__name__)
