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

import logging
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.memory import theatre as th
from dmbot.transcript import theatre_run as tr
from dmbot.transcript.theatre_runs import TheatreRuns
from dmbot.waiting import WaitingMessage, message_is_gone

log = logging.getLogger(__name__)

TITLE = "📜 Making a script of this session…"
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


def done_text(report: tr.Report) -> str:
    """What the card ends on after a run that reached the end. Counts only."""
    if report.saved:
        return f"Script ready: {report.kept} lines from {report.parts} parts. Only you can read it."
    if report.parts == 0 or report.lines == 0:
        return "There was nothing to put in a script. Nothing was changed."
    return "No lines were kept. Nothing was changed."


@dataclass(frozen=True, slots=True)
class Outcome:
    text: str  # the last word on the card
    started: bool  # False: another run was going
    ok: bool = False


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
    clock: Callable[[], float] = time.monotonic,
    scene_size: int = th.SCENE_LINES,
    **waiting: Any,
) -> Outcome:
    """Make the session's script and end the card. Never raises for a failed run: the outcome's
    text says what happened. `edit` shows a text on the DM's card; `waiting` is for tests (the
    waiting message's own clock and sleep)."""
    runs = TheatreRuns(db)
    token = await runs.begin(guild_id, session_id)
    if token is None:
        return Outcome(ALREADY, started=False)
    card = WaitingMessage(
        edit,
        TITLE,
        stall_after_s=STALL_AFTER_S,
        stall_note=STILL_WORKING,
        gone=message_is_gone,
        clock=clock,
        **waiting,
    )

    async def progress(p: tr.Progress) -> None:
        card.step(_STAGE.get(p.stage, p.stage), p.part, p.of)
        if not await runs.moved(guild_id, session_id, token, p.part, p.of):
            lost.append(True)  # (`theatre_run` counts a failing callback and goes on, so note it)

    lost: list[bool] = []
    ok = False
    text = FAILED
    try:
        async with card:
            report = await tr.run(
                db, ask, guild_id, session_id, agreed=agreed, names=names, known=known,
                still_agreed=still_agreed, progress=progress, clock=clock, scene_size=scene_size,
            )  # fmt: skip
        ok = not report.failed_parts and not lost
        text = done_text(report) if ok else FAILED
    except Exception:  # (counts only: nothing from the session goes in the log)
        log.warning("A script run failed", exc_info=False)
    await runs.finish(guild_id, session_id, token, ok=ok)
    try:
        await edit(text)
    except Exception as exc:
        if not message_is_gone(exc):
            log.warning("Could not end the script card")
    return Outcome(text, started=True, ok=ok)
