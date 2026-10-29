"""How far a recap being made has got, in the one shape every website wait uses (#2636).

The recap page's wait panel needs "part 2 of 4" and "last moved" (CLAUDE.md: every wait shows that
DMbot is still working). Everything here is worked out from the job row the pipeline already
saves after each step and each part, so the worker has nothing more to remember: `updated_at` is
the last time any step moved, `window_done` of `window_count` are the parts of the transcript
read. Stage codes, counts and times only: never a word of a transcript or a recap, and a failure
is a fixed code (`REASONS`), not the stored sentence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from dmbot import progress
from dmbot.recaps import critics
from dmbot.recaps import pipeline as pl

# The pipeline's own steps (docs in `recaps.pipeline`), as the stage codes the page puts into words.
NAMES = "recap_names"  # waiting for PlotBot's name pass on the transcript (#2628)
READING = "recap_reading"  # the AI reads the session, a part at a time (done of total parts)
BEATS = "recap_beats"  # the beats it found are checked against the transcript
WRITING = "recap_writing"  # the recap is written from the beats
CHECKING = "recap_checking"  # the critics read the draft
CHECK_WAITING = "recap_check_waiting"  # written, and waiting to be checked: tried again later
FINAL_READ = "recap_final_read"  # the last editing pass over the finished draft (#2952)
FINISHING = "recap_finishing"  # the title and the housekeeping notes
READY = "recap_ready"
FAILED = "recap_failed"

# What a failed recap says, as codes the page puts into words (the stored sentences are the
# pipeline's own words for the DM; a code is all this shape carries).
CHECK_LATE_S = 30 * 60  # past its time to be tried again by this long, a check wait is stalled

REASONS: Final[Mapping[str, str]] = {
    pl.NOTHING: "nothing_said",
    pl.TOO_LITTLE: "too_little",
    pl.STOPPED: "nothing_said",
    pl.CAP: "limit",
    pl.DAILY: "daily_limit",
    pl.SERVICE: "service",
    pl.FUNDS: "service",
    pl.CUT_OFF: "cut_off",
    pl.FAILED_CHECKS: "checks",
    pl.NO_NAMES: "names",
    pl.NO_COUNT: "allowance",
}


def stage_of(row: Mapping[str, Any], *, now: int) -> str:
    """Which step of the pipeline this job is in. The states are the pipeline's own (`queued`,
    `beats`, `checked`, `prose`, `housekeeping`, then `ready` or `failed`)."""
    state = row["state"]
    if state == "failed":
        return FAILED
    if state == "ready":
        return READY
    if state == "queued":
        if int(row["window_count"]) > 0:
            return READING  # between two parts of a long session
        # The wait for the name pass (#2628) says so in the job itself, and the note goes when the
        # wait is over, so this is the pipeline's own marker and not a guess from other columns.
        if row["last_error"] == pl.CHECKING_NAMES:
            return NAMES
        return READING
    if state == "beats":
        return BEATS
    if state == "checked":
        return WRITING
    if state == "prose":
        if row["last_error"] == critics.CHECK_WAIT:
            return CHECK_WAITING
        loop = critics.loop_from_json(row.get("critics"))
        if loop.done and loop.final == "reading":
            return FINAL_READ
        return FINISHING if loop.done else CHECKING
    return FINISHING


def reason_of(row: Mapping[str, Any]) -> str | None:
    """The code for why a recap failed; any sentence not in the table is a plain `recap_failed`."""
    if row["state"] != "failed":
        return None
    return REASONS.get(str(row["last_error"]), progress.RECAP_FAILED_REASON)


def of(row: Mapping[str, Any], *, now: int) -> dict[str, Any] | None:
    """The progress object for the recap view: None once the recap is posted or dropped."""
    if row["status"] != "draft":
        return None
    stage = stage_of(row, now=now)
    reading = stage == READING and int(row["window_count"]) > 0
    shaped = progress.shape(
        stage,
        started_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]) or int(row["created_at"]),
        now=now,
        # The parts read so far; the one being read counts when it is done, so a job that has
        # just started shows 0 of N, and the last part's end moves the stage on.
        done=int(row["window_done"]) if reading else None,
        total=int(row["window_count"]) if reading else None,
        unit="parts" if reading else None,
        reason=reason_of(row),
    )
    # A written recap waits hours for its check by design, so it is never stalled by the usual
    # clock; it is stalled only when the time it was to be tried again has long passed (the worker
    # is not running it) (#2639 review).
    due = int(row["next_at"] or 0)
    if stage == CHECK_WAITING and due and now > due + CHECK_LATE_S:
        shaped["state"] = progress.STALLED
        shaped["reason"] = progress.STALLED_REASON
    return shaped
